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
    delete_item,
    get_by_id,
    get_by_ids,
    get_by_url,
    update_item,
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


def _touch_page(items_path: Path, url: str) -> None:
    """Update the parent PAGE's updated_at so it floats to the top."""
    from datetime import datetime, timezone
    for page in get_by_url(items_path, url):
        if page.type == ItemType.PAGE:
            update_item(
                items_path, page.id,
                lambda it: it.model_copy(update={"updated_at": datetime.now(timezone.utc)}),
            )
            break


def _touch_page_by_item_id(items_path: Path, item_id: str) -> None:
    """Touch parent PAGE given a child item's ID."""
    item = get_by_id(items_path, item_id)
    if item and item.url:
        _touch_page(items_path, item.url)


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
        _touch_page(items_path, message["url"])
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
        # Run OCR on the saved image
        from lumos.core.ocr import extract_text
        ocr_text, ocr_err = extract_text(media_path, config.models.ocr)
        if ocr_text:
            item.ocr_text = ocr_text

        saved = append_item(items_path, item)
        _touch_page(items_path, message["url"])
        result = {"ok": True, "item": json.loads(saved.model_dump_json())}
        if ocr_err:
            result["ocr_error"] = ocr_err
        return result

    elif action == "save_page":
        import base64

        # Check for existing PAGE with same URL (upsert)
        existing_pages = [
            i for i in get_by_url(items_path, message["url"])
            if i.type == ItemType.PAGE
        ]

        # Build cache
        cache_formats = config.cache.formats
        mhtml_data = None
        readable_text = None

        if "mhtml" in cache_formats and message.get("mhtml_data"):
            mhtml_data = base64.b64decode(message["mhtml_data"])
        if "readable" in cache_formats and message.get("readable_text"):
            readable_text = message["readable_text"]

        if existing_pages:
            # Update existing page
            page = existing_pages[0]
            updates: dict = {"title": message["title"]}

            if mhtml_data or readable_text:
                cache_result = save_cache(
                    config.cache_dir(), page.id, mhtml_data, readable_text
                )
                updates["cache"] = Cache(
                    mhtml=cache_result.get("mhtml"),
                    readable=cache_result.get("readable"),
                )

            saved = update_item(
                items_path, page.id,
                lambda it: it.model_copy(update=updates),
            )
        else:
            # Create new page
            item = Item(
                type=ItemType.PAGE,
                url=message["url"],
                title=message["title"],
                source=Source(via=SourceVia.WEB),
            )

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

    elif action == "check_url":
        url = message.get("url", "")
        items = get_by_url(items_path, url)
        ids = [i.id for i in items]
        return {"ok": True, "exists": len(ids) > 0, "ids": ids}

    elif action == "get_items_by_ids":
        ids = message.get("ids", [])
        items = get_by_ids(items_path, ids)
        return {
            "ok": True,
            "items": [json.loads(i.model_dump_json()) for i in items],
        }

    elif action == "delete_item":
        ok = delete_item(items_path, message["id"])
        return {"ok": ok}

    elif action == "update_note":
        note = message.get("note")
        updated = update_item(
            items_path, message["id"],
            lambda it: it.model_copy(update={"note": note}),
        )
        if updated:
            _touch_page_by_item_id(items_path, message["id"])
        return {"ok": updated is not None}

    elif action == "update_priority":
        delta = message.get("delta", 0)
        updated = update_item(
            items_path, message["id"],
            lambda it: it.model_copy(update={"priority": it.priority + delta}),
        )
        if updated:
            _touch_page_by_item_id(items_path, message["id"])
            return {"ok": True, "priority": updated.priority}
        return {"ok": False, "error": "Item not found"}

    elif action == "cache_page":
        import base64

        url = message.get("url", "")
        title = message.get("title", "")

        # Find or create PAGE item
        existing_pages = [
            i for i in get_by_url(items_path, url)
            if i.type == ItemType.PAGE
        ]

        if existing_pages:
            page = existing_pages[0]
        else:
            # Auto-create page entry if not saved yet
            page = Item(
                type=ItemType.PAGE,
                url=url,
                title=title,
                source=Source(via=SourceVia.WEB),
            )
            page = append_item(items_path, page)

        # Save cache files
        mhtml_data = None
        readable_text = None

        cache_formats = config.cache.formats
        if "mhtml" in cache_formats and message.get("mhtml_data"):
            mhtml_data = base64.b64decode(message["mhtml_data"])
        if "readable" in cache_formats and message.get("readable_text"):
            readable_text = message["readable_text"]

        if not mhtml_data and not readable_text:
            return _error("No cache data provided")

        cache_result = save_cache(
            config.cache_dir(), page.id, mhtml_data, readable_text
        )
        saved = update_item(
            items_path, page.id,
            lambda it: it.model_copy(update={
                "title": title or it.title,
                "cache": Cache(
                    mhtml=cache_result.get("mhtml"),
                    readable=cache_result.get("readable"),
                ),
            }),
        )
        return {"ok": True, "item": json.loads(saved.model_dump_json())}

    elif action == "get_cache":
        url = message.get("url", "")
        pages = [i for i in get_by_url(items_path, url) if i.type == ItemType.PAGE]
        if not pages:
            return {"ok": False, "error": "Page not found"}
        page = pages[0]
        if not page.cache or not page.cache.readable:
            return {"ok": False, "error": "No cache available"}
        data_dir = config.get_data_dir()
        cache_path = data_dir / page.cache.readable
        if not cache_path.exists():
            return {"ok": False, "error": "Cache file missing"}
        text = cache_path.read_text(encoding="utf-8")
        return {"ok": True, "text": text, "title": page.title}

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
