"""Lumos Initializer — for setting up the data directory and native host."""

from __future__ import annotations

import json
import platform
import shutil
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich import print as rprint

app = typer.Typer(
    name="lumos-init",
    help="Initialize Lumos data directory and native messaging host.",
    add_completion=False,
)

@app.command()
def init(
    data_dir: Annotated[Optional[str], typer.Option("--data-dir", help="Data directory path")] = None,
    extension_id: Annotated[Optional[str], typer.Option("--extension-id", help="Chrome extension ID")] = None,
):
    """Initialize Lumos and register the native messaging host."""
    from lumos.core.config import CONFIG_PATH, load_config, save_config

    config = load_config()
    if data_dir:
        config.data_dir = data_dir

    dd = config.get_data_dir()
    dd.mkdir(parents=True, exist_ok=True)
    (dd / "media").mkdir(parents=True, exist_ok=True)
    (dd / "cache").mkdir(parents=True, exist_ok=True)

    items_path = dd / "items.jsonl"
    if not items_path.exists():
        items_path.touch()

    save_config(config)
    rprint(f"[green]✅[/green] Data dir: {dd}")
    rprint(f"[green]✅[/green] Config: {CONFIG_PATH}")

    if platform.system() == "Darwin":
        _register_native_host(extension_id)
        rprint("[green]✅[/green] Native host registered")
        if not extension_id:
            rprint("[yellow]⚠[/yellow]  Run again with --extension-id <ID> after loading the extension in Chrome")

    rprint("[green]✅[/green] Ready!")


def _register_native_host(extension_id: Optional[str] = None) -> None:
    host_dir = Path(
        "~/Library/Application Support/Google/Chrome/NativeMessagingHosts"
    ).expanduser()
    host_dir.mkdir(parents=True, exist_ok=True)

    host_path = shutil.which("lumos-host")
    if not host_path:
        # In dev, lumos-host might not be in PATH, so find it relative to lumos-init
        host_path = str(Path(sys.argv[0]).parent / "lumos-host")


    allowed_origins = (
        [f"chrome-extension://{extension_id}/"] if extension_id else []
    )

    manifest = {
        "name": "com.lumos.host",
        "description": "Lumos Native Messaging Host",
        "path": str(host_path),
        "type": "stdio",
        "allowed_origins": allowed_origins,
    }
    manifest_path = host_dir / "com.lumos.host.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "
")


if __name__ == "__main__":
    app()
