"""Chrome Native Messaging Host for Lumos.

Protocol: Chrome sends/receives messages as length-prefixed JSON over stdin/stdout.
Each message: 4-byte little-endian length + JSON payload.

Usage: python -m lumos.native_host.host
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

from lumos.core.config import load_config
from lumos.core.media import save_cache, save_media
from lumos.core.models import (
    Cache,
    Item,
    ItemType,
    Location,
    Source,
    SourceVia,
)
from lumos.core.store import (
    append_item,
    build_url_index,
    get_by_ids,
)


def _read_message() -> dict | None:
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length or len(raw_length) < 4:
        return None
    length = struct.unpack("<I", raw_length)[0]
    data = sys.stdin.buffer.read(length)
    return json.loads(data)


def _send_message(msg: dict) -> None:
    encoded = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def _error(msg: str) -> dict:
    return {"ok": False, "error": msg}


def _handle(message: dict) -> dict:
    action = message.get("action", "")
    config = load_config()
    items_path = config.items_path()

    if action == "save_highlight":
        item = Item(
            type=ItemType.HIGHLIGHT,
            url=message["url"],
            title=message["title"],
            text=message["text"],
            note=message.get("note"),
            source=Source(
                via=SourceVia.WEB,
                original_html=message.get("original_html"),
            ),
            location=Location(
                xpath=message.get("xpath"),
                start_offset=message.get("start_offset"),
                end_offset=message.get("end_offset"),
                text_fingerprint=message.get("text_fingerprint"),
            ) if message.get("xpath") else None,
        )
        saved = append_item(items_path, item)
        return {"ok": True, "item": json.loads(saved.model_dump_json())}

    elif action == "save_image":
        import base64

        img_data = base64.b64decode(message["image_data"])
        ext = message.get("ext", "png")
        media_path = save_media(config.media_dir(), img_data, ext)

        item = Item(
            type=ItemType.IMAGE,
            url=message["url"],
            title=message["title"],
            media=media_path,
            note=message.get("note"),
            source=Source(via=SourceVia.WEB),
        )
        saved = append_item(items_path, item)
        # OCR would be triggered asynchronously here
        return {"ok": True, "item": json.loads(saved.model_dump_json())}

    elif action == "save_page":
        import base64

        item = Item(
            type=ItemType.PAGE,
            url=message["url"],
            title=message["title"],
            source=Source(via=SourceVia.WEB),
        )

        # Handle cache
        cache_mode = config.cache.mode
        mhtml_data = None
        readable_text = None

        if cache_mode in ("both", "mhtml") and "mhtml_data" in message:
            mhtml_data = base64.b64decode(message["mhtml_data"])
        if cache_mode in ("both", "readable") and "readable_text" in message:
            readable_text = message["readable_text"]

        if mhtml_data or readable_text:
            cache_result = save_cache(
                config.cache_dir(), item.id, mhtml_data, readable_text
            )
            item.cache = Cache(
                mhtml=cache_result.get("mhtml"),
                readable=cache_result.get("readable"),
            )

        saved = append_item(items_path, item)
        return {"ok": True, "item": json.loads(saved.model_dump_json())}

    elif action == "get_url_index":
        index = build_url_index(items_path)
        return {"ok": True, "index": index}

    elif action == "get_items_by_ids":
        ids = message.get("ids", [])
        items = get_by_ids(items_path, ids)
        return {
            "ok": True,
            "items": [json.loads(i.model_dump_json()) for i in items],
        }

    else:
        return _error(f"Unknown action: {action}")


def main():
    while True:
        message = _read_message()
        if message is None:
            break
        try:
            response = _handle(message)
        except Exception as e:
            response = _error(str(e))
        _send_message(response)


if __name__ == "__main__":
    main()
