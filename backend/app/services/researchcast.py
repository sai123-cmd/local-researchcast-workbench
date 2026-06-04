from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from ..config import Settings
from ..db import (
    content_hash,
    json_dumps,
    json_loads,
    log_external_call_finish,
    log_external_call_start,
    row_to_dict,
    rows_to_dicts,
    utc_now,
)


RESEARCHCAST_ENGINE = "researchcast"
RESEARCHCAST_READY_STATUS = "audio_ready"
RESEARCHCAST_PROCESSING_STATUS = "audio_processing"


class ResearchCastService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def create_learning_pack(
        self,
        conn: sqlite3.Connection,
        knowledge_item_ids: list[int],
        title: str | None = None,
        regenerate: bool = True,
    ) -> dict[str, Any]:
        if not knowledge_item_ids:
            raise ValueError("knowledge_item_ids cannot be empty")
        items = self._knowledge_items_by_id(conn, knowledge_item_ids)
        if not items:
            raise ValueError("No knowledge items found")
        pack_title = title or f"ResearchCast: {items[0].get('domain') or 'new knowledge'} / {str(items[0].get('title') or '')[:28]}"
        existing = row_to_dict(
            conn.execute(
                "SELECT * FROM learning_packs WHERE title=? AND engine=? ORDER BY id DESC LIMIT 1",
                (pack_title, RESEARCHCAST_ENGINE),
            ).fetchone()
        )
        if existing and not regenerate:
            return enrich_researchcast_pack(existing, self.settings)
        pack_id = self._upsert_pack(conn, existing, pack_title, [int(item["id"]) for item in items], source="knowledge_items")
        return await self._run_pipeline(conn, pack_id, items)

    async def create_today_learning_pack(
        self,
        conn: sqlite3.Connection,
        regenerate: bool = False,
        limit: int = 6,
    ) -> dict[str, Any]:
        day = datetime.now().date().isoformat()
        pack_title = f"ResearchCast 晨间学习包 {day}"
        existing = row_to_dict(
            conn.execute(
                "SELECT * FROM learning_packs WHERE title=? AND engine=? ORDER BY id DESC LIMIT 1",
                (pack_title, RESEARCHCAST_ENGINE),
            ).fetchone()
        )
        if existing and not regenerate:
            return enrich_researchcast_pack(existing, self.settings)
        items = self._select_recent_knowledge(conn, day, limit)
        if not items:
            raise ValueError("No knowledge items found for ResearchCast")
        pack_id = self._upsert_pack(conn, existing, pack_title, [int(item["id"]) for item in items], source="daily_knowledge")
        return await self._run_pipeline(conn, pack_id, items)

    def _upsert_pack(
        self,
        conn: sqlite3.Connection,
        existing: dict[str, Any] | None,
        title: str,
        item_ids: list[int],
        source: str,
    ) -> int:
        now = utc_now()
        if existing:
            pack_id = int(existing["id"])
            provenance = json_loads(existing.get("provenance_json"), {})
            if not isinstance(provenance, dict):
                provenance = {}
            provenance.update({"source": source, "regenerated": True, "engine": RESEARCHCAST_ENGINE})
            conn.execute(
                """
                UPDATE learning_packs
                SET status='queued',
                    knowledge_item_ids_json=?,
                    script_text='',
                    local_audio_path=NULL,
                    engine=?,
                    research_status='queued',
                    blog_path=NULL,
                    source_manifest_json='{}',
                    audio_status='queued',
                    tts_task_id=NULL,
                    audio_error='',
                    notebooklm_status='manual_only',
                    provenance_json=?,
                    updated_at=?
                WHERE id=?
                """,
                (json_dumps(item_ids), RESEARCHCAST_ENGINE, json_dumps(provenance), now, pack_id),
            )
            conn.commit()
            return pack_id

        cur = conn.execute(
            """
            INSERT INTO learning_packs(
                title, status, knowledge_item_ids_json, script_text,
                notebooklm_status, engine, research_status, source_manifest_json,
                audio_status, provenance_json, created_at, updated_at
            ) VALUES (?, 'queued', ?, '', 'manual_only', ?, 'queued', '{}', 'queued', ?, ?, ?)
            """,
            (
                title,
                json_dumps(item_ids),
                RESEARCHCAST_ENGINE,
                json_dumps({"source": source, "engine": RESEARCHCAST_ENGINE}),
                now,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)

    async def _run_pipeline(self, conn: sqlite3.Connection, pack_id: int, items: list[dict[str, Any]]) -> dict[str, Any]:
        self._update_pack(conn, pack_id, status="researching", research_status="researching")
        pack = row_to_dict(conn.execute("SELECT * FROM learning_packs WHERE id=?", (pack_id,)).fetchone()) or {}
        title = str(pack.get("title") or "ResearchCast")
        manifest = self.collect_sources(conn, items)
        manifest_path = self._write_json(pack_id, title, "sources", manifest)
        blog = await self.generate_research_blog(title, items, manifest)
        blog_path = self._write_text(pack_id, title, "blog", blog)
        self._update_pack(
            conn,
            pack_id,
            status="blog_ready",
            research_status="blog_ready",
            blog_path=str(blog_path),
            source_manifest_json=json_dumps({**manifest, "manifest_path": str(manifest_path)}),
        )
        script = await self.generate_audio_script(title, blog, manifest)
        self._write_text(pack_id, title, "script", script)
        self._update_pack(conn, pack_id, status="script_ready", script_text=script, audio_status="queued")

        pack = row_to_dict(conn.execute("SELECT * FROM learning_packs WHERE id=?", (pack_id,)).fetchone()) or {}
        status, audio_path, task_id, error = await self.synthesize_minimax_tts(pack, script, wait_seconds=75)
        self._record_audio_result(conn, pack_id, status, audio_path, task_id, error)
        return enrich_researchcast_pack(row_to_dict(conn.execute("SELECT * FROM learning_packs WHERE id=?", (pack_id,)).fetchone()) or {}, self.settings)

    def collect_sources(self, conn: sqlite3.Connection, items: list[dict[str, Any]]) -> dict[str, Any]:
        event_ids: list[int] = []
        for item in items:
            for event_id in _int_list(item.get("source_event_ids") or json_loads(item.get("source_event_ids_json"), [])):
                if event_id not in event_ids:
                    event_ids.append(event_id)

        source_events: list[dict[str, Any]] = []
        if event_ids:
            placeholders = ",".join("?" for _ in event_ids[:60])
            source_events = rows_to_dicts(
                conn.execute(
                    f"""
                    SELECT id, source, source_type, title, body, author, conversation, happened_at, received_at
                    FROM raw_events
                    WHERE id IN ({placeholders})
                    ORDER BY COALESCE(happened_at, received_at) DESC
                    """,
                    event_ids[:60],
                ).fetchall()
            )

        documents = rows_to_dicts(
            conn.execute(
                """
                SELECT id, path, title, kind, substr(text, 1, 1800) AS text, indexed_at
                FROM documents
                ORDER BY indexed_at DESC
                LIMIT 8
                """
            ).fetchall()
        )
        attachments = rows_to_dicts(
            conn.execute(
                """
                SELECT id, raw_event_id, path, kind, title, substr(text, 1, 1400) AS text, created_at
                FROM attachments
                WHERE COALESCE(text, '') != ''
                ORDER BY created_at DESC
                LIMIT 8
                """
            ).fetchall()
        )
        tasks = self._related_tasks(conn, event_ids)
        return {
            "generated_at": utc_now(),
            "source_policy": "local evidence first; external web research is disabled unless configured separately",
            "knowledge_items": [_compact_item(item) for item in items],
            "raw_events": [_compact_event(event) for event in source_events],
            "documents": documents,
            "attachments": attachments,
            "related_tasks": tasks,
            "web_research": {"enabled": False, "sources": [], "note": "No search API is configured; ResearchCast did not depend on NotebookLM."},
        }

    def _related_tasks(self, conn: sqlite3.Connection, event_ids: list[int]) -> list[dict[str, Any]]:
        if not event_ids:
            return []
        rows = rows_to_dicts(
            conn.execute(
                """
                SELECT id, title, summary, priority_label, next_action, source_event_ids_json, status
                FROM tasks
                ORDER BY priority_score DESC, updated_at DESC
                LIMIT 80
                """
            ).fetchall()
        )
        event_set = set(event_ids)
        result = []
        for row in rows:
            ids = set(_int_list(row.get("source_event_ids") or json_loads(row.get("source_event_ids_json"), [])))
            if ids & event_set:
                result.append(row)
            if len(result) >= 12:
                break
        return result

    async def generate_research_blog(self, title: str, items: list[dict[str, Any]], manifest: dict[str, Any]) -> str:
        fallback = _template_blog(title, items, manifest)
        if not self.settings.llm_configured:
            return fallback
        prompt = f"""
你是 用户的研究助理。请基于下面的本地证据，写一篇中文学习博客。

要求：
- 读者是 用户，目标是快速学懂新领域并决定下一步行动。
- 必须包含：一句话结论、为什么今天要学、核心概念、关键参数/规则、对当前项目的影响、供应商追问清单、证据不足与下一步补证。
- 不要泛泛科普；所有判断必须明确来自证据，证据不足就说不能下结论。
- 输出 Markdown，不要 JSON。

标题：{title}
知识点：{json.dumps(items, ensure_ascii=False)[:8000]}
来源材料：{json.dumps(manifest, ensure_ascii=False)[:18000]}
"""
        call_id = _audit_start(self.settings, "ResearchCast 研究博客生成", prompt, {"title": title, "items": items})
        try:
            async with httpx.AsyncClient(timeout=80) as client:
                response = await client.post(
                    f"{self.settings.llm_base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                    json={
                        "model": self.settings.llm_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.25,
                    },
                )
                response.raise_for_status()
                text = _strip_thinking(response.json()["choices"][0]["message"]["content"]).strip()
                log_external_call_finish(call_id, status="ok", response_summary=_compact(text, 360))
                return text or fallback
        except Exception as exc:
            log_external_call_finish(call_id, status="failed", error=_compact(str(exc), 360))
            return fallback

    async def generate_audio_script(self, title: str, blog: str, manifest: dict[str, Any]) -> str:
        fallback = _template_script(title, blog, manifest)
        if not self.settings.llm_configured:
            return fallback
        prompt = f"""
请把下面的研究博客改写成适合 8-12 分钟收听的中文单人讲解稿。

风格：
- 创始人速学课，清楚、具体、有判断。
- 不要机械播报标题，不要双人闲聊，不要口水话。
- 开头 20 秒说清楚为什么值得听。
- 每一段都要服务于 当前项目 下一步决策。
- 结尾输出 5 个可直接发给供应商/团队的问题。

标题：{title}
研究博客：
{blog[:18000]}

来源摘要：
{json.dumps(manifest, ensure_ascii=False)[:8000]}
"""
        call_id = _audit_start(self.settings, "ResearchCast 音频讲稿生成", prompt, {"title": title, "blog_chars": len(blog)})
        try:
            async with httpx.AsyncClient(timeout=80) as client:
                response = await client.post(
                    f"{self.settings.llm_base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                    json={
                        "model": self.settings.llm_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.35,
                    },
                )
                response.raise_for_status()
                text = _strip_thinking(response.json()["choices"][0]["message"]["content"]).strip()
                log_external_call_finish(call_id, status="ok", response_summary=_compact(text, 360))
                return text or fallback
        except Exception as exc:
            log_external_call_finish(call_id, status="failed", error=_compact(str(exc), 360))
            return fallback

    async def synthesize_minimax_tts(
        self,
        pack: dict[str, Any],
        script: str,
        wait_seconds: int = 90,
    ) -> tuple[str, Path | None, str | None, str]:
        if not self.settings.llm_api_key:
            path, error = await self._edge_tts(int(pack["id"]), str(pack.get("title") or "ResearchCast"), script)
            return ("audio_ready" if path else "audio_failed", path, None, error)

        task_id = (pack.get("tts_task_id") or "").strip()
        audio_path = self._pack_path(int(pack["id"]), str(pack.get("title") or "ResearchCast"), "mp3")
        try:
            if not task_id:
                task_id = await self._create_minimax_tts_task(script)
            deadline = time.monotonic() + max(1, wait_seconds)
            while time.monotonic() < deadline:
                status, file_id, error = await self._query_minimax_tts_task(task_id)
                if status == "success" and file_id:
                    await self._download_minimax_file(file_id, audio_path)
                    return "audio_ready", audio_path, task_id, ""
                if status == "failed":
                    fallback_path, fallback_error = await self._edge_tts(int(pack["id"]), str(pack.get("title") or "ResearchCast"), script)
                    if fallback_path:
                        return "audio_ready", fallback_path, task_id, f"MiniMax failed; Edge TTS fallback used. {error}"
                    return "audio_failed", None, task_id, error or fallback_error
                await asyncio.sleep(5)
            return RESEARCHCAST_PROCESSING_STATUS, None, task_id, ""
        except Exception as exc:
            fallback_path, fallback_error = await self._edge_tts(int(pack["id"]), str(pack.get("title") or "ResearchCast"), script)
            if fallback_path:
                return "audio_ready", fallback_path, task_id or None, f"MiniMax exception; Edge TTS fallback used. {exc}"
            return "audio_failed", None, task_id or None, f"{exc}; fallback failed: {fallback_error}"

    async def continue_pending_audio(self, conn: sqlite3.Connection, limit: int = 3, wait_seconds: int = 45) -> dict[str, Any]:
        rows = rows_to_dicts(
            conn.execute(
                """
                SELECT *
                FROM learning_packs
                WHERE engine=?
                  AND COALESCE(script_text, '') != ''
                  AND COALESCE(local_audio_path, '') = ''
                  AND audio_status IN ('queued', 'audio_processing', 'audio_failed')
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (RESEARCHCAST_ENGINE, limit),
            ).fetchall()
        )
        result = {"pending": len(rows), "attempted": 0, "ready": 0, "processing": 0, "failed": 0}
        for row in rows:
            result["attempted"] += 1
            status, audio_path, task_id, error = await self.synthesize_minimax_tts(row, row.get("script_text") or "", wait_seconds=wait_seconds)
            self._record_audio_result(conn, int(row["id"]), status, audio_path, task_id, error)
            if status == "audio_ready":
                result["ready"] += 1
            elif status == RESEARCHCAST_PROCESSING_STATUS:
                result["processing"] += 1
            else:
                result["failed"] += 1
        return result

    async def _create_minimax_tts_task(self, script: str) -> str:
        payload = {
            "model": self.settings.minimax_tts_model,
            "text": _compact(script, 95000),
            "language_boost": "Chinese",
            "voice_setting": {
                "voice_id": self.settings.minimax_tts_voice,
                "speed": 1.0,
                "vol": 10,
                "pitch": 0,
            },
            "audio_setting": {
                "audio_sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
        }
        call_id = log_external_call_start(
            provider="minimax",
            purpose="ResearchCast MiniMax TTS task create",
            model=self.settings.minimax_tts_model,
            base_url=self.settings.llm_base_url,
            prompt_chars=len(script),
            content_summary=_compact(script, 600),
            request_hash=content_hash("minimax_tts", script),
            provenance={"voice": self.settings.minimax_tts_voice},
        )
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{self.settings.llm_base_url}/t2a_async_v2",
                    headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
            task_id = _find_first(data, ("task_id", "taskId", "id"))
            if not task_id:
                raise RuntimeError(f"MiniMax did not return task_id: {_compact(json.dumps(data, ensure_ascii=False), 260)}")
            log_external_call_finish(call_id, status="ok", response_summary=f"task_id={task_id}")
            return str(task_id)
        except Exception as exc:
            log_external_call_finish(call_id, status="failed", error=_compact(str(exc), 360))
            raise

    async def _query_minimax_tts_task(self, task_id: str) -> tuple[str, str | None, str]:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.get(
                f"{self.settings.llm_base_url}/query/t2a_async_query_v2",
                headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                params={"task_id": task_id},
            )
            response.raise_for_status()
            data = response.json()
        status_text = str(_find_first(data, ("status", "state", "task_status", "taskStatus")) or "").lower()
        file_id = _find_first(data, ("file_id", "fileId", "audio_file_id", "audioFileId"))
        if status_text in {"success", "succeeded", "done", "finished", "completed"}:
            if file_id:
                return "success", str(file_id), ""
            return "failed", None, f"MiniMax task completed without file_id: {_compact(json.dumps(data, ensure_ascii=False), 320)}"
        if status_text in {"failed", "fail", "error"}:
            return "failed", None, _compact(json.dumps(data, ensure_ascii=False), 360)
        return "processing", None, ""

    async def _download_minimax_file(self, file_id: str, audio_path: Path) -> None:
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.get(
                f"{self.settings.llm_base_url}/files/retrieve_content",
                params={"file_id": file_id},
                headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
            )
            response.raise_for_status()
            audio_path.write_bytes(response.content)

    async def _edge_tts(self, pack_id: int, title: str, script: str) -> tuple[Path | None, str]:
        try:
            import edge_tts

            audio_path = self._pack_path(pack_id, title, "mp3")
            communicate = edge_tts.Communicate(script, "zh-CN-YunxiNeural")
            await communicate.save(str(audio_path))
            return audio_path, ""
        except Exception as exc:
            return None, str(exc)

    def _record_audio_result(
        self,
        conn: sqlite3.Connection,
        pack_id: int,
        status: str,
        audio_path: Path | None,
        task_id: str | None,
        error: str,
    ) -> None:
        status_value = "audio_ready" if status == "audio_ready" else ("audio_processing" if status == RESEARCHCAST_PROCESSING_STATUS else "audio_failed")
        pack_status = "audio_ready" if status_value == "audio_ready" else "script_ready"
        conn.execute(
            """
            UPDATE learning_packs
            SET status=?,
                audio_status=?,
                local_audio_path=COALESCE(?, local_audio_path),
                tts_task_id=COALESCE(?, tts_task_id),
                audio_error=?,
                updated_at=?
            WHERE id=?
            """,
            (pack_status, status_value, str(audio_path) if audio_path else None, task_id, error[:500], utc_now(), pack_id),
        )
        conn.commit()

    def _update_pack(self, conn: sqlite3.Connection, pack_id: int, **fields: Any) -> None:
        allowed = {
            "status",
            "script_text",
            "local_audio_path",
            "engine",
            "research_status",
            "blog_path",
            "source_manifest_json",
            "audio_status",
            "tts_task_id",
            "audio_error",
        }
        updates = [(key, value) for key, value in fields.items() if key in allowed]
        if not updates:
            return
        assignments = ", ".join(f"{key}=?" for key, _ in updates)
        values = [value for _, value in updates]
        values.extend([utc_now(), pack_id])
        conn.execute(f"UPDATE learning_packs SET {assignments}, updated_at=? WHERE id=?", values)
        conn.commit()

    def _knowledge_items_by_id(self, conn: sqlite3.Connection, ids: list[int]) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in ids)
        rows = rows_to_dicts(conn.execute(f"SELECT * FROM knowledge_items WHERE id IN ({placeholders})", ids).fetchall())
        by_id = {int(row["id"]): row for row in rows}
        return [by_id[item_id] for item_id in ids if item_id in by_id]

    def _select_recent_knowledge(self, conn: sqlite3.Connection, day: str, limit: int) -> list[dict[str, Any]]:
        rows = rows_to_dicts(
            conn.execute(
                """
                SELECT *
                FROM knowledge_items
                WHERE substr(created_at, 1, 10)=?
                ORDER BY CASE urgency WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                         novelty_score DESC, created_at DESC
                LIMIT ?
                """,
                (day, limit),
            ).fetchall()
        )
        if rows:
            return rows
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        rows = rows_to_dicts(
            conn.execute(
                """
                SELECT *
                FROM knowledge_items
                WHERE created_at >= ?
                ORDER BY CASE urgency WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                         novelty_score DESC, created_at DESC
                LIMIT ?
                """,
                (since, limit),
            ).fetchall()
        )
        if rows:
            return rows
        return rows_to_dicts(
            conn.execute(
                """
                SELECT *
                FROM knowledge_items
                ORDER BY CASE urgency WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                         novelty_score DESC, created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        )

    def _write_text(self, pack_id: int, title: str, suffix: str, text: str) -> Path:
        path = self._pack_path(pack_id, title, f"{suffix}.md" if suffix == "blog" else "txt")
        path.write_text(text, encoding="utf-8")
        return path

    def _write_json(self, pack_id: int, title: str, suffix: str, value: Any) -> Path:
        path = self._pack_path(pack_id, title, f"{suffix}.json")
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _pack_path(self, pack_id: int, title: str, suffix: str) -> Path:
        if not suffix.startswith("."):
            suffix = "." + suffix
        path = self.settings.podcasts_dir / f"researchcast-{pack_id}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


def enrich_researchcast_pack(row: dict[str, Any], settings: Settings) -> dict[str, Any]:
    row["knowledge_item_ids"] = json_loads(row.get("knowledge_item_ids_json"), [])
    row["local_audio_url"] = _media_url(row.get("local_audio_path"), settings.podcasts_dir)
    row["notebooklm_audio_url"] = _media_url(row.get("notebooklm_audio_path"), settings.podcasts_dir)
    row["blog_url"] = _media_url(row.get("blog_path"), settings.podcasts_dir)
    row["source_manifest_url"] = _manifest_url(row, settings.podcasts_dir)
    row["script_url"] = _script_url(row, settings.podcasts_dir)
    return row


def _media_url(path_value: Any, root: Path) -> str | None:
    if not path_value:
        return None
    try:
        path = Path(str(path_value)).resolve()
        root = root.resolve()
        relative = path.relative_to(root)
    except (OSError, ValueError):
        return None
    if not path.exists() or not path.is_file():
        return None
    return "/media/podcasts/" + "/".join(quote(part) for part in relative.parts)


def _manifest_url(row: dict[str, Any], root: Path) -> str | None:
    manifest = row.get("source_manifest")
    if isinstance(manifest, dict) and manifest.get("manifest_path"):
        return _media_url(manifest.get("manifest_path"), root)
    manifest_json = row.get("source_manifest_json")
    if isinstance(manifest_json, str):
        parsed = json_loads(manifest_json, {})
        if isinstance(parsed, dict) and parsed.get("manifest_path"):
            return _media_url(parsed.get("manifest_path"), root)
    return None


def _script_url(row: dict[str, Any], root: Path) -> str | None:
    local_audio = row.get("local_audio_path")
    if local_audio:
        url = _media_url(str(Path(str(local_audio)).with_suffix(".txt")), root)
        if url:
            return url
    pack_id = row.get("id")
    title = row.get("title") or ""
    if pack_id:
        url = _media_url(str(root / f"researchcast-{pack_id}.txt"), root)
        if url:
            return url
        safe = _safe_filename(f"{pack_id}-{title}")[:90]
        url = _media_url(str(root / f"{safe}.txt"), root)
        if url:
            return url
        safe = _safe_filename(f"{pack_id}-{title}")[:80]
        return _media_url(str(root / f"{safe}.txt"), root)
    return None


def _audit_start(settings: Settings, purpose: str, prompt: str, payload: Any) -> int | None:
    try:
        return log_external_call_start(
            provider="openai_compatible",
            purpose=purpose,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            prompt_chars=len(prompt),
            content_summary=_compact(prompt, 600),
            request_hash=content_hash(purpose, payload),
            provenance={"engine": RESEARCHCAST_ENGINE},
        )
    except Exception:
        return None


def _template_blog(title: str, items: list[dict[str, Any]], manifest: dict[str, Any]) -> str:
    lines = [
        f"# {title}",
        "",
        "## 一句话结论",
        "这是基于本地微信、文档和 OCR 证据生成的 ResearchCast 学习博客；证据不足的部分不会强行下结论。",
        "",
        "## 为什么今天要学",
        "这些知识点出现在 当前项目 当天工作流中，通常会影响硬件打样、供应商沟通、规格确认、合规或增长决策。",
        "",
        "## 核心知识点",
    ]
    for item in items:
        lines.extend(
            [
                f"### {item.get('title') or '未命名知识点'}",
                f"- 领域：{item.get('domain') or '其他'}",
                f"- 摘要：{item.get('summary') or ''}",
                f"- 项目关系：{item.get('work_relevance') or ''}",
                f"- 关键问题：{'; '.join(map(str, item.get('key_questions') or [])) or '待补充'}",
                f"- 下一步：{'; '.join(map(str, item.get('followups') or [])) or '待补充'}",
                "",
            ]
        )
    lines.extend(
        [
            "## 供应商/团队追问清单",
            "- 请补充规格书、报价、交期、认证文件和测试数据。",
            "- 请明确哪些参数是已验证结论，哪些只是口头描述。",
            "- 请给出可量产条件、风险点和替代方案。",
            "",
            "## 证据不足",
            f"当前本地证据包含 {len(manifest.get('raw_events') or [])} 条原始事件、{len(manifest.get('documents') or [])} 个文档摘要、{len(manifest.get('attachments') or [])} 个附件/OCR 摘要。没有直接证据覆盖的结论均应继续补证。",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _template_script(title: str, blog: str, manifest: dict[str, Any]) -> str:
    return (
        f"今天的 ResearchCast 主题是：{title}。\n\n"
        "先说结论：这份内容不是泛泛科普，而是从今天进入 你的工作流的新知识里，提取出你需要马上理解的概念、参数、风险和下一步问题。\n\n"
        + _compact(re.sub(r"#+\\s*", "", blog), 15000)
        + "\n\n最后，把行动收束成三件事：第一，补齐原始规格和报价证据；第二，问清供应商哪些参数已经验证；第三，把影响沉淀到 当前项目的产品、供应链或增长决策表里。"
    )


def _compact_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "domain": item.get("domain"),
        "summary": _compact(str(item.get("summary") or ""), 800),
        "work_relevance": _compact(str(item.get("work_relevance") or ""), 800),
        "urgency": item.get("urgency"),
        "novelty_score": item.get("novelty_score"),
        "key_questions": item.get("key_questions") or json_loads(item.get("key_questions_json"), []),
        "followups": item.get("followups") or json_loads(item.get("followups_json"), []),
        "source_event_ids": item.get("source_event_ids") or json_loads(item.get("source_event_ids_json"), []),
    }


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        **{key: event.get(key) for key in ("id", "source", "source_type", "title", "author", "conversation", "happened_at", "received_at")},
        "body": _compact(str(event.get("body") or ""), 2400),
    }


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", value)
    value = re.sub(r"\s+", "_", value).strip("._ ")
    return value or "researchcast"


def _strip_thinking(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I).strip()


def _compact(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


def _find_first(value: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        for key in keys:
            if value.get(key):
                return value[key]
        for item in value.values():
            found = _find_first(item, keys)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_first(item, keys)
            if found:
                return found
    return None

