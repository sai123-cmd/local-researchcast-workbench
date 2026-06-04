from __future__ import annotations

import re
import sqlite3
from typing import Any

from ..db import json_loads, row_to_dict, rows_to_dicts


def get_task_context(conn: sqlite3.Connection, task_id: int) -> dict[str, Any] | None:
    task_row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task_row:
        return None

    task = row_to_dict(task_row) or {}
    source_ids = _int_list(task.get("source_event_ids"))
    source_events = _source_events(conn, source_ids)
    if not source_events:
        source_events = _fallback_events(conn, task)

    related_knowledge = _related_knowledge(conn, source_ids, task)
    source_text = "\n".join([event.get("body", "") for event in source_events])
    combined = "\n".join([task.get("title", ""), task.get("summary", ""), task.get("next_action", ""), source_text])

    return {
        "task": task,
        "source_events": source_events,
        "related_knowledge": related_knowledge,
        "next_questions": _next_questions(combined),
        "suggested_reply": _suggested_reply(task, combined),
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


def _fallback_events(conn: sqlite3.Connection, task: dict[str, Any]) -> list[dict[str, Any]]:
    keywords = _keywords(" ".join([task.get("title", ""), task.get("summary", ""), task.get("next_action", "")]))
    if not keywords:
        return []
    clauses = []
    params: list[str] = []
    for keyword in keywords[:4]:
        clauses.append("(title LIKE ? OR body LIKE ? OR COALESCE(conversation, '') LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
    rows = conn.execute(
        f"""
        SELECT *
        FROM raw_events
        WHERE {" OR ".join(clauses)}
        ORDER BY COALESCE(happened_at, received_at) DESC
        LIMIT 8
        """,
        params,
    ).fetchall()
    return rows_to_dicts(rows)


def _related_knowledge(conn: sqlite3.Connection, source_ids: list[int], task: dict[str, Any]) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM knowledge_items ORDER BY created_at DESC LIMIT 160").fetchall()
    source_set = set(source_ids)
    keywords = set(_keywords(" ".join([task.get("title", ""), task.get("summary", ""), task.get("next_action", "")])))
    matches: list[dict[str, Any]] = []
    for row in rows:
        item = row_to_dict(row) or {}
        item_sources = set(_int_list(json_loads(row["source_event_ids_json"], [])))
        text = " ".join([item.get("title", ""), item.get("domain", ""), item.get("summary", ""), item.get("work_relevance", "")])
        if (source_set and source_set.intersection(item_sources)) or any(keyword in text for keyword in keywords):
            matches.append(item)
        if len(matches) >= 5:
            break
    return matches


def _suggested_reply(task: dict[str, Any], combined: str) -> str:
    focus = _focus_phrase(combined)
    next_action = task.get("next_action") or "我先确认关键信息，再给你明确下一步。"
    title = task.get("title") or "这个事项"
    return "\n".join(
        [
            "我收到了，这个事项我先这样推进：",
            f"1. {next_action}",
            f"2. 我会重点确认{focus}，避免后面打样/报价/排期反复。",
            "你这边如果已有约束条件、样品时间或报价口径，也先发我，我整理后给你明确结论。",
            "",
            f"内部备注：对应任务「{title}」。",
        ]
    )


def _next_questions(combined: str) -> list[str]:
    text = combined.lower()
    if "电池" in combined or "mah" in text or "锂" in combined:
        return [
            "电芯容量、尺寸、厚度公差和保护板方案分别是多少？",
            "是否已有 UN38.3、MSDS、IEC/CB 或出货所需认证资料？",
            "样品周期、MOQ、报价有效期和是否支持不穿刺/挤压测试口径是什么？",
        ]
    if "摄像" in combined or "sensor" in text or "模组" in combined:
        return [
            "Sensor 型号、分辨率、FOV、接口和模组尺寸分别是多少？",
            "低照度、功耗、ISP/驱动支持和样品交期是否满足当前打样节奏？",
            "报价、MOQ、镜头可替代方案和量产风险点是什么？",
        ]
    if "报价" in combined or "价格" in combined or "quote" in text:
        return [
            "报价是否含税、含运费，币种和有效期是什么？",
            "MOQ、阶梯价、样品价和量产价是否分开？",
            "交期、付款条件和可接受的规格变更边界是什么？",
        ]
    if "认证" in combined or "ce" in text or "fcc" in text or "un38.3" in text:
        return [
            "目标市场和必须覆盖的认证清单是什么？",
            "当前硬件/电池/无线方案里哪些资料已经齐备？",
            "认证前置测试、整改周期和最早送检时间是什么？",
        ]
    return [
        "这件事的期望输出是什么：结论、报价、资料、样品还是排期？",
        "对方需要你回复的最小信息是什么？",
        "这件事是否影响硬件打样、融资材料或内容引擎 demo 的关键路径？",
    ]


def _focus_phrase(combined: str) -> str:
    text = combined.lower()
    if "电池" in combined or "mah" in text or "锂" in combined:
        return "容量、尺寸、保护板、认证资料、样品周期和测试口径"
    if "摄像" in combined or "sensor" in text or "模组" in combined:
        return "Sensor、FOV、接口、模组尺寸、驱动支持、功耗和样品周期"
    if "报价" in combined or "价格" in combined or "quote" in text:
        return "税费、MOQ、阶梯价、交期、付款条件和报价有效期"
    if "认证" in combined or "ce" in text or "fcc" in text or "un38.3" in text:
        return "目标市场、认证清单、资料齐备度、测试周期和整改风险"
    return "需求边界、下一步负责人、截止时间、对 当前项目 关键路径的影响"


def _keywords(text: str) -> list[str]:
    stopwords = {"处理", "工作", "消息", "需要", "确认", "下一步", "任务", "当前项目", "project"}
    tokens = re.findall(r"[A-Za-z0-9_.+-]{2,}|[\u4e00-\u9fff]{2,}", text)
    result: list[str] = []
    for token in tokens:
        if token in stopwords or token.lower() in {word.lower() for word in stopwords}:
            continue
        if token not in result:
            result.append(token)
    return result[:10]


def _int_list(value: Any) -> list[int]:
    if isinstance(value, str):
        value = json_loads(value, [])
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result

