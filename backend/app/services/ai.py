from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

import httpx

from ..config import Settings
from ..db import content_hash, log_external_call_finish, log_external_call_start


TASK_KEYWORDS = [
    "报价",
    "规格",
    "规格书",
    "确认",
    "请",
    "需要",
    "能否",
    "是否",
    "会议",
    "打样",
    "供应商",
    "外包",
    "认证",
    "deadline",
    "today",
    "tomorrow",
    "asap",
    "?",
    "？",
]

KNOWLEDGE_DOMAIN_RULES = {
    "电池": {
        "strong": ["电池", "电芯", "锂电", "battery", "mah", "充电", "保护板", "un38.3", "msds", "pogo"],
        "weak": ["容量", "发热", "热管理", "功耗"],
    },
    "摄像模组": {
        "strong": ["摄像", "camera", "ov", "fov", "sensor", "镜头", "isp"],
        "weak": ["模组", "图像", "分辨率", "低照度"],
    },
    "通信定位": {
        "strong": ["4g", "gnss", "gps", "ble", "天线", "cat.1", "esim", "定位"],
        "weak": ["通信", "联网", "蓝牙", "蜂窝"],
    },
    "认证量产": {
        "strong": ["认证", "ptcrb", "fcc", "ce", "srrc", "rohs", "reach", "un38.3", "bom", "nre", "量产"],
        "weak": ["供应链", "送检", "合规", "测试"],
    },
    "AI内容引擎": {
        "strong": ["高光", "vlog", "diary", "comic", "pipeline", "多模态", "模型", "agent", "llm", "推理"],
        "weak": ["内容", "视频", "算力", "cpu", "gpu"],
    },
}


class AIService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def analyze_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        if self.settings.llm_configured:
            llm_result = await self._analyze_with_llm(events)
            if llm_result:
                return llm_result
        return self._analyze_with_heuristics(events)

    async def _analyze_with_llm(self, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        payload_events = [
            {
                "id": event["id"],
                "source": event["source"],
                "title": event["title"],
                "body": event["body"][:4000],
                "author": event.get("author"),
                "conversation": event.get("conversation"),
                "happened_at": event.get("happened_at"),
            }
            for event in events
        ]
        prompt = f"""
你是 用户的本地工作助理。请从每日微信/文档事件中抽取任务、风险、等待事项和新知识。

优先级权重：融资/投资人 > 硬件打样/供应商/规格/认证 > 内容引擎 demo > 招聘/团队 > 政策/法务 > 其他日常。

只返回 JSON，格式：
{{
  "tasks": [{{
    "title": "...",
    "summary": "...",
    "priority_score": 0-100,
    "priority_label": "P0|P1|P2|P3",
    "project": "当前项目",
    "owner": "me|other|supplier",
    "next_action": "...",
    "due_at": "ISO8601 or null",
    "source_event_ids": [1]
  }}],
  "knowledge_items": [{{
    "title": "...",
    "domain": "电池|摄像模组|通信定位|认证量产|AI内容引擎|其他",
    "summary": "...",
    "novelty_score": 0-100,
    "urgency": "low|medium|high",
    "work_relevance": "...",
    "key_questions": ["..."],
    "followups": ["..."],
    "source_event_ids": [1]
  }}]
}}

事件：
{json.dumps(payload_events, ensure_ascii=False)}
"""
        call_id = _audit_start(
            self.settings,
            purpose="任务和新知识抽取",
            prompt=prompt,
            content_summary=_events_summary(payload_events),
            request_payload=payload_events,
            provenance={"event_count": len(payload_events)},
        )
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                response = await client.post(
                    f"{self.settings.llm_base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                    json={
                        "model": self.settings.llm_model,
                        "messages": [
                            {"role": "system", "content": "Return strict JSON only."},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.2,
                    },
                )
                response.raise_for_status()
                content = _strip_thinking(response.json()["choices"][0]["message"]["content"])
                log_external_call_finish(call_id, status="ok", response_summary=_compact(content, 360))
                return _extract_json(content)
        except Exception as exc:
            log_external_call_finish(call_id, status="failed", error=_compact(str(exc), 360))
            return None

    def _analyze_with_heuristics(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        tasks: list[dict[str, Any]] = []
        knowledge_items: list[dict[str, Any]] = []
        for event in events:
            text = f"{event.get('title', '')}\n{event.get('body', '')}"
            short = _compact(text, 160)
            event_ids = [event["id"]]
            if any(keyword.lower() in text.lower() for keyword in TASK_KEYWORDS):
                score = _priority_score(text)
                tasks.append(
                    {
                        "title": _task_title(event, text),
                        "summary": short,
                        "priority_score": score,
                        "priority_label": _priority_label(score),
                        "project": "当前项目",
                        "owner": "me",
                        "next_action": _next_action(text),
                        "due_at": _guess_due_at(text),
                        "source_event_ids": event_ids,
                        "provenance": {"mode": "heuristic"},
                    }
                )
            domain = _match_knowledge_domain(text)
            if domain:
                knowledge_items.append(
                    {
                        "title": f"{domain}新信息：{_compact(event.get('title') or short, 36)}",
                        "domain": domain,
                        "summary": short,
                        "novelty_score": 70 if "规格" in text or "规格书" in text else 60,
                        "urgency": "high" if domain in {"电池", "摄像模组", "通信定位"} else "medium",
                        "work_relevance": f"这可能影响 当前硬件项目 的{domain}决策、供应商沟通或打样风险。",
                        "key_questions": _domain_questions(domain),
                        "followups": ["整理原始资料", "列出供应商追问清单", "形成 5 分钟学习音频"],
                        "source_event_ids": event_ids,
                        "provenance": {"mode": "heuristic"},
                    }
                )
        return {"tasks": tasks[:30], "knowledge_items": knowledge_items[:20]}

    async def learning_script(self, items: list[dict[str, Any]], title: str, source_material: str | None = None) -> str:
        if self.settings.llm_configured:
            prompt = f"""
请把以下 当前项目 新知识整理成 6-8 分钟中文播客讲稿。
风格：像一个清醒、靠谱的技术合伙人，帮助创始人快速补课。
结构：为什么重要 -> 核心概念 -> 对当前项目的影响 -> 我下一步该问什么。
标题：{title}
资料：{json.dumps(items, ensure_ascii=False)}
"""
            prompt += f"""

请按“创始人新领域快速补课”的标准重写，不要逐条照念摘要，也不要写成会议纪要。
必须覆盖：今天为什么要懂、核心概念、关键参数/规则、当前项目的产品/供应链/增长影响、下一轮追问清单、仍缺的证据。
如果来源证据不足，要明确说目前不足以判断，并指出还需要什么原文、规格书、报价或对方确认。

原文证据与研究材料：
{(source_material or "")[:16000]}
"""
            call_id = _audit_start(
                self.settings,
                purpose="学习播客讲稿生成",
                prompt=prompt,
                content_summary=_knowledge_summary(items),
                request_payload={"title": title, "items": items},
                provenance={"knowledge_item_count": len(items), "title": title},
            )
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    response = await client.post(
                        f"{self.settings.llm_base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                        json={
                            "model": self.settings.llm_model,
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.4,
                        },
                    )
                    response.raise_for_status()
                    content = _strip_thinking(response.json()["choices"][0]["message"]["content"]).strip()
                    log_external_call_finish(call_id, status="ok", response_summary=_compact(content, 360))
                    return content
            except Exception as exc:
                log_external_call_finish(call_id, status="failed", error=_compact(str(exc), 360))
                pass
        return self._template_script(items, title)

    def _template_script(self, items: list[dict[str, Any]], title: str) -> str:
        lines = [f"今天的学习主题是：{title}。", "我们先抓住它为什么对当前项目重要。"]
        for item in items:
            lines.append(f"第一，{item['title']}。{item['summary']}")
            lines.append(f"它和当前项目的关系是：{item['work_relevance']}")
            questions = item.get("key_questions") or []
            if questions:
                lines.append("下一轮沟通可以问：" + "；".join(questions[:4]) + "。")
        lines.append("最后，把行动收束成三件事：确认规格边界，追问供应商证据，沉淀到 当前项目的硬件和产品决策表。")
        return "\n\n".join(lines)

    async def daily_briefing(self, context: dict[str, Any]) -> dict[str, Any]:
        if self.settings.llm_configured:
            prompt = f"""
你是 用户的本地工作参谋。请把今日任务、提醒、新知识和最近事件整理成一份可执行作战简报。

目标：让创始人知道今天先做什么、哪些人要追问、哪些决策不能拖、哪些新知识值得变成学习内容。
优先级权重：融资/投资人 > 硬件打样/供应商/规格/认证 > 内容引擎 demo > 招聘/团队 > 政策/法务 > 其他日常。

只返回 JSON，格式：
{{
  "title": "今日作战简报",
  "summary": "两到三句话，直接说明今天最重要的局面。",
  "focus": [{{"title": "...", "body": "...", "action": "...", "priority": "P0|P1|P2"}}],
  "decisions": [{{"title": "...", "body": "...", "action": "...", "priority": "P0|P1|P2"}}],
  "followups": [{{"title": "...", "body": "...", "action": "...", "priority": "P0|P1|P2"}}],
  "waiting": [{{"title": "...", "body": "...", "action": "...", "priority": "P0|P1|P2"}}],
  "learning": [{{"title": "...", "body": "...", "action": "...", "priority": "P0|P1|P2"}}],
  "risks": [{{"title": "...", "body": "...", "action": "...", "priority": "P0|P1|P2"}}]
}}

上下文：
{json.dumps(context, ensure_ascii=False)}
"""
            call_id = _audit_start(
                self.settings,
                purpose="今日作战简报生成",
                prompt=prompt,
                content_summary=_briefing_context_summary(context),
                request_payload=context,
                provenance={
                    "task_count": len(context.get("tasks", [])),
                    "knowledge_count": len(context.get("knowledge", [])),
                    "reminder_count": len(context.get("reminders", [])),
                },
            )
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    response = await client.post(
                        f"{self.settings.llm_base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                        json={
                            "model": self.settings.llm_model,
                            "messages": [
                                {"role": "system", "content": "Return strict JSON only."},
                                {"role": "user", "content": prompt},
                            ],
                            "temperature": 0.25,
                        },
                    )
                    response.raise_for_status()
                    content = _strip_thinking(response.json()["choices"][0]["message"]["content"])
                    log_external_call_finish(call_id, status="ok", response_summary=_compact(content, 360))
                    parsed = _extract_json(content)
                    if parsed:
                        parsed["provenance"] = {"mode": "llm", "model": self.settings.llm_model}
                        return _normalize_briefing(parsed)
            except Exception as exc:
                log_external_call_finish(call_id, status="failed", error=_compact(str(exc), 360))
                pass
        return self._template_briefing(context)

    def _template_briefing(self, context: dict[str, Any]) -> dict[str, Any]:
        tasks = sorted(context.get("tasks", []), key=lambda item: int(item.get("priority_score") or 0), reverse=True)
        reminders = context.get("reminders", [])
        knowledge = sorted(context.get("knowledge", []), key=lambda item: int(item.get("novelty_score") or 0), reverse=True)

        focus = [
            _brief_item(
                task.get("title", "未命名任务"),
                task.get("summary") or task.get("next_action") or "",
                task.get("next_action") or "拆成一个明确下一步并推进。",
                task.get("priority_label", "P2"),
            )
            for task in tasks[:5]
        ]
        decisions = [
            _brief_item(
                task.get("title", "待决策事项"),
                task.get("summary") or "",
                "确认是否今天推进、委派、等待或归档。",
                task.get("priority_label", "P2"),
            )
            for task in tasks
            if any(keyword in f"{task.get('title', '')}{task.get('summary', '')}{task.get('next_action', '')}" for keyword in ["报价", "规格", "供应商", "打样", "认证", "会议"])
        ][:4]
        followups = [
            _brief_item(
                task.get("title", "待追问事项"),
                task.get("summary") or "",
                task.get("next_action") or "发一条明确追问，要求对方补齐参数、时间或责任人。",
                task.get("priority_label", "P2"),
            )
            for task in tasks
            if any(keyword in f"{task.get('title', '')}{task.get('summary', '')}{task.get('next_action', '')}" for keyword in ["供应商", "追问", "确认", "能否", "是否", "？", "?"])
        ][:5]
        waiting = [
            _brief_item(
                reminder.get("title", "待提醒事项"),
                reminder.get("body") or reminder.get("remind_at") or "",
                f"到点检查：{reminder.get('remind_at', '')}",
                "P1",
            )
            for reminder in reminders[:5]
        ]
        learning = [
            _brief_item(
                item.get("title", "新知识"),
                item.get("work_relevance") or item.get("summary") or "",
                "生成学习包或播客，先听懂关键参数和下一轮追问。",
                "P1" if item.get("urgency") == "high" else "P2",
            )
            for item in knowledge[:5]
        ]
        risks = [
            _brief_item("模型 API 未配置", "当前任务理解仍使用本地规则，复杂语境会漏判。", "在系统页填入 API Key 并测试连通。", "P1")
        ] if not self.settings.llm_configured else []

        summary_bits = []
        if focus:
            summary_bits.append(f"今天先盯 {len(focus)} 个开放重点。")
        if followups:
            summary_bits.append(f"有 {len(followups)} 个事项需要你主动追问或确认。")
        if learning:
            summary_bits.append(f"新知识队列里有 {len(learning)} 个主题适合转成学习包。")
        return {
            "title": "今日作战简报",
            "summary": "".join(summary_bits) or "今天暂时没有高压事项，适合整理资料、清空待处理事件并补齐系统配置。",
            "focus": focus,
            "decisions": decisions,
            "followups": followups,
            "waiting": waiting,
            "learning": learning,
            "risks": risks,
            "provenance": {"mode": "heuristic"},
        }


def _extract_json(content: str) -> dict[str, Any] | None:
    content = _strip_thinking(content)
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?", "", content).strip()
        content = re.sub(r"```$", "", content).strip()
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.S)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                return None
    return None


def _strip_thinking(content: str) -> str:
    return re.sub(r"<think>.*?</think>", "", content or "", flags=re.S | re.I).strip()


def _audit_start(
    settings: Settings,
    *,
    purpose: str,
    prompt: str,
    content_summary: str,
    request_payload: Any,
    provenance: dict[str, Any] | None = None,
) -> int | None:
    try:
        return log_external_call_start(
            provider="openai_compatible",
            purpose=purpose,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            prompt_chars=len(prompt),
            content_summary=_compact(content_summary, 600),
            request_hash=content_hash(purpose, request_payload),
            provenance=provenance or {},
        )
    except Exception:
        return None


def _events_summary(events: list[dict[str, Any]]) -> str:
    samples = []
    for event in events[:5]:
        title = event.get("title") or event.get("conversation") or "事件"
        source = event.get("source") or ""
        body = event.get("body") or ""
        samples.append(f"{event.get('id')} {source} {title}: {_compact(str(body), 80)}")
    return f"{len(events)} 条事件；样例：" + " | ".join(samples)


def _knowledge_summary(items: list[dict[str, Any]]) -> str:
    samples = []
    for item in items[:6]:
        samples.append(f"{item.get('id')} {item.get('domain', '')} {item.get('title', '')}")
    return f"{len(items)} 个知识项；" + " | ".join(samples)


def _briefing_context_summary(context: dict[str, Any]) -> str:
    tasks = context.get("tasks", [])
    knowledge = context.get("knowledge", [])
    reminders = context.get("reminders", [])
    first_task = tasks[0].get("title") if tasks and isinstance(tasks[0], dict) else ""
    first_knowledge = knowledge[0].get("title") if knowledge and isinstance(knowledge[0], dict) else ""
    return (
        f"任务 {len(tasks)}，提醒 {len(reminders)}，知识 {len(knowledge)}；"
        f"首个任务：{first_task}；首个知识：{first_knowledge}"
    )


def _normalize_briefing(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(data.get("title") or "今日作战简报"),
        "summary": str(data.get("summary") or ""),
        "focus": _normalize_brief_items(data.get("focus")),
        "decisions": _normalize_brief_items(data.get("decisions")),
        "followups": _normalize_brief_items(data.get("followups")),
        "waiting": _normalize_brief_items(data.get("waiting")),
        "learning": _normalize_brief_items(data.get("learning")),
        "risks": _normalize_brief_items(data.get("risks")),
        "provenance": data.get("provenance") if isinstance(data.get("provenance"), dict) else {"mode": "llm"},
    }


def _normalize_brief_items(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value[:8]:
        if isinstance(item, dict):
            result.append(
                _brief_item(
                    item.get("title") or "事项",
                    item.get("body") or item.get("summary") or "",
                    item.get("action") or item.get("next_action") or "明确下一步。",
                    item.get("priority") or item.get("priority_label") or "P2",
                )
            )
        else:
            result.append(_brief_item(str(item), "", "明确下一步。", "P2"))
    return result


def _brief_item(title: Any, body: Any, action: Any, priority: Any) -> dict[str, str]:
    priority_text = str(priority or "P2").upper()
    if priority_text not in {"P0", "P1", "P2", "P3"}:
        priority_text = "P2"
    return {
        "title": _compact(str(title or "事项"), 80),
        "body": _compact(str(body or ""), 220),
        "action": _compact(str(action or "明确下一步。"), 180),
        "priority": priority_text,
    }


def _priority_score(text: str) -> int:
    score = 45
    weighted = {
        "投资": 30,
        "融资": 30,
        "供应商": 22,
        "规格书": 22,
        "报价": 20,
        "打样": 24,
        "认证": 20,
        "外包": 16,
        "今天": 18,
        "明天": 18,
        "紧急": 25,
        "asap": 25,
    }
    lower = text.lower()
    for keyword, points in weighted.items():
        if keyword.lower() in lower:
            score += points
    if "?" in text or "？" in text:
        score += 10
    return max(0, min(100, score))


def _priority_label(score: int) -> str:
    if score >= 85:
        return "P0"
    if score >= 70:
        return "P1"
    if score >= 45:
        return "P2"
    return "P3"


def _compact(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    return clean[: limit - 1] + "…" if len(clean) > limit else clean


def _task_title(event: dict[str, Any], text: str) -> str:
    conversation = event.get("conversation") or event.get("author") or "新事项"
    if "报价" in text:
        return f"处理报价/成本信息：{conversation}"
    if "规格" in text or "规格书" in text:
        return f"阅读并追问规格资料：{conversation}"
    if "供应商" in text or "模组" in text:
        return f"跟进供应商问题：{conversation}"
    return f"处理工作消息：{_compact(event.get('title') or conversation, 40)}"


def _next_action(text: str) -> str:
    if "规格书" in text or "规格" in text:
        return "先提取关键参数，再列出供应商追问清单。"
    if "报价" in text:
        return "把报价、MOQ、交期、NRE 和认证成本整理到对比表。"
    if "会议" in text:
        return "确认会议目标、参会人、议程和会后责任人。"
    if "?" in text or "？" in text:
        return "先判断问题归属，再回复一个明确下一步或需要对方补充的信息。"
    return "判断是否需要回复、归档或拆成下一步任务。"


def _guess_due_at(text: str) -> str | None:
    now = datetime.now()
    if "今天" in text or "asap" in text.lower() or "紧急" in text:
        return now.replace(hour=20, minute=0, second=0, microsecond=0).isoformat()
    if "明天" in text or "tomorrow" in text.lower():
        return (now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0).isoformat()
    return None


def _match_knowledge_domain(text: str) -> str | None:
    lower = text.lower()
    best_domain: str | None = None
    best_score = 0
    for domain, rules in KNOWLEDGE_DOMAIN_RULES.items():
        strong_hits = sum(1 for keyword in rules["strong"] if keyword.lower() in lower)
        weak_hits = sum(1 for keyword in rules["weak"] if keyword.lower() in lower)
        if not strong_hits:
            continue
        score = strong_hits * 3 + weak_hits
        if score > best_score:
            best_domain = domain
            best_score = score
    return best_domain


def _domain_questions(domain: str) -> list[str]:
    return {
        "电池": ["容量和尺寸是多少？", "充电电流和发热边界是什么？", "是否有 UN38.3/运输资料？", "循环寿命和低温表现如何？"],
        "摄像模组": ["传感器型号和 FOV 是什么？", "低照度表现如何？", "功耗和发热是多少？", "模组尺寸和排线约束是什么？"],
        "通信定位": ["支持哪些频段？", "北美认证路径是什么？", "弱网下回传策略如何？", "天线布局有哪些禁区？"],
        "认证量产": ["认证/NRE/MOQ 分别是多少？", "交期和风险项是什么？", "哪些参数还未冻结？"],
        "AI内容引擎": ["输入数据最低要求是什么？", "高光判定标准是什么？", "输出质量验收如何定义？"],
    }.get(domain, ["这个概念是什么？", "它影响 当前项目 哪个决策？", "下一步要验证什么？"])

