"""Image and cache path helpers for Lumos."""

from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone
from pathlib import Path


def _random_suffix(length: int = 6) -> str:
    return "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(length))


def generate_media_filename(ext: str = "png") -> str:
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y%m%d")
    return f"img_{date_str}_{_random_suffix()}.{ext}"


def save_media(media_dir: Path, data: bytes, ext: str = "png") -> str:
    media_dir.mkdir(parents=True, exist_ok=True)
    filename = generate_media_filename(ext)
    filepath = media_dir / filename
    filepath.write_bytes(data)
    return f"media/{filename}"


def generate_cache_paths(cache_dir: Path, item_id: str) -> dict[str, str]:
    return {
        "mhtml": f"cache/{item_id}.mhtml",
        "readable": f"cache/{item_id}.md",
    }


def save_cache(cache_dir: Path, item_id: str, mhtml: bytes | None = None, readable: str | None = None) -> dict[str, str | None]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, str | None] = {"mhtml": None, "readable": None}

    if mhtml is not None:
        mhtml_path = cache_dir / f"{item_id}.mhtml"
        mhtml_path.write_bytes(mhtml)
        result["mhtml"] = f"cache/{item_id}.mhtml"

    if readable is not None:
        md_path = cache_dir / f"{item_id}.md"
        md_path.write_text(readable, encoding="utf-8")
        result["readable"] = f"cache/{item_id}.md"

    return result


def delete_media(data_dir: Path, relative_path: str) -> bool:
    full = data_dir / relative_path
    if full.exists():
        full.unlink()
        return True
    return False
