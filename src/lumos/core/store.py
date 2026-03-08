"""JSONL store for Lumos items."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .models import Item, ItemType


def _read_all(path: Path) -> list[Item]:
    if not path.exists():
        return []
    items = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            items.append(Item.model_validate_json(line))
    return items


def _write_all(path: Path, items: list[Item]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            for item in items:
                f.write(item.model_dump_json() + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_item(path: Path, item: Item) -> Item:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(item.model_dump_json() + "\n")
    return item


def get_all(path: Path) -> list[Item]:
    return _read_all(path)


def get_by_id(path: Path, item_id: str) -> Optional[Item]:
    for item in _read_all(path):
        if item.id == item_id:
            return item
    return None


def get_by_ids(path: Path, item_ids: list[str]) -> list[Item]:
    id_set = set(item_ids)
    return [item for item in _read_all(path) if item.id in id_set]


def get_by_url(path: Path, url: str) -> list[Item]:
    return [item for item in _read_all(path) if item.url == url]


def update_item(path: Path, item_id: str, updater: Callable[[Item], Item]) -> Optional[Item]:
    items = _read_all(path)
    updated = None
    for i, item in enumerate(items):
        if item.id == item_id:
            result = updater(item)
            result.updated_at = datetime.now(timezone.utc)
            items[i] = result
            updated = result
            break
    if updated:
        _write_all(path, items)
    return updated


def delete_item(path: Path, item_id: str) -> bool:
    items = _read_all(path)
    new_items = [item for item in items if item.id != item_id]
    if len(new_items) == len(items):
        return False
    _write_all(path, new_items)
    return True


def delete_items(path: Path, item_ids: set[str]) -> int:
    items = _read_all(path)
    new_items = [item for item in items if item.id not in item_ids]
    removed = len(items) - len(new_items)
    if removed > 0:
        _write_all(path, new_items)
    return removed


def search(
    path: Path,
    query: str = "",
    source: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    search_in: Optional[list[str]] = None,
    case_sensitive: bool = False,
    sort_by: str = "date",
    descending: bool = True,
    limit: int = 10,
    offset: int = 0,
    expanded_terms: Optional[list[str]] = None,
) -> tuple[list[Item], int]:
    all_items = _read_all(path)

    # Filter by source
    if source:
        all_items = [item for item in all_items if item.source.via.value == source]

    # Filter by date range
    if since:
        all_items = [item for item in all_items if item.created_at >= since]
    if until:
        all_items = [item for item in all_items if item.created_at <= until]

    # Text search: find matching URLs (if any item matches, include its whole page group)
    if query or expanded_terms:
        fields = search_in or ["title", "url", "text", "note", "ocr"]
        import shlex
        try:
            terms = shlex.split(query.strip()) if query else []
        except ValueError:
            terms = query.strip().split() if query else []
        terms = [t if case_sensitive else t.lower() for t in terms]

        def _get_field(item: Item, field: str) -> str | None:
            if field == "title":
                return item.title
            elif field == "url":
                return item.url
            elif field == "text":
                return item.text
            elif field == "note":
                return item.note
            elif field == "ocr":
                return item.ocr_text
            return None

        def item_has_term(item: Item, term: str) -> bool:
            for field in fields:
                val = _get_field(item, field)
                if val:
                    check = val if case_sensitive else val.lower()
                    if term in check:
                        return True
            return False

        # Group items by URL
        from collections import defaultdict
        url_items: dict[str, list[Item]] = defaultdict(list)
        for item in all_items:
            url_items[item.url].append(item)

        matching_urls: set[str] = set()

        if expanded_terms:
            # Smart match: OR logic — any expanded term matches
            exp = [t if case_sensitive else t.lower() for t in expanded_terms]
            for url, items_in_url in url_items.items():
                if any(
                    item_has_term(item, term)
                    for term in exp
                    for item in items_in_url
                ):
                    matching_urls.add(url)
        else:
            # Exact match: AND logic — all terms must match across the group
            for url, items_in_url in url_items.items():
                all_terms_found = True
                for term in terms:
                    if not any(item_has_term(item, term) for item in items_in_url):
                        all_terms_found = False
                        break
                if all_terms_found:
                    matching_urls.add(url)

        all_items = [item for item in all_items if item.url in matching_urls]

    # Collect all pages and sort them
    pages = [item for item in all_items if item.type == ItemType.PAGE]

    if sort_by == "date":
        pages.sort(key=lambda x: x.created_at, reverse=descending)
    elif sort_by == "priority":
        pages.sort(key=lambda x: x.priority, reverse=descending)
    elif sort_by == "title":
        pages.sort(key=lambda x: x.title.lower(), reverse=descending)

    # Page-based pagination
    total_pages = len(pages)
    page_slice = pages[offset : offset + limit]
    page_urls = {p.url for p in page_slice}

    # Return sliced pages + all their children (highlights/images with same URL)
    # Maintain sort order: pages first (in slice order), then children
    result = list(page_slice)
    for item in all_items:
        if item.type != ItemType.PAGE and item.url in page_urls:
            result.append(item)

    return result, total_pages


def build_url_index(path: Path) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for item in _read_all(path):
        index.setdefault(item.url, []).append(item.id)
    return index
