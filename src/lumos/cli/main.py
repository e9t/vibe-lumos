"""Lumos CLI — main entry point."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich import print as rprint
from rich.console import Console

from lumos.core.config import (
    CONFIG_PATH,
    LumosConfig,
    load_config,
    save_config,
    update_config,
)
from lumos.core.models import Item, ItemType, Source, SourceVia
from lumos.core.store import append_item, get_all

app = typer.Typer(
    name="lumos",
    help="Personal knowledge capture tool.",
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
)

console = Console()

# ── import subcommand ──────────────────────────────────────────────────────
from lumos.cli.import_cmd import import_app  # noqa: E402

app.add_typer(import_app, name="import", help="Import from external sources.")

# ── config subcommand ──────────────────────────────────────────────────────
config_app = typer.Typer(help="Manage configuration.")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show():
    """Show current configuration."""
    config = load_config()
    rprint(json.dumps(config.model_dump(), indent=2, ensure_ascii=False))


@config_app.command("set")
def config_set(key: str, value: str):
    """Set a config value (dot-separated key)."""
    parts = key.split(".")
    updates: dict = {}
    current = updates
    for part in parts[:-1]:
        current[part] = {}
        current = current[part]
    # Try to parse as JSON for booleans/numbers
    try:
        current[parts[-1]] = json.loads(value)
    except json.JSONDecodeError:
        current[parts[-1]] = value
    config = update_config(updates)
    rprint(f"[green]✓[/green] Set {key} = {value}")


@config_app.command("path")
def config_path():
    """Show config file path."""
    rprint(str(CONFIG_PATH))


@config_app.command("edit")
def config_edit():
    """Open config in $EDITOR."""
    editor = os.environ.get("EDITOR", "vim")
    if not CONFIG_PATH.exists():
        save_config(LumosConfig())
    subprocess.run([editor, str(CONFIG_PATH)])


# ── init ───────────────────────────────────────────────────────────────────
@app.command()
def init(
    data_dir: Annotated[
        Optional[str], typer.Option("--data-dir", help="Data directory path")
    ] = None,
):
    """Initialize Lumos."""
    config = load_config()
    if data_dir:
        config.data_dir = data_dir

    dd = config.get_data_dir()

    # Create directories
    dd.mkdir(parents=True, exist_ok=True)
    config.media_dir().mkdir(parents=True, exist_ok=True)
    config.cache_dir().mkdir(parents=True, exist_ok=True)

    # Create items.jsonl if missing
    items_path = config.items_path()
    if not items_path.exists():
        items_path.touch()

    # Save config
    save_config(config)
    rprint(f"[green]✅[/green] Created: {dd}/{{items.jsonl, media/, cache/}}")
    rprint(f"[green]✅[/green] Config: {CONFIG_PATH}")

    # Register Native Messaging Host (macOS)
    if platform.system() == "Darwin":
        _register_native_host()
        rprint("[green]✅[/green] Native Host registered")

    rprint("[green]✅[/green] Ready!")


def _register_native_host():
    host_dir = Path(
        "~/Library/Application Support/Google/Chrome/NativeMessagingHosts"
    ).expanduser()
    host_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = host_dir / "com.lumos.host.json"

    python_path = sys.executable
    manifest = {
        "name": "com.lumos.host",
        "description": "Lumos Native Messaging Host",
        "path": python_path,
        "type": "stdio",
        "allowed_origins": [],  # filled in when extension ID is known
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


# ── add ────────────────────────────────────────────────────────────────────
@app.command()
def add(
    url: str,
    title: Annotated[Optional[str], typer.Option("--title", "-t")] = None,
    text: Annotated[Optional[str], typer.Option("--text")] = None,
    note: Annotated[Optional[str], typer.Option("--note", "-n")] = None,
    item_type: Annotated[str, typer.Option("--type")] = "page",
):
    """Add an item manually."""
    config = load_config()
    resolved_title = title or url

    item = Item(
        type=ItemType(item_type),
        url=url,
        title=resolved_title,
        text=text,
        note=note,
        source=Source(via=SourceVia.WEB),
    )
    append_item(config.items_path(), item)
    rprint(f"[green]✓[/green] Added: {resolved_title}")


# ── ocr-retry ─────────────────────────────────────────────────────────────
@app.command("ocr-retry")
def ocr_retry():
    """Retry OCR for images missing ocr_text."""
    config = load_config()
    if not config.ocr.enabled:
        rprint("[yellow]OCR is disabled in config.[/yellow]")
        raise typer.Exit()

    items = get_all(config.items_path())
    pending = [
        item for item in items if item.type == ItemType.IMAGE and item.ocr_text is None
    ]

    if not pending:
        rprint("No images pending OCR.")
        raise typer.Exit()

    rprint(f"Found {len(pending)} images pending OCR.")
    # OCR processing would happen here when Upstage integration is wired up
    rprint("[yellow]OCR processing not yet implemented.[/yellow]")


# ── default: search / interactive ──────────────────────────────────────────
def _parse_since(value: str) -> datetime:
    if value.endswith("d"):
        days = int(value[:-1])
        return datetime.now(timezone.utc) - timedelta(days=days)
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """Personal knowledge capture tool."""
    if ctx.invoked_subcommand is None:
        _run_search()


def _run_search(
    query: str = "",
    source: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
    sort: str = "date",
    desc: bool = True,
    search_in: str | None = None,
    case_sensitive: bool = False,
):
    from lumos.cli.interactive import run_tui

    config = load_config()
    lim = limit or config.list.default_limit
    since_dt = _parse_since(since) if since else None
    until_dt = _parse_since(until) if until else None
    fields = search_in.split(",") if search_in else None

    run_tui(
        items_path=config.items_path(),
        data_dir=config.get_data_dir(),
        query=query,
        source=source,
        since=since_dt,
        until=until_dt,
        limit=lim,
        sort_by=sort,
        descending=desc,
        search_in=fields,
        case_sensitive=case_sensitive,
    )


@app.command("search")
def search_cmd(
    query: Annotated[Optional[str], typer.Argument(help="Search query")] = None,
    source: Annotated[Optional[str], typer.Option("--source")] = None,
    since: Annotated[Optional[str], typer.Option("--since")] = None,
    until: Annotated[Optional[str], typer.Option("--until")] = None,
    limit: Annotated[Optional[int], typer.Option("--limit")] = None,
    sort: Annotated[str, typer.Option("--sort")] = "date",
    desc: Annotated[bool, typer.Option("--desc/--asc")] = True,
    search_in: Annotated[Optional[str], typer.Option("--in")] = None,
    case_sensitive: Annotated[bool, typer.Option("--case-sensitive")] = False,
):
    """Search and browse items interactively."""
    _run_search(
        query=query or "",
        source=source,
        since=since,
        until=until,
        limit=limit,
        sort=sort,
        desc=desc,
        search_in=search_in,
        case_sensitive=case_sensitive,
    )


if __name__ == "__main__":
    app()
