from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


@dataclass(frozen=True)
class OcrResult:
    ok: bool
    text: str = ""
    provider: str = ""
    message: str = ""
    meta: dict[str, Any] | None = None


class OcrService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def health(self) -> dict[str, Any]:
        if self.settings.ocr_provider == "off":
            return {
                "installed": False,
                "ok": False,
                "provider": "off",
                "message": "OCR disabled",
            }
        if self.settings.ocr_provider != "tesseract":
            return {
                "installed": False,
                "ok": False,
                "provider": self.settings.ocr_provider,
                "message": f"Unsupported OCR_PROVIDER: {self.settings.ocr_provider}",
            }

        binary = self._tesseract_binary()
        if not binary:
            return {
                "installed": False,
                "ok": False,
                "provider": "tesseract",
                "bin": self.settings.tesseract_bin,
                "message": "tesseract not found",
            }
        try:
            proc = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            first_line = (proc.stdout or proc.stderr or "").splitlines()[0] if (proc.stdout or proc.stderr) else "tesseract"
            return {
                "installed": proc.returncode == 0,
                "ok": proc.returncode == 0,
                "provider": "tesseract",
                "bin": binary,
                "lang": self.settings.ocr_lang,
                "message": first_line,
            }
        except Exception as exc:
            return {
                "installed": False,
                "ok": False,
                "provider": "tesseract",
                "bin": binary,
                "message": str(exc),
            }

    def extract_text(self, path: Path, max_chars: int = 12_000) -> OcrResult:
        if not path.exists():
            return OcrResult(ok=False, provider=self.settings.ocr_provider, message="image not found")
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            return OcrResult(ok=False, provider=self.settings.ocr_provider, message="unsupported image type")
        if self.settings.ocr_provider != "tesseract":
            return OcrResult(ok=False, provider=self.settings.ocr_provider, message="OCR provider unavailable")
        binary = self._tesseract_binary()
        if not binary:
            return OcrResult(ok=False, provider="tesseract", message="tesseract not found")

        languages = [self.settings.ocr_lang]
        if self.settings.ocr_lang != "eng":
            languages.append("eng")
        last_error = ""
        for lang in languages:
            proc = subprocess.run(
                [binary, str(path), "stdout", "-l", lang],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45,
            )
            text = _clean_ocr_text(proc.stdout)
            if proc.returncode == 0 and text:
                return OcrResult(ok=True, text=text[:max_chars], provider="tesseract", message="ok", meta={"lang": lang})
            last_error = _clean_ocr_text(proc.stderr) or f"exit {proc.returncode}"
        return OcrResult(ok=False, provider="tesseract", message=last_error[:400], meta={"langs": languages})

    def _tesseract_binary(self) -> str | None:
        configured = self.settings.tesseract_bin
        if configured and Path(configured).exists():
            return configured
        return shutil.which(configured or "tesseract")


def append_ocr_text(body: str, ocr_text: str) -> str:
    clean = _clean_ocr_text(ocr_text)
    if not clean:
        return body
    if "[OCR]" in body and clean in body:
        return body
    return f"{body.rstrip()}\n\n[OCR]\n{clean}"


def _clean_ocr_text(text: str) -> str:
    lines = [" ".join(line.split()) for line in (text or "").splitlines()]
    return "\n".join(line for line in lines if line).strip()

