from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..config import Settings
from ..db import json_dumps, json_loads, row_to_dict, rows_to_dicts, utc_now
from .ai import AIService


NOTEBOOKLM_READY_STATUS = "notebooklm_audio_ready"


class PodcastService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def notebooklm_cmd(self, *args: str) -> list[str]:
        notebooklm = str(self.settings.notebooklm_bin) if self.settings.notebooklm_bin.exists() else "notebooklm"
        profile = (self.settings.notebooklm_profile or "default").strip()
        cmd = [notebooklm]
        if profile:
            cmd.extend(["--profile", profile])
        cmd.extend(args)
        return cmd

    async def create_learning_pack(
        self,
        conn: sqlite3.Connection,
        knowledge_item_ids: list[int],
        title: str | None = None,
        use_notebooklm: bool = True,
    ) -> dict[str, Any]:
        if not knowledge_item_ids:
            raise ValueError("knowledge_item_ids cannot be empty")
        placeholders = ",".join("?" for _ in knowledge_item_ids)
        items = rows_to_dicts(
            conn.execute(
                f"SELECT * FROM knowledge_items WHERE id IN ({placeholders})",
                knowledge_item_ids,
            ).fetchall()
        )
        if not items:
            raise ValueError("No knowledge items found")
        pack_title = title or f"学习包：{items[0]['domain']} / {items[0]['title'][:24]}"
        source_text = self.source_text_for_items(conn, items, pack_title)
        script = await AIService(self.settings).learning_script(items, pack_title, source_text)
        now = utc_now()
        cur = conn.execute(
            """
            INSERT INTO learning_packs(
                title, status, knowledge_item_ids_json, script_text,
                notebooklm_status, provenance_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pack_title,
                "script_ready",
                json_dumps(knowledge_item_ids),
                script,
                "pending" if use_notebooklm else "not_requested",
                json_dumps({"source": "knowledge_items"}),
                now,
                now,
            ),
        )
        pack_id = int(cur.lastrowid)
        conn.commit()
        if use_notebooklm:
            self.write_script_file(pack_id, pack_title, script)
            local_path, local_status = None, "script_ready"
        else:
            local_path, local_status = await self.generate_local_audio(pack_id, pack_title, script)
        conn.execute(
            "UPDATE learning_packs SET status=?, local_audio_path=?, updated_at=? WHERE id=?",
            (local_status, str(local_path) if local_path else None, utc_now(), pack_id),
        )
        conn.commit()
        return enrich_learning_pack(row_to_dict(conn.execute("SELECT * FROM learning_packs WHERE id=?", (pack_id,)).fetchone()) or {}, self.settings)

    async def create_today_learning_pack(
        self,
        conn: sqlite3.Connection,
        use_notebooklm: bool = True,
        regenerate: bool = False,
        limit: int = 6,
    ) -> dict[str, Any]:
        day = datetime.now().date().isoformat()
        pack_title = f"今日学习音频 {day}"
        existing = row_to_dict(
            conn.execute(
                "SELECT * FROM learning_packs WHERE title=? ORDER BY id DESC LIMIT 1",
                (pack_title,),
            ).fetchone()
        )
        if existing and not regenerate:
            existing["knowledge_item_ids"] = json_loads(existing.get("knowledge_item_ids_json"), [])
            return existing

        items = self._select_today_knowledge(conn, day, limit)
        if not items:
            raise ValueError("No knowledge items found for today's learning pack")
        item_ids = [int(item["id"]) for item in items]
        source_text = self.source_text_for_items(conn, items, pack_title)
        script = await AIService(self.settings).learning_script(items, pack_title, source_text)
        now = utc_now()

        if existing:
            pack_id = int(existing["id"])
            provenance = {"source": "daily_knowledge", "day": day, "regenerated": True}
            existing_provenance = json_loads(existing.get("provenance_json"), {})
            if isinstance(existing_provenance, dict) and isinstance(existing_provenance.get("notebooklm_sync"), dict):
                provenance["previous_notebooklm_sync"] = existing_provenance["notebooklm_sync"]
            conn.execute(
                """
                UPDATE learning_packs
                SET status=?, knowledge_item_ids_json=?, script_text=?,
                    notebooklm_status=?, notebooklm_audio_path=NULL,
                    provenance_json=?, updated_at=?
                WHERE id=?
                """,
                (
                    "script_ready",
                    json_dumps(item_ids),
                    script,
                    "pending" if use_notebooklm else "not_requested",
                    json_dumps(provenance),
                    now,
                    pack_id,
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO learning_packs(
                    title, status, knowledge_item_ids_json, script_text,
                    notebooklm_status, provenance_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pack_title,
                    "script_ready",
                    json_dumps(item_ids),
                    script,
                    "pending" if use_notebooklm else "not_requested",
                    json_dumps({"source": "daily_knowledge", "day": day}),
                    now,
                    now,
                ),
            )
            pack_id = int(cur.lastrowid)

        conn.commit()
        if use_notebooklm:
            self.write_script_file(pack_id, pack_title, script)
            local_path, local_status = None, "script_ready"
        else:
            local_path, local_status = await self.generate_local_audio(pack_id, pack_title, script)
        conn.execute(
            "UPDATE learning_packs SET status=?, local_audio_path=?, updated_at=? WHERE id=?",
            (local_status, str(local_path) if local_path else None, utc_now(), pack_id),
        )
        conn.commit()
        pack = row_to_dict(conn.execute("SELECT * FROM learning_packs WHERE id=?", (pack_id,)).fetchone()) or {}
        pack["knowledge_item_ids"] = json_loads(pack.get("knowledge_item_ids_json"), [])
        return enrich_learning_pack(pack, self.settings)

    def _select_today_knowledge(self, conn: sqlite3.Connection, day: str, limit: int) -> list[dict[str, Any]]:
        rows = rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM knowledge_items
                WHERE substr(created_at, 1, 10)=?
                ORDER BY
                    CASE urgency WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                    novelty_score DESC,
                    created_at DESC
                LIMIT ?
                """,
                (day, limit),
            ).fetchall()
        )
        if rows:
            return rows
        return rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM knowledge_items
                ORDER BY
                    CASE urgency WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                    novelty_score DESC,
                    created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        )

    def source_text_for_pack(self, conn: sqlite3.Connection, pack: dict[str, Any]) -> str:
        item_ids = _int_list(pack.get("knowledge_item_ids") or json_loads(pack.get("knowledge_item_ids_json"), []))
        items: list[dict[str, Any]] = []
        if item_ids:
            placeholders = ",".join("?" for _ in item_ids)
            rows = rows_to_dicts(
                conn.execute(
                    f"SELECT * FROM knowledge_items WHERE id IN ({placeholders})",
                    item_ids,
                ).fetchall()
            )
            by_id = {int(row["id"]): row for row in rows}
            items = [by_id[item_id] for item_id in item_ids if item_id in by_id]
        return self.source_text_for_items(conn, items, pack.get("title") or "当前项目 学习包", pack.get("script_text") or "")

    def source_text_for_items(
        self,
        conn: sqlite3.Connection,
        items: list[dict[str, Any]],
        title: str,
        script: str = "",
    ) -> str:
        event_ids: list[int] = []
        for item in items:
            for event_id in _int_list(item.get("source_event_ids") or json_loads(item.get("source_event_ids_json"), [])):
                if event_id not in event_ids:
                    event_ids.append(event_id)

        source_events: list[dict[str, Any]] = []
        if event_ids:
            placeholders = ",".join("?" for _ in event_ids[:40])
            source_events = rows_to_dicts(
                conn.execute(
                    f"""
                    SELECT *
                    FROM raw_events
                    WHERE id IN ({placeholders})
                    ORDER BY COALESCE(happened_at, received_at) DESC
                    """,
                    event_ids[:40],
                ).fetchall()
            )

        lines = [
            f"# {title}",
            "",
            "## NotebookLM Audio Overview 指令",
            "请基于下面的原文证据和研究材料生成中文 Audio Overview。听众是 用户，需要快速学习新领域并决定下一步行动。",
            "不要照读资料。请解释概念、参数、风险、和当前项目的关系，并给出下一轮供应商/团队追问清单。",
            "如果证据不足，请明确指出哪些结论不能下，以及还需要补哪些规格书、报价、图片、测试或对方确认。",
            "",
            "## 学习目标",
            "- 这条新知识为什么今天值得学。",
            "- 它对 当前硬件项目、供应链、内容引擎、增长或合规有什么影响。",
            "- 我下一轮应该问谁、问什么、要什么证据。",
            "",
            "## 结构化知识点",
        ]
        for index, item in enumerate(items, 1):
            questions = item.get("key_questions") or json_loads(item.get("key_questions_json"), [])
            followups = item.get("followups") or json_loads(item.get("followups_json"), [])
            lines.extend(
                [
                    f"### {index}. {item.get('title') or '未命名知识点'}",
                    f"- 领域：{item.get('domain') or '未知'}",
                    f"- 紧急度：{item.get('urgency') or 'medium'}",
                    f"- 新颖度：{item.get('novelty_score') or ''}",
                    f"- 摘要：{item.get('summary') or ''}",
                    f"- 项目关系：{item.get('work_relevance') or ''}",
                    f"- 关键问题：{'; '.join(map(str, questions[:8])) if questions else '暂无'}",
                    f"- 后续动作：{'; '.join(map(str, followups[:8])) if followups else '暂无'}",
                    "",
                ]
            )

        if source_events:
            lines.append("## 原文证据")
            for index, event in enumerate(source_events, 1):
                body = _compact(str(event.get("body") or ""), 2600)
                lines.extend(
                    [
                        f"### 证据 {index}: {event.get('title') or event.get('source') or 'source'}",
                        f"- 来源：{event.get('source') or ''} / {event.get('conversation') or ''} / {event.get('author') or ''}",
                        f"- 时间：{event.get('happened_at') or event.get('received_at') or ''}",
                        "",
                        body,
                        "",
                    ]
                )
        else:
            lines.extend(["## 原文证据", "暂无直接关联原文。请把结构化知识点视为二手摘要，生成时降低确定性。", ""])

        if script:
            lines.extend(
                [
                    "## 本地研究讲稿草稿",
                    "下面只是本地模型生成的草稿，供 NotebookLM 理解意图，不要照读；请优先依据原文证据重组内容。",
                    "",
                    _compact(script, 5000),
                    "",
                ]
            )
        return "\n".join(lines).strip() + "\n"

    async def generate_local_audio(self, pack_id: int, title: str, script: str) -> tuple[Path | None, str]:
        txt_path = self.write_script_file(pack_id, title, script)
        mp3_path = txt_path.with_suffix(".mp3")
        if self.settings.tts_provider.lower() != "edge":
            return None, "script_ready"
        try:
            import edge_tts

            communicate = edge_tts.Communicate(script, "zh-CN-XiaoxiaoNeural")
            await communicate.save(str(mp3_path))
            return mp3_path, "local_audio_ready"
        except Exception:
            return None, "script_ready"

    def write_script_file(self, pack_id: int, title: str, script: str) -> Path:
        safe = _safe_filename(f"{pack_id}-{title}")[:80]
        txt_path = self.settings.podcasts_dir / f"{safe}.txt"
        txt_path.write_text(script, encoding="utf-8")
        return txt_path

    def pending_notebooklm_packs(
        self,
        conn: sqlite3.Connection,
        limit: int = 3,
        force: bool = False,
        min_retry_minutes: int = 120,
    ) -> list[dict[str, Any]]:
        fetch_limit = max(limit * 8, limit, 20)
        rows = rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM learning_packs
                WHERE COALESCE(script_text, '') != ''
                  AND (notebooklm_audio_path IS NULL OR notebooklm_audio_path = '')
                  AND (
                    notebooklm_status IS NULL
                    OR notebooklm_status IN ('not_requested', 'auth_required', 'pending', 'syncing')
                    OR notebooklm_status LIKE 'failed%'
                  )
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (fetch_limit,),
            ).fetchall()
        )
        if force:
            return rows[:limit]

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=min_retry_minutes)
        due: list[dict[str, Any]] = []
        for row in rows:
            status = row.get("notebooklm_status") or "not_requested"
            if status == "not_requested" or _notebooklm_retry_due(row, cutoff):
                due.append(row)
            if len(due) >= limit:
                break
        return due

    def sync_notebooklm_pack(
        self,
        conn: sqlite3.Connection,
        pack: dict[str, Any],
        auth_checked: bool = False,
    ) -> dict[str, Any]:
        if not auth_checked:
            auth_ok, auth_message = self.check_notebooklm_auth()
            if not auth_ok:
                self._update_notebooklm_result(conn, pack, "auth_required", None, auth_message)
                conn.commit()
                return {"id": pack["id"], "title": pack["title"], "status": "auth_required", "message": auth_message}

        self._update_notebooklm_result(conn, pack, "syncing", None, "NotebookLM sync started")
        conn.commit()
        source_text = self.source_text_for_pack(conn, pack)
        status, audio_path = self.generate_notebooklm_audio(
            int(pack["id"]),
            pack["title"],
            pack["script_text"],
            check_auth=False,
            source_text=source_text,
        )
        self._update_notebooklm_result(conn, pack, status, audio_path, status)
        conn.commit()
        return {
            "id": pack["id"],
            "title": pack["title"],
            "status": status,
            "audio_path": str(audio_path) if audio_path else None,
        }

    def sync_pending_notebooklm(
        self,
        conn: sqlite3.Connection,
        limit: int = 3,
        force: bool = False,
    ) -> dict[str, Any]:
        packs = self.pending_notebooklm_packs(conn, limit=limit, force=force)
        result: dict[str, Any] = {
            "pending": len(packs),
            "attempted": 0,
            "updated": 0,
            "ready": 0,
            "auth_required": False,
            "failed": [],
            "packs": [],
        }
        if not packs:
            result["message"] = "no pending NotebookLM packs"
            return result

        auth_ok, auth_message = self.check_notebooklm_auth()
        result["auth_message"] = auth_message
        if not auth_ok:
            result["auth_required"] = True
            for pack in packs:
                self._update_notebooklm_result(conn, pack, "auth_required", None, auth_message)
                result["updated"] += 1
                result["packs"].append({"id": pack["id"], "title": pack["title"], "status": "auth_required"})
            result["message"] = "NotebookLM login required"
            return result

        for pack in packs:
            result["attempted"] += 1
            pack_result = self.sync_notebooklm_pack(conn, pack, auth_checked=True)
            status = str(pack_result.get("status") or "")
            result["updated"] += 1
            if status == NOTEBOOKLM_READY_STATUS:
                result["ready"] += 1
            elif status.startswith("failed") or status == "auth_required":
                result["failed"].append({"id": pack["id"], "title": pack["title"], "status": status})
            result["packs"].append({"id": pack["id"], "title": pack["title"], "status": status})

        result["message"] = f"synced {result['ready']} NotebookLM audio packs"
        return result

    def check_notebooklm_auth(self) -> tuple[bool, str]:
        try:
            check = subprocess.run(
                self.notebooklm_cmd("auth", "check", "--test", "--json"),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=40,
            )
        except FileNotFoundError:
            return False, "notebooklm command not found"
        except Exception as exc:
            return False, str(exc)

        output = (check.stdout or check.stderr or "").strip()
        if check.returncode != 0:
            return False, output[:240] or f"auth check failed with code {check.returncode}"
        try:
            payload = json.loads(check.stdout or "{}")
            if payload.get("status") == "ok":
                return True, "ok"
            return False, output[:240] or "NotebookLM auth is not ok"
        except json.JSONDecodeError:
            return ('"status": "ok"' in check.stdout), output[:240] or "NotebookLM auth check completed"

    def _update_notebooklm_result(
        self,
        conn: sqlite3.Connection,
        pack: dict[str, Any],
        status: str,
        audio_path: Path | None,
        message: str,
    ) -> None:
        provenance = json_loads(pack.get("provenance_json"), {})
        if not isinstance(provenance, dict):
            provenance = {}
        previous_sync = provenance.get("notebooklm_sync") if isinstance(provenance.get("notebooklm_sync"), dict) else {}
        provenance["notebooklm_sync"] = {
            **previous_sync,
            "last_attempt_at": utc_now(),
            "last_status": status,
            "last_error": "" if status == NOTEBOOKLM_READY_STATUS else message[:300],
        }
        conn.execute(
            """
            UPDATE learning_packs
            SET notebooklm_status=?,
                notebooklm_audio_path=COALESCE(?, notebooklm_audio_path),
                provenance_json=?,
                updated_at=?
            WHERE id=?
            """,
            (
                status,
                str(audio_path) if audio_path else None,
                json_dumps(provenance),
                utc_now(),
                int(pack["id"]),
            ),
        )

    def generate_notebooklm_audio(
        self,
        pack_id: int,
        title: str,
        script: str,
        check_auth: bool = True,
        source_text: str | None = None,
    ) -> tuple[str, Path | None]:
        safe = _safe_filename(f"{pack_id}-{title}")[:80]
        source_path = self.settings.podcasts_dir / f"{safe}-notebooklm-source.md"
        audio_path = self.settings.podcasts_dir / f"{safe}-notebooklm.mp3"
        source_path.write_text(source_text or f"# {title}\n\n{script}", encoding="utf-8")
        try:
            if check_auth:
                auth_ok, _ = self.check_notebooklm_auth()
                if not auth_ok:
                    return "auth_required", None

            notebook_title = f"当前项目 {title}"
            return self._generate_notebooklm_audio_from_source(self.notebooklm_cmd(), notebook_title, source_path, audio_path)
            commands = [
                (self.notebooklm_cmd("--quiet", "create", "--use", notebook_title), 300),
                (self.notebooklm_cmd("--quiet", "source", "add", "--type", "file", "--title", title, "--timeout", "120", str(source_path)), 300),
                (
                    [
                        *self.notebooklm_cmd(),
                        "--quiet",
                        "generate",
                        "audio",
                        "--language",
                        "zh_Hans",
                        "--wait",
                        "--timeout",
                        "1200",
                        "输出中文播客，重点讲清楚创始人下一步该问什么",
                    ],
                    1300,
                ),
                (self.notebooklm_cmd("--quiet", "download", "audio", "--force", str(audio_path)), 300),
            ]
            for cmd, timeout in commands:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                )
                if proc.returncode != 0:
                    return f"failed: {(proc.stderr or proc.stdout).strip()[:200]}", None
            return "notebooklm_audio_ready", audio_path if audio_path.exists() else None
        except Exception as exc:
            return f"failed: {exc}", None

    def _generate_notebooklm_audio_from_source(
        self,
        base_cmd: list[str],
        notebook_title: str,
        source_path: Path,
        audio_path: Path,
    ) -> tuple[str, Path | None]:
        proc = _run_notebooklm([*base_cmd, "--quiet", "create", "--use", notebook_title], 300)
        if proc.returncode != 0:
            return _notebooklm_status_from_failure(proc), None

        proc = _run_notebooklm(
            [
                *base_cmd,
                "--quiet",
                "source",
                "add",
                "--type",
                "file",
                "--title",
                source_path.name,
                "--timeout",
                "120",
                str(source_path),
            ],
            300,
        )
        if proc.returncode != 0:
            return _notebooklm_status_from_failure(proc), None

        audio_prompt = (
            "请生成中文 Audio Overview。重点帮助 用户理解这个新知识："
            "先解释为什么重要，再讲核心概念、证据、风险、项目影响和下一轮追问清单。"
            "不要照读资料，不要泛泛总结。"
        )
        proc = _run_notebooklm(
            [
                *base_cmd,
                "--quiet",
                "generate",
                "audio",
                "--language",
                "zh_Hans",
                "--format",
                "deep-dive",
                "--length",
                "default",
                "--wait",
                "--timeout",
                "1800",
                "--retry",
                "2",
                "--json",
                audio_prompt,
            ],
            1900,
            attempts=2,
        )
        if proc.returncode != 0:
            return _notebooklm_status_from_failure(proc), None

        artifact_id = _extract_audio_artifact_id(proc.stdout)
        if not artifact_id:
            proc = _run_notebooklm([*base_cmd, "--quiet", "artifact", "list", "--type", "audio", "--json"], 120)
            if proc.returncode != 0:
                return _notebooklm_status_from_failure(proc), None
            artifact_id = _extract_audio_artifact_id(proc.stdout)
        if not artifact_id:
            return "failed: NotebookLM did not return an audio artifact id", None

        proc = _run_notebooklm(
            [*base_cmd, "--quiet", "download", "audio", "--artifact", artifact_id, "--force", str(audio_path)],
            300,
            attempts=2,
        )
        if proc.returncode != 0:
            return _notebooklm_status_from_failure(proc), None

        return NOTEBOOKLM_READY_STATUS, audio_path if audio_path.exists() else None


def list_learning_packs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = rows_to_dicts(conn.execute("SELECT * FROM learning_packs ORDER BY created_at DESC LIMIT 100").fetchall())
    settings = SettingsLike.from_env()
    for row in rows:
        row["knowledge_item_ids"] = json_loads(row.get("knowledge_item_ids_json"), [])
        enrich_learning_pack(row, settings)
    return rows


class SettingsLike:
    def __init__(self, podcasts_dir: Path) -> None:
        self.podcasts_dir = podcasts_dir

    @classmethod
    def from_env(cls) -> "SettingsLike":
        from ..config import get_settings

        return cls(get_settings().podcasts_dir)


def enrich_learning_pack(row: dict[str, Any], settings: Settings | SettingsLike) -> dict[str, Any]:
    row["knowledge_item_ids"] = json_loads(row.get("knowledge_item_ids_json"), [])
    row["local_audio_url"] = _media_url(row.get("local_audio_path"), settings.podcasts_dir)
    row["notebooklm_audio_url"] = _media_url(row.get("notebooklm_audio_path"), settings.podcasts_dir)
    row["blog_url"] = _media_url(row.get("blog_path"), settings.podcasts_dir)
    row["source_manifest_url"] = _source_manifest_url(row, settings.podcasts_dir)
    row["script_url"] = _script_url(row, settings.podcasts_dir)
    return row


def _source_manifest_url(row: dict[str, Any], podcasts_dir: Path) -> str | None:
    manifest = row.get("source_manifest")
    if isinstance(manifest, dict) and manifest.get("manifest_path"):
        return _media_url(str(manifest.get("manifest_path")), podcasts_dir)
    manifest_json = row.get("source_manifest_json")
    if isinstance(manifest_json, str):
        parsed = json_loads(manifest_json, {})
        if isinstance(parsed, dict) and parsed.get("manifest_path"):
            return _media_url(str(parsed.get("manifest_path")), podcasts_dir)
    return None


def _script_url(row: dict[str, Any], podcasts_dir: Path) -> str | None:
    local_audio = row.get("local_audio_path")
    if local_audio:
        txt_path = Path(local_audio).with_suffix(".txt")
        url = _media_url(str(txt_path), podcasts_dir)
        if url:
            return url
    title = row.get("title") or ""
    pack_id = row.get("id")
    if pack_id:
        safe = _safe_filename(f"{pack_id}-{title}")[:90]
        url = _media_url(str(podcasts_dir / f"{safe}.txt"), podcasts_dir)
        if url:
            return url
        safe = _safe_filename(f"{pack_id}-{title}")[:80]
        return _media_url(str(podcasts_dir / f"{safe}.txt"), podcasts_dir)
    return None


def _media_url(path_value: Any, podcasts_dir: Path) -> str | None:
    if not path_value:
        return None
    try:
        path = Path(str(path_value)).resolve()
        root = podcasts_dir.resolve()
        relative = path.relative_to(root)
    except (OSError, ValueError):
        return None
    if not path.exists() or not path.is_file():
        return None
    return "/media/podcasts/" + "/".join(quote(part) for part in relative.parts)


def _safe_filename(value: str) -> str:
    value = re.sub(r"[^\w\-.一-龥]+", "_", value, flags=re.U)
    return value.strip("._") or "podcast"


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


def _compact(value: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", value or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


def _run_notebooklm(cmd: list[str], timeout: int, attempts: int = 1) -> subprocess.CompletedProcess[str]:
    last: subprocess.CompletedProcess[str] | None = None
    for attempt in range(max(1, attempts)):
        try:
            last = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _decode_timeout_stream(exc.stdout)
            stderr = _decode_timeout_stream(exc.stderr) or f"timeout after {timeout}s"
            last = subprocess.CompletedProcess(cmd, 124, stdout=stdout, stderr=stderr)
        if last.returncode == 0 or not _notebooklm_transient_failure(last):
            return last
        if attempt < attempts - 1:
            time.sleep(2 * (attempt + 1))
    return last


def _notebooklm_failed(proc: subprocess.CompletedProcess[str]) -> str:
    output = (proc.stderr or proc.stdout or "").strip()
    return f"failed: {output[:300] or f'exit code {proc.returncode}'}"


def _decode_timeout_stream(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _notebooklm_status_from_failure(proc: subprocess.CompletedProcess[str]) -> str:
    output = (proc.stderr or proc.stdout or "").strip()
    if _notebooklm_auth_failure(output):
        return "auth_required"
    return _notebooklm_failed(proc)


def _notebooklm_auth_failure(output: str) -> bool:
    text = output.lower()
    return any(
        marker in text
        for marker in (
            "authentication expired",
            "auth expired",
            "auth_required",
            "login required",
            "re-authenticate",
            "accounts.google.com",
            "token fetch failed",
            "invalid cookie",
            "invalid credentials",
        )
    )


def _notebooklm_transient_failure(proc: subprocess.CompletedProcess[str]) -> bool:
    output = (proc.stderr or proc.stdout or "").lower()
    if _notebooklm_auth_failure(output):
        return False
    return any(
        marker in output
        for marker in (
            "rate limit",
            "429",
            "timeout",
            "timed out",
            "temporarily unavailable",
            "503",
            "502",
            "connection",
            "network",
        )
    )


def _extract_audio_artifact_id(output: str) -> str | None:
    try:
        payload = json.loads(output or "{}")
    except json.JSONDecodeError:
        return None
    return _find_audio_artifact_id(payload)


def _find_audio_artifact_id(value: Any) -> str | None:
    if isinstance(value, dict):
        artifacts = value.get("artifacts")
        if isinstance(artifacts, list):
            for artifact in artifacts:
                artifact_id = _find_audio_artifact_id(artifact)
                if artifact_id:
                    return artifact_id
        type_text = str(value.get("type_id") or value.get("type") or "").lower()
        if type_text in {"audio", "audio overview"} or "audio" in type_text:
            artifact_id = value.get("id") or value.get("artifact_id") or value.get("task_id")
            return str(artifact_id) if artifact_id else None
        for key in ("artifact_id", "artifactId", "task_id", "taskId", "id"):
            item = value.get(key)
            if isinstance(item, str) and item:
                return item
        for item in value.values():
            artifact_id = _find_audio_artifact_id(item)
            if artifact_id:
                return artifact_id
    if isinstance(value, list):
        for item in value:
            artifact_id = _find_audio_artifact_id(item)
            if artifact_id:
                return artifact_id
    return None


def _notebooklm_retry_due(row: dict[str, Any], cutoff: datetime) -> bool:
    provenance = json_loads(row.get("provenance_json"), {})
    if not isinstance(provenance, dict):
        return True
    sync_meta = provenance.get("notebooklm_sync")
    if not isinstance(sync_meta, dict):
        return True
    last_attempt = sync_meta.get("last_attempt_at")
    if not last_attempt:
        return True
    try:
        parsed = datetime.fromisoformat(str(last_attempt))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return parsed <= cutoff

