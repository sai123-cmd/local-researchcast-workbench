from __future__ import annotations

import sqlite3
from typing import Any

from ..db import json_loads, row_to_dict, rows_to_dicts
from .task_context import _fallback_events, _int_list, _keywords, _next_questions


def get_knowledge_context(conn: sqlite3.Connection, knowledge_id: int) -> dict[str, Any] | None:
    item_row = conn.execute("SELECT * FROM knowledge_items WHERE id=?", (knowledge_id,)).fetchone()
    if not item_row:
        return None

    item = row_to_dict(item_row) or {}
    source_ids = _int_list(item.get("source_event_ids"))
    source_events = _source_events(conn, source_ids)
    if not source_events:
        source_events = _fallback_events(
            conn,
            {
                "title": item.get("title", ""),
                "summary": item.get("summary", ""),
                "next_action": item.get("work_relevance", ""),
            },
        )

    related_tasks = _related_tasks(conn, source_ids, item)
    combined = "\n".join(
        [
            item.get("title", ""),
            item.get("domain", ""),
            item.get("summary", ""),
            item.get("work_relevance", ""),
            "\n".join([event.get("body", "") for event in source_events]),
        ]
    )
    supplier_questions = _supplier_questions(item, combined)
    return {
        "item": item,
        "source_events": source_events,
        "related_tasks": related_tasks,
        "glossary": _glossary(item.get("domain", ""), combined),
        "learning_goals": _learning_goals(item, combined),
        "supplier_questions": supplier_questions,
        "podcast_outline": _podcast_outline(item, supplier_questions),
        "study_note": _study_note(item, combined),
        "confidence": "source_linked" if source_ids else "fallback_search",
    }


def _source_events(conn: sqlite3.Connection, source_ids: list[int]) -> list[dict[str, Any]]:
    if not source_ids:
        return []
    source_ids = source_ids[:50]
    placeholders = ",".join("?" for _ in source_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM raw_events
        WHERE id IN ({placeholders})
        ORDER BY COALESCE(happened_at, received_at) DESC
        """,
        source_ids,
    ).fetchall()
    return rows_to_dicts(rows)


def _related_tasks(conn: sqlite3.Connection, source_ids: list[int], item: dict[str, Any]) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM tasks ORDER BY updated_at DESC LIMIT 200").fetchall()
    source_set = set(source_ids)
    keywords = set(_keywords(" ".join([item.get("title", ""), item.get("domain", ""), item.get("summary", "")])))
    matches: list[dict[str, Any]] = []
    for row in rows:
        task = row_to_dict(row) or {}
        task_sources = set(_int_list(json_loads(row["source_event_ids_json"], [])))
        text = " ".join([task.get("title", ""), task.get("summary", ""), task.get("next_action", "")])
        if (source_set and source_set.intersection(task_sources)) or any(keyword in text for keyword in keywords):
            matches.append(task)
        if len(matches) >= 5:
            break
    return matches


def _glossary(domain: str, combined: str) -> list[dict[str, str]]:
    text = combined.lower()
    if "电池" in domain or "电池" in combined or "mah" in text or "锂" in combined:
        return [
            {"term": "mAh", "meaning": "容量单位，用来估算续航，但还要结合功耗曲线和放电倍率。"},
            {"term": "保护板/PCM", "meaning": "电芯保护电路，负责过充、过放、短路等安全边界。"},
            {"term": "UN38.3/MSDS", "meaning": "运输和安全资料，影响样品寄送、出口和量产合规。"},
            {"term": "厚度公差", "meaning": "电池厚度浮动会直接影响结构堆叠、装配压力和可靠性。"},
        ]
    if "摄像" in domain or "摄像" in combined or "sensor" in text or "模组" in combined:
        return [
            {"term": "Sensor", "meaning": "图像传感器型号，决定分辨率、低照能力、成本和驱动适配。"},
            {"term": "FOV", "meaning": "视场角，影响宠物第一视角覆盖范围和画面畸变。"},
            {"term": "MIPI/USB", "meaning": "常见摄像头接口，关系到主控选型、驱动和功耗。"},
            {"term": "ISP", "meaning": "图像处理链路，影响曝光、降噪、色彩和低照表现。"},
        ]
    if "通信" in domain or "定位" in domain or "gnss" in text or "蓝牙" in combined:
        return [
            {"term": "GNSS", "meaning": "卫星定位能力，影响户外轨迹、搜寻和功耗策略。"},
            {"term": "BLE", "meaning": "低功耗蓝牙，常用于近距离连接、配网和基础控制。"},
            {"term": "eSIM/蜂窝", "meaning": "远距离联网方案，会带来资费、认证、天线和功耗问题。"},
            {"term": "天线净空", "meaning": "结构中留给天线工作的空间，直接影响信号稳定性。"},
        ]
    if "认证" in domain or "认证" in combined or "fcc" in text or "ce" in text:
        return [
            {"term": "CE/FCC", "meaning": "欧盟/美国常见准入认证，影响目标市场和送检计划。"},
            {"term": "SRRC", "meaning": "中国无线电型号核准，涉及无线发射模块。"},
            {"term": "RoHS/REACH", "meaning": "材料环保合规要求，常在量产和渠道准入阶段需要。"},
            {"term": "整改周期", "meaning": "测试失败后的结构、射频或材料调整时间，是排期风险。"},
        ]
    return [
        {"term": "关键约束", "meaning": "决定这条信息是否会影响 当前项目 打样、融资、内容 demo 或排期。"},
        {"term": "供应商追问", "meaning": "把不确定点变成对方能明确回答的问题，减少来回沟通。"},
        {"term": "证据链", "meaning": "保留原文、文件和时间，方便复盘为什么做出某个判断。"},
    ]


def _learning_goals(item: dict[str, Any], combined: str) -> list[str]:
    domain = item.get("domain") or "这个领域"
    return [
        f"先弄清楚「{domain}」里哪些参数会影响 当前项目关键路径。",
        f"把这条信息转成可执行判断：{item.get('work_relevance') or '它对当前项目的影响是什么。'}",
        "整理一轮供应商/同事能直接回答的追问，避免只停留在“我需要学习”。",
        "如果它影响硬件打样、认证或报价，把对应任务拉进今日作战清单。",
    ]


def _supplier_questions(item: dict[str, Any], combined: str) -> list[str]:
    questions: list[str] = []
    for value in (item.get("key_questions") or []) + (item.get("followups") or []):
        if value and value not in questions:
            questions.append(str(value))
    for value in _next_questions(combined):
        if value not in questions:
            questions.append(value)
    return questions[:8]


def _podcast_outline(item: dict[str, Any], supplier_questions: list[str]) -> list[str]:
    title = item.get("title") or "这条新知识"
    relevance = item.get("work_relevance") or "它可能影响 当前项目的产品判断。"
    first_question = supplier_questions[0] if supplier_questions else "下一轮先问清楚边界条件。"
    return [
        f"开场：今天为什么要理解「{title}」。",
        f"核心概念：用通俗话解释 {item.get('domain') or '相关领域'} 的关键术语。",
        f"项目影响：{relevance}",
        f"行动清单：下一轮先问「{first_question}」",
    ]


def _study_note(item: dict[str, Any], combined: str) -> str:
    domain = item.get("domain") or "新领域"
    summary = item.get("summary") or "这条信息需要补充摘要。"
    relevance = item.get("work_relevance") or "需要判断它对当前项目的影响。"
    return f"这条「{domain}」知识的重点不是背参数，而是把它变成产品判断：{summary} {relevance}"

