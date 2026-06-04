from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


WORKBENCH_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = WORKBENCH_ROOT.parent
ENV_PATH = WORKBENCH_ROOT / ".env"
WRITABLE_ENV_KEYS = (
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_EMBEDDING_MODEL",
    "NOTEBOOKLM_PROFILE",
    "TTS_PROVIDER",
    "OCR_PROVIDER",
    "OCR_LANG",
    "TESSERACT_BIN",
    "WX_POLL_SECONDS",
    "WX_BACKFILL_SESSION_LIMIT",
    "WX_BACKFILL_HISTORY_LIMIT",
    "WX_ATTACHMENT_SESSION_LIMIT",
    "WX_ATTACHMENT_LIMIT",
    "WX_FILE_SCAN_LIMIT",
    "DOCUMENT_MAX_MB",
    "AUTO_STARTUP_SYNC",
    "AUTO_DAILY_BRIEFING",
    "AUTO_DAILY_LEARNING_AUDIO",
    "WORKSPACE_ROOT",
    "WECHAT_FILES_ROOT",
)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(raw_line)
        if not parsed:
            continue
        key, value = parsed
        os.environ[key] = value


def update_env_file(updates: dict[str, str]) -> None:
    allowed = set(WRITABLE_ENV_KEYS)
    clean_updates = {key: str(value).strip() for key, value in updates.items() if key in allowed}
    if not clean_updates:
        return

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    seen: set[str] = set()
    next_lines: list[str] = []
    for raw_line in lines:
        parsed = _parse_env_line(raw_line)
        if not parsed:
            next_lines.append(raw_line)
            continue
        key, _ = parsed
        if key in clean_updates:
            next_lines.append(f"{key}={_format_env_value(clean_updates[key])}")
            seen.add(key)
        else:
            next_lines.append(raw_line)

    for key in WRITABLE_ENV_KEYS:
        if key in clean_updates and key not in seen:
            next_lines.append(f"{key}={_format_env_value(clean_updates[key])}")

    ENV_PATH.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")
    for key, value in clean_updates.items():
        os.environ[key] = value


def _parse_env_line(raw_line: str) -> tuple[str, str] | None:
    line = raw_line.strip().lstrip("\ufeff")
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, value = line.split("=", 1)
    key = key.strip()
    if not key:
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def _format_env_value(value: str) -> str:
    if value == "" or re.fullmatch(r"[^\s#'\"=]+", value):
        return value
    return json.dumps(value, ensure_ascii=False)


def _env_bool(key: str, default: bool) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(key: str, default: int, minimum: int = 0) -> int:
    value = os.getenv(key)
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


@dataclass(frozen=True)
class Settings:
    workbench_root: Path
    repo_root: Path
    data_dir: Path
    inbox_dir: Path
    attachments_dir: Path
    podcasts_dir: Path
    db_path: Path
    workspace_root: Path
    wechat_files_root: Path | None
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_embedding_model: str
    notebooklm_profile: str
    notebooklm_bin: Path
    tts_provider: str
    minimax_tts_model: str
    minimax_tts_voice: str
    ocr_provider: str
    ocr_lang: str
    tesseract_bin: str
    wx_poll_seconds: int
    wx_backfill_session_limit: int
    wx_backfill_history_limit: int
    wx_attachment_session_limit: int
    wx_attachment_limit: int
    wx_file_scan_limit: int
    document_max_mb: int
    auto_startup_sync: bool
    auto_daily_briefing: bool
    auto_daily_learning_audio: bool

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key and self.llm_base_url and self.llm_model)


def get_settings() -> Settings:
    _load_env_file(ENV_PATH)
    data_dir = Path(os.getenv("WORKBENCH_DATA_DIR", WORKBENCH_ROOT / "data")).resolve()
    inbox_dir = Path(os.getenv("WORKBENCH_INBOX_DIR", data_dir / "inbox")).resolve()
    attachments_dir = Path(os.getenv("WORKBENCH_ATTACHMENTS_DIR", data_dir / "attachments")).resolve()
    podcasts_dir = Path(os.getenv("WORKBENCH_PODCASTS_DIR", data_dir / "podcasts")).resolve()
    workspace_root = Path(os.getenv("WORKSPACE_ROOT", REPO_ROOT)).resolve()
    wechat_files_root = _detect_wechat_files_root()
    for folder in (data_dir, inbox_dir, attachments_dir, podcasts_dir):
        folder.mkdir(parents=True, exist_ok=True)

    return Settings(
        workbench_root=WORKBENCH_ROOT,
        repo_root=REPO_ROOT,
        data_dir=data_dir,
        inbox_dir=inbox_dir,
        attachments_dir=attachments_dir,
        podcasts_dir=podcasts_dir,
        db_path=Path(os.getenv("WORKBENCH_DB_PATH", data_dir / "workbench.sqlite3")).resolve(),
        workspace_root=workspace_root,
        wechat_files_root=wechat_files_root,
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        llm_embedding_model=os.getenv("LLM_EMBEDDING_MODEL", ""),
        notebooklm_profile=os.getenv("NOTEBOOKLM_PROFILE", "default"),
        notebooklm_bin=Path(
            os.getenv("NOTEBOOKLM_BIN", WORKBENCH_ROOT / ".venv" / "Scripts" / "notebooklm.exe")
        ).resolve(),
        tts_provider=os.getenv("TTS_PROVIDER", "edge"),
        minimax_tts_model=os.getenv("MINIMAX_TTS_MODEL", "speech-2.8-hd"),
        minimax_tts_voice=os.getenv("MINIMAX_TTS_VOICE", "audiobook_male_1"),
        ocr_provider=os.getenv("OCR_PROVIDER", "tesseract").strip().lower(),
        ocr_lang=os.getenv("OCR_LANG", "chi_sim+eng").strip() or "eng",
        tesseract_bin=os.getenv("TESSERACT_BIN", "tesseract").strip() or "tesseract",
        wx_poll_seconds=max(30, int(os.getenv("WX_POLL_SECONDS", "90"))),
        wx_backfill_session_limit=_env_int("WX_BACKFILL_SESSION_LIMIT", 0, minimum=0),
        wx_backfill_history_limit=_env_int("WX_BACKFILL_HISTORY_LIMIT", 0, minimum=0),
        wx_attachment_session_limit=_env_int("WX_ATTACHMENT_SESSION_LIMIT", 0, minimum=0),
        wx_attachment_limit=_env_int("WX_ATTACHMENT_LIMIT", 0, minimum=0),
        wx_file_scan_limit=_env_int("WX_FILE_SCAN_LIMIT", 0, minimum=0),
        document_max_mb=max(1, int(os.getenv("DOCUMENT_MAX_MB", "30"))),
        auto_startup_sync=_env_bool("AUTO_STARTUP_SYNC", True),
        auto_daily_briefing=_env_bool("AUTO_DAILY_BRIEFING", True),
        auto_daily_learning_audio=_env_bool("AUTO_DAILY_LEARNING_AUDIO", True),
    )


def _detect_wechat_files_root() -> Path | None:
    configured = os.getenv("WECHAT_FILES_ROOT")
    if configured:
        path = Path(configured).resolve()
        return path if path.exists() else None
    wx_config = Path.home() / ".wx-cli" / "config.json"
    if wx_config.exists():
        try:
            data = json.loads(wx_config.read_text(encoding="utf-8"))
            db_dir = Path(data.get("db_dir", "")).resolve()
            if db_dir.exists() and db_dir.name == "db_storage":
                return db_dir.parent
        except Exception:
            return None
    fallback = Path.home() / "Documents" / "xwechat_files"
    if fallback.exists():
        candidates = [p for p in fallback.iterdir() if p.is_dir() and p.name.startswith("wxid_")]
        if candidates:
            return max(candidates, key=lambda p: p.stat().st_mtime)
    return None

