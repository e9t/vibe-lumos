"""Lumos Importer — for Kindle and Diigo."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Annotated

import typer
from rich import print as rprint

from lumos.core.config import load_config
from lumos.core.models import Item, ItemType, Source, SourceVia
from lumos.core.store import append_item

app = typer.Typer(
    name="lumos-import",
    help="Import data from Kindle and Diigo.",
    add_completion=False,
    no_args_is_help=True,
)


# ── HTML stripping ─────────────────────────────────────────────────────────
class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str):
        self.parts.append(data)

    def get_text(self) -> str:
        return "".join(self.parts)


def _strip_html(html: str) -> str:
    s = _HTMLStripper()
    s.feed(html)
    return s.get_text()


# ── Diigo ──────────────────────────────────────────────────────────────────
def _parse_diigo_date(raw: str) -> datetime:
    """Parse '2026/03/06 02:50:18 +0000' → datetime."""
    # Remove timezone offset and parse, then set UTC
    clean = re.sub(r"\s*[+-]\d{4}$", "", raw)
    dt = datetime.strptime(clean, "%Y/%m/%d %H:%M:%S")
    return dt.replace(tzinfo=timezone.utc)


def _iter_jsonl(file: Path):
    """Iterate JSON objects from a JSONL file, handling entries that span multiple lines."""
    buf = ""
    for line in file.read_text().splitlines():
        line = line.strip()
        if not line and not buf:
            continue
        buf = buf + "\n" + line if buf else line
        try:
            yield json.loads(buf, strict=False)
            buf = ""
        except json.JSONDecodeError:
            continue
    if buf:
        yield json.loads(buf, strict=False)


@app.command("diigo")
def import_diigo(
    file: Annotated[Path, typer.Argument(help="Path to Diigo JSONL export")],
):
    """Import Diigo bookmarks/highlights."""
    config = load_config()
    items_path = config.items_path()

    if not file.exists():
        rprint(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)

    count = 0
    for entry in _iter_jsonl(file):
        url = entry.get("url", "")
        title = entry.get("title", url)
        created = entry.get("created_at")
        created_dt = _parse_diigo_date(created) if created else datetime.now(timezone.utc)

        annotations = entry.get("annotations", [])
        if annotations:
            # Create one page item + highlight items per annotation
            page_item = Item(
                type=ItemType.PAGE,
                url=url,
                title=title,
                source=Source(via=SourceVia.WEB),
                created_at=created_dt,
                updated_at=created_dt,
            )
            append_item(items_path, page_item)
            count += 1

            for ann in annotations:
                content = ann.get("content", "")
                original_html = content
                text = _strip_html(content)
                raw_comments = ann.get("comments", [])
                comments = [
                    c["content"] if isinstance(c, dict) else str(c)
                    for c in raw_comments
                ]
                note = " ".join(comments) if comments else None

                hl_item = Item(
                    type=ItemType.HIGHLIGHT,
                    url=url,
                    title=title,
                    text=text,
                    note=note,
                    source=Source(via=SourceVia.WEB, original_html=original_html),
                    created_at=created_dt,
                    updated_at=created_dt,
                )
                append_item(items_path, hl_item)
                count += 1
        else:
            # Page-only bookmark
            page_item = Item(
                type=ItemType.PAGE,
                url=url,
                title=title,
                source=Source(via=SourceVia.WEB),
                created_at=created_dt,
                updated_at=created_dt,
            )
            append_item(items_path, page_item)
            count += 1

    rprint(f"[green]✓[/green] Imported {count} items from Diigo.")


# ── Kindle ─────────────────────────────────────────────────────────────────
_KINDLE_SEPARATOR = "=========="
_KINDLE_META_RE = re.compile(
    r"- Your (?:Highlight|Note|Bookmark) on (?:page (\d+) \| )?Location (\d+(?:-\d+)?)"
)
_KINDLE_AUTHOR_RE = re.compile(r"\(([^)]+)\)\s*$")


def _parse_kindle_clippings(text: str) -> list[dict]:
    entries = []
    blocks = text.split(_KINDLE_SEPARATOR)
    for block in blocks:
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if len(lines) < 2:
            continue

        # Line 0: Book Title (Author)
        title_line = lines[0]
        author_match = _KINDLE_AUTHOR_RE.search(title_line)
        author = author_match.group(1) if author_match else None
        title = _KINDLE_AUTHOR_RE.sub("", title_line).strip()

        # Line 1: metadata
        meta_match = _KINDLE_META_RE.search(lines[1])
        page = int(meta_match.group(1)) if meta_match and meta_match.group(1) else None
        location = meta_match.group(2) if meta_match else None

        # Line 2+: highlighted text
        text_content = "\n".join(lines[2:]) if len(lines) > 2 else None

        if not text_content:
            continue

        entries.append({
            "title": title,
            "author": author,
            "page": page,
            "location": location,
            "text": text_content,
        })

    return entries


def _title_to_slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


@app.command("kindle")
def import_kindle(
    file: Annotated[Path, typer.Argument(help="Path to My Clippings.txt")],
):
    """Import Kindle highlights."""
    config = load_config()
    items_path = config.items_path()

    if not file.exists():
        rprint(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)

    raw = file.read_text(encoding="utf-8-sig")
    entries = _parse_kindle_clippings(raw)

    # Group by title to create page items
    seen_titles: set[str] = set()
    count = 0

    for entry in entries:
        title = entry["title"]
        url = f"kindle://book/{_title_to_slug(title)}"

        if title not in seen_titles:
            page_item = Item(
                type=ItemType.PAGE,
                url=url,
                title=title,
                source=Source(
                    via=SourceVia.KINDLE,
                    book=title,
                    author=entry["author"],
                ),
            )
            append_item(items_path, page_item)
            seen_titles.add(title)
            count += 1

        hl_item = Item(
            type=ItemType.HIGHLIGHT,
            url=url,
            title=title,
            text=entry["text"],
            source=Source(
                via=SourceVia.KINDLE,
                book=title,
                author=entry["author"],
                page=entry["page"],
                location=entry["location"],
            ),
        )
        append_item(items_path, hl_item)
        count += 1

    rprint(f"[green]✓[/green] Imported {count} items from Kindle.")


if __name__ == "__main__":
    app()
