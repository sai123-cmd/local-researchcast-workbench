from __future__ import annotations

from typing import Any


def build_readiness(health: dict[str, Any]) -> dict[str, Any]:
    counts = health.get("counts") or {}
    limits = health.get("wechat_limits") or {}
    items = [
        _item(
            "wechat",
            "微信深抓",
            bool((health.get("wx") or {}).get("ok")),
            True,
            (health.get("wx") or {}).get("message") or "未检测",
            "管理员 PowerShell 运行 init_wechat_admin.ps1 后确认 wx sessions/new-messages 正常。",
            ".\\init_wechat_admin.ps1",
        ),
        _item(
            "full_wechat_scope",
            "全量微信范围",
            _full_wechat_scope(limits),
            True,
            _scope_detail(limits),
            "保持 WX_*_LIMIT=0 表示全部；如改过 .env，请改回 0 并重启。",
            "notepad .env",
        ),
        _item(
            "llm",
            "模型 API",
            bool((health.get("llm") or {}).get("configured")),
            True,
            (health.get("llm") or {}).get("model") or "未配置",
            "在系统页填写 Base URL、API Key、模型名，并点“测试连通”。",
        ),
        _item(
            "notebooklm",
            "NotebookLM 实验插件",
            bool((health.get("notebooklm") or {}).get("ok")),
            False,
            (health.get("notebooklm") or {}).get("message") or "未检测",
            "NotebookLM 已降级为手动实验插件；ResearchCast 不依赖它。",
            f".\\.venv\\Scripts\\notebooklm.exe --profile {(health.get('notebooklm') or {}).get('profile') or 'default'} login",
        ),
        _item(
            "ocr",
            "图片 OCR",
            bool((health.get("ocr") or {}).get("ok")),
            True,
            (health.get("ocr") or {}).get("message") or "未检测",
            "安装 Tesseract OCR，或在 .env 里把 TESSERACT_BIN 指向 tesseract.exe。",
            "tesseract --version",
        ),
        _item(
            "tasks",
            "任务识别入库",
            int(counts.get("tasks") or 0) > 0,
            True,
            f"{counts.get('tasks', 0)} 个任务",
            "运行微信抓取、扫描文档并分析未处理事件。",
        ),
        _item(
            "knowledge",
            "新知识识别入库",
            int(counts.get("knowledge_items") or 0) > 0,
            True,
            f"{counts.get('knowledge_items', 0)} 个知识项",
            "导入规格书/供应商问题，运行分析并生成知识项。",
        ),
        _item(
            "local_audio",
            "ResearchCast 学习包",
            int(counts.get("learning_packs") or 0) > 0,
            True,
            f"{counts.get('learning_packs', 0)} 个学习包",
            "在今日页点“生成今日 ResearchCast”。",
        ),
        _item(
            "startup",
            "登录自启动",
            bool((health.get("startup") or {}).get("installed")),
            False,
            (health.get("startup") or {}).get("message") or "未安装",
            "安装后每天登录自动运行抓取、提醒和简报。",
            ".\\install_startup_task.ps1",
        ),
    ]
    required = [item for item in items if item["required"]]
    required_done = [item for item in required if item["status"] == "done"]
    blockers = [item for item in required if item["status"] != "done"]
    score = round(len(required_done) / max(1, len(required)) * 100)
    complete = not blockers
    return {
        "complete": complete,
        "status": "ready" if complete else "needs_setup",
        "score": score,
        "summary": "核心链路已可验收" if complete else f"还差 {len(blockers)} 个必须项",
        "items": items,
        "blockers": blockers,
    }


def _item(
    id_: str,
    label: str,
    ok: bool,
    required: bool,
    detail: str,
    action: str,
    command: str | None = None,
) -> dict[str, Any]:
    return {
        "id": id_,
        "label": label,
        "status": "done" if ok else "todo",
        "required": required,
        "detail": detail,
        "action": action,
        "command": command or "",
    }


def _full_wechat_scope(limits: dict[str, Any]) -> bool:
    keys = [
        "backfill_session_limit",
        "backfill_history_limit",
        "attachment_session_limit",
        "attachment_limit",
        "file_scan_limit",
    ]
    return all(int(limits.get(key) or 0) == 0 for key in keys)


def _scope_detail(limits: dict[str, Any]) -> str:
    return (
        f"回看会话 {_limit_text(limits.get('backfill_session_limit'))} / "
        f"消息 {_limit_text(limits.get('backfill_history_limit'))}；"
        f"附件会话 {_limit_text(limits.get('attachment_session_limit'))} / "
        f"图片 {_limit_text(limits.get('attachment_limit'))}；"
        f"文件 {_limit_text(limits.get('file_scan_limit'))}"
    )


def _limit_text(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "未检测"
    return "全部" if number == 0 else str(number)

