from __future__ import annotations

import json
import re
import subprocess
import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import get_settings, update_env_file
from ..db import db_session
from ..models import ApiResponse
from ..services.ocr import OcrService
from ..services.readiness import build_readiness
from ..services.wx_adapter import WxCliAdapter

router = APIRouter(prefix="/api/settings", tags=["settings"])


class LLMSettingsPayload(BaseModel):
    base_url: str | None = Field(default=None)
    api_key: str | None = Field(default=None)
    model: str | None = Field(default=None)
    embedding_model: str | None = Field(default=None)


@router.get("")
def settings_health() -> dict[str, Any]:
    settings = get_settings()
    with db_session() as conn:
        counts = {
            "raw_events": conn.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0],
            "tasks": conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
            "knowledge_items": conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0],
            "reminders_pending": conn.execute("SELECT COUNT(*) FROM reminders WHERE status='pending'").fetchone()[0],
            "learning_packs": conn.execute("SELECT COUNT(*) FROM learning_packs").fetchone()[0],
            "briefings": conn.execute("SELECT COUNT(*) FROM briefings").fetchone()[0],
            "external_calls": conn.execute("SELECT COUNT(*) FROM external_calls").fetchone()[0],
        }
    return {
        "paths": {
            "workbench_root": str(settings.workbench_root),
            "data_dir": str(settings.data_dir),
            "inbox_dir": str(settings.inbox_dir),
            "attachments_dir": str(settings.attachments_dir),
            "podcasts_dir": str(settings.podcasts_dir),
            "db_path": str(settings.db_path),
            "workspace_root": str(settings.workspace_root),
            "wechat_files_root": str(settings.wechat_files_root) if settings.wechat_files_root else "",
        },
        "llm": {
            "configured": settings.llm_configured,
            "base_url": settings.llm_base_url,
            "model": settings.llm_model,
            "embedding_model": settings.llm_embedding_model,
            "api_key_set": bool(settings.llm_api_key),
        },
        "wx": WxCliAdapter().health(),
        "ocr": OcrService(settings).health(),
        "notebooklm": _notebooklm_health(),
        "scheduler": {
            "wx_poll_seconds": settings.wx_poll_seconds,
            "auto_startup_sync": settings.auto_startup_sync,
            "auto_daily_briefing": settings.auto_daily_briefing,
            "auto_daily_learning_audio": settings.auto_daily_learning_audio,
        },
        "startup": _startup_task_health(),
        "wechat_limits": {
            "backfill_session_limit": settings.wx_backfill_session_limit,
            "backfill_history_limit": settings.wx_backfill_history_limit,
            "attachment_session_limit": settings.wx_attachment_session_limit,
            "attachment_limit": settings.wx_attachment_limit,
            "file_scan_limit": settings.wx_file_scan_limit,
            "document_max_mb": settings.document_max_mb,
        },
        "counts": counts,
    }


@router.get("/readiness")
def readiness() -> dict[str, Any]:
    return build_readiness(settings_health())


@router.put("/llm", response_model=ApiResponse)
def update_llm_settings(payload: LLMSettingsPayload) -> ApiResponse:
    updates: dict[str, str] = {}
    if payload.base_url is not None:
        base_url = payload.base_url.strip().rstrip("/")
        if not base_url:
            raise HTTPException(status_code=400, detail="LLM_BASE_URL cannot be empty")
        updates["LLM_BASE_URL"] = base_url
    if payload.api_key is not None:
        updates["LLM_API_KEY"] = payload.api_key.strip()
    if payload.model is not None:
        model = payload.model.strip()
        if not model:
            raise HTTPException(status_code=400, detail="LLM_MODEL cannot be empty")
        updates["LLM_MODEL"] = model
    if payload.embedding_model is not None:
        updates["LLM_EMBEDDING_MODEL"] = payload.embedding_model.strip()

    update_env_file(updates)
    return ApiResponse(ok=True, data=settings_health(), message="模型配置已保存")


@router.post("/llm/test", response_model=ApiResponse)
def test_llm_settings(payload: LLMSettingsPayload | None = None) -> ApiResponse:
    settings = get_settings()
    base_url = (payload.base_url if payload and payload.base_url is not None else settings.llm_base_url).strip().rstrip("/")
    api_key = (payload.api_key if payload and payload.api_key is not None else settings.llm_api_key).strip()
    model = (payload.model if payload and payload.model is not None else settings.llm_model).strip()

    if not base_url or not api_key or not model:
        return ApiResponse(ok=False, data={"configured": False}, message="请先填写 Base URL、API Key 和模型名")

    started = time.perf_counter()
    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "Return a terse JSON object."},
                        {"role": "user", "content": "Return {\"ok\": true, \"service\": \"local-researchcast-workbench\"}"},
                    ],
                    "temperature": 0,
                    "max_tokens": 80,
                },
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if response.status_code >= 400:
                return ApiResponse(
                    ok=False,
                    data={"status_code": response.status_code, "elapsed_ms": elapsed_ms},
                    message=_compact_error(response.text),
                )
            body = response.json()
            sample = _strip_thinking(body.get("choices", [{}])[0].get("message", {}).get("content", ""))
            return ApiResponse(
                ok=True,
                data={"status_code": response.status_code, "elapsed_ms": elapsed_ms, "sample": sample[:240]},
                message=f"模型连通正常，耗时 {elapsed_ms}ms",
            )
    except Exception as exc:
        return ApiResponse(ok=False, data={"configured": True}, message=str(exc))


def _notebooklm_health() -> dict[str, Any]:
    settings = get_settings()
    notebooklm = str(settings.notebooklm_bin) if settings.notebooklm_bin.exists() else "notebooklm"
    profile = (settings.notebooklm_profile or "default").strip()
    cmd = [notebooklm]
    if profile:
        cmd.extend(["--profile", profile])
    cmd.extend(["auth", "check", "--test", "--json"])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        data = json.loads(proc.stdout or "{}")
        return {
            "installed": proc.returncode == 0 or bool(data),
            "ok": data.get("status") == "ok",
            "status": data.get("status"),
            "bin": notebooklm,
            "profile": profile,
            "message": data.get("details", {}).get("error") or "ok",
        }
    except Exception as exc:
        return {"installed": False, "ok": False, "bin": notebooklm, "profile": profile, "message": str(exc)}


def _startup_task_health() -> dict[str, Any]:
    task_name = "Local ResearchCast Workbench"
    script = (
        "$taskName = 'Local ResearchCast Workbench'; "
        "$startup = [Environment]::GetFolderPath([Environment+SpecialFolder]::Startup); "
        "if (-not $startup) { $startup = Join-Path $env:APPDATA 'Microsoft\\Windows\\Start Menu\\Programs\\Startup' }; "
        "$startupFile = Join-Path $startup 'Local ResearchCast Workbench.cmd'; "
        "$task = $null; $taskError = $null; "
        "try { $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop } catch { $taskError = $_.Exception.Message }; "
        "$message = if ($taskError) { $taskError } else { 'not installed' }; "
        "if ($task) { "
        "[pscustomobject]@{installed=$true; state=$task.State.ToString(); task_name=$task.TaskName; method='scheduled_task'; path=$null; message='scheduled task installed'} | ConvertTo-Json -Compress "
        "} elseif (Test-Path $startupFile) { "
        "[pscustomobject]@{installed=$true; state='Startup folder'; task_name='Local ResearchCast Workbench.cmd'; method='startup_folder'; path=$startupFile; message='startup folder fallback installed'} | ConvertTo-Json -Compress "
        "} else { "
        "[pscustomobject]@{installed=$false; state='missing'; task_name=$taskName; method='none'; path=$startupFile; message=$message} | ConvertTo-Json -Compress "
        "}"
    )
    try:
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        data = json.loads(proc.stdout or "{}")
        return {
            "installed": bool(data.get("installed")),
            "ok": bool(data.get("installed")),
            "state": data.get("state") or "unknown",
            "task_name": data.get("task_name") or task_name,
            "method": data.get("method") or "unknown",
            "path": data.get("path") or "",
            "message": data.get("message") or ("installed" if data.get("installed") else "not installed"),
        }
    except Exception as exc:
        return {"installed": False, "ok": False, "state": "unknown", "task_name": task_name, "message": str(exc)}


def _strip_thinking(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I).strip()


def _compact_error(text: str, limit: int = 260) -> str:
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    return text[: limit - 1] + "…" if len(text) > limit else text

