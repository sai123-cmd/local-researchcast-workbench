from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

UNLIMITED_SESSION_FETCH_LIMIT = 5000


@dataclass
class WxResult:
    ok: bool
    results: list[dict[str, Any]]
    meta: dict[str, Any]
    raw: Any = None
    error: str = ""


class WxCliAdapter:
    def __init__(self, timeout_seconds: int = 25) -> None:
        self.timeout_seconds = timeout_seconds
        self.config_dir = Path.home() / ".wx-cli"

    @property
    def wx_bin(self) -> str | None:
        return shutil.which("wx.cmd") or shutil.which("wx.exe") or shutil.which("wx")

    @property
    def installed(self) -> bool:
        return self.wx_bin is not None

    def _run(self, args: list[str], timeout_seconds: int | None = None) -> WxResult:
        wx_bin = self.wx_bin
        if not wx_bin:
            return WxResult(False, [], {}, error="wx CLI is not installed. Run install.ps1, then run wx init as administrator.")
        cmd = [wx_bin, *args, "--with-meta", "--json"]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout_seconds or self.timeout_seconds,
                cwd=str(self.config_dir) if self.config_dir.exists() else None,
            )
        except subprocess.TimeoutExpired as exc:
            return WxResult(False, [], {}, error=f"wx command timed out: {exc}")
        except OSError as exc:
            return WxResult(False, [], {}, error=str(exc))

        if proc.returncode != 0:
            detail = (_decode(proc.stderr) or _decode(proc.stdout)).strip()
            return WxResult(False, [], {}, error=detail or f"wx exited with {proc.returncode}")

        try:
            data = json.loads(_decode(proc.stdout) or "{}")
        except json.JSONDecodeError as exc:
            return WxResult(False, [], {}, error=f"wx returned invalid JSON: {exc}")

        results, meta = self._unwrap(data)
        ok = meta.get("status") in (None, "ok", "windowed")
        return WxResult(ok, results, meta, raw=data)

    def _unwrap(self, data: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)], {}
        if not isinstance(data, dict):
            return [], {}
        meta = data.get("meta") or {}
        for key in ("results", "messages", "sessions", "attachments", "items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)], meta
        dict_items = [v for v in data.values() if isinstance(v, dict)]
        return dict_items, meta

    def health(self) -> dict[str, Any]:
        if not self.installed:
            return {"installed": False, "ok": False, "message": "wx CLI not installed"}
        result = self.sessions(limit=3)
        return {
            "installed": True,
            "ok": result.ok,
            "message": result.error or "wx CLI ready",
            "meta": result.meta,
            "sample_count": len(result.results),
        }

    def sessions(self, limit: int = 20) -> WxResult:
        actual_limit = UNLIMITED_SESSION_FETCH_LIMIT if limit <= 0 else limit
        return self._run(["sessions", "-n", str(actual_limit)])

    def new_messages(self) -> WxResult:
        return self._run(["new-messages"], timeout_seconds=60)

    def history(
        self,
        chat: str,
        since: str,
        until: str | None = None,
        limit: int = 120,
        offset: int = 0,
    ) -> WxResult:
        args = ["history", chat, "-n", str(limit), "--offset", str(offset), "--since", since]
        if until:
            args.extend(["--until", until])
        return self._run(args, timeout_seconds=60)

    def attachments(
        self,
        chat: str,
        since: str,
        until: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> WxResult:
        args = ["attachments", chat, "--kind", "image", "-n", str(limit), "--offset", str(offset), "--since", since]
        if until:
            args.extend(["--until", until])
        return self._run(args, timeout_seconds=60)

    def extract_attachment(self, attachment_id: str, output_path: Path) -> WxResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        args = ["extract", "--overwrite", "--output", os.fspath(output_path), attachment_id]
        return self._run(args, timeout_seconds=90)


def normalize_message(item: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    content = (
        item.get("content")
        or item.get("text")
        or item.get("message")
        or item.get("summary")
        or ""
    )
    conversation = item.get("chat") or item.get("conversation") or item.get("session") or item.get("name") or context.get("chat") or ""
    timestamp = item.get("timestamp") or item.get("time") or item.get("message_time") or item.get("recv_time_str")
    wx_msg_id = str(
        item.get("id")
        or item.get("msg_id")
        or item.get("local_id")
        or item.get("server_id")
        or ""
    )
    sender = item.get("sender") or item.get("from") or item.get("last_sender") or ""
    return {
        "wx_msg_id": wx_msg_id,
        "conversation": conversation,
        "chat_type": item.get("chat_type") or context.get("chat_type") or item.get("type") or "",
        "sender": sender,
        "username": item.get("username") or context.get("username") or "",
        "sender_username": item.get("sender_username") or "",
        "sender_contact_display": item.get("sender_contact_display") or "",
        "sender_group_nickname": item.get("sender_group_nickname") or "",
        "content": str(content).strip(),
        "message_time": str(timestamp or ""),
        "payload": item,
    }


def normalize_attachment(item: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    attachment_id = str(item.get("attachment_id") or "")
    chat = item.get("chat") or context.get("chat") or ""
    sender = item.get("sender") or ""
    timestamp = item.get("timestamp") or item.get("time") or ""
    return {
        "attachment_id": attachment_id,
        "conversation": chat,
        "chat_type": item.get("chat_type") or context.get("chat_type") or "",
        "username": item.get("username") or context.get("username") or "",
        "sender": sender,
        "kind": item.get("kind") or "image",
        "local_id": item.get("local_id"),
        "message_time": str(timestamp or ""),
        "payload": item,
    }


def _decode(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    candidates = []
    for encoding in ("utf-8-sig", "gbk", "cp936", "utf-16"):
        try:
            text = data.decode(encoding)
            badness = text.count("\ufffd") * 10 + sum(text.count(ch) for ch in "åèéçï")
            candidates.append((badness, text))
        except UnicodeDecodeError:
            continue
    if not candidates:
        return data.decode("utf-8", errors="replace")
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]

