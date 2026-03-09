"""lumos-update-priorities — Apply priority-updates.jsonl to items.jsonl."""

from __future__ import annotations

import json

import typer

from lumos.core.config import load_config
from lumos.core.store import get_by_url, update_item

app = typer.Typer(
    name="lumos-update-priorities",
    help="Read priority-updates.jsonl, apply deltas to items.jsonl, then clear processed entries.",
    add_completion=False,
)


@app.callback(invoke_without_command=True)
def main():
    """Read priority-updates.jsonl, apply deltas to items.jsonl, then clear processed entries."""
    config = load_config()
    items_path = config.items_path()
    updates_path = config.get_data_dir() / "priority-updates.jsonl"

    if not updates_path.exists() or updates_path.stat().st_size == 0:
        return

    entries = []
    with open(updates_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not entries:
        return

    applied = 0
    for entry in entries:
        url = entry.get("url", "")
        delta = entry.get("delta", 0)
        item_id = entry.get("id", "")

        if not delta:
            continue

        if item_id:
            updated = update_item(
                items_path, item_id,
                lambda it, d=delta: it.model_copy(update={"priority": it.priority + d}),
            )
            if updated:
                applied += 1
        elif url:
            matches = get_by_url(items_path, url)
            for m in matches:
                updated = update_item(
                    items_path, m.id,
                    lambda it, d=delta: it.model_copy(update={"priority": it.priority + d}),
                )
                if updated:
                    applied += 1

    # Clear the file after processing
    updates_path.write_text("", encoding="utf-8")

    from rich import print as rprint
    rprint(f"[green]✓ Applied {applied} priority updates[/green]")
