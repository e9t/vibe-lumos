"""OCR via Upstage Document Digitization API."""

from __future__ import annotations

import os
from pathlib import Path

from lumos.core.config import OcrConfig

API_URL = "https://api.upstage.ai/v1/document-digitization"


def extract_text(image_path: str, config: OcrConfig) -> tuple[str | None, str | None]:
    """Extract text from an image file. Returns (text, error_message)."""
    if not config.enabled:
        return None, None

    api_key = os.environ.get(config.api_key_env)
    if not api_key:
        return None, f"OCR API key not set (${config.api_key_env})."

    path = Path(image_path)
    if not path.exists():
        return None, f"Image file not found: {image_path}"

    import httpx

    for attempt in range(config.retry_max):
        try:
            with open(path, "rb") as f:
                resp = httpx.post(
                    API_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"document": (path.name, f, "application/octet-stream")},
                    data={"model": "ocr"},
                    timeout=60,
                )
            resp.raise_for_status()
            data = resp.json()
            pages = data.get("pages", [])
            text = "\n".join(p.get("text", "") for p in pages).strip()
            return text if text else None, None
        except Exception as e:
            if attempt == config.retry_max - 1:
                return None, f"OCR failed after {config.retry_max} attempts: {e}"
    return None, "OCR failed: unexpected error."
