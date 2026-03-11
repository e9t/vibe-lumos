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

# Known Chromium browser native messaging host directories.
# Each entry: (label, {platform: path_template})
_BROWSERS: dict[str, dict[str, str]] = {
    "chrome": {
        "Darwin": "~/Library/Application Support/Google/Chrome/NativeMessagingHosts",
        "Linux": "~/.config/google-chrome/NativeMessagingHosts",
    },
    "chromium": {
        "Darwin": "~/Library/Application Support/Chromium/NativeMessagingHosts",
        "Linux": "~/.config/chromium/NativeMessagingHosts",
    },
}


def _get_host_dirs(browsers: list[str] | None, host_dir: str | None) -> list[tuple[str, Path]]:
    """Return list of (label, path) for native messaging host directories to register."""
    system = platform.system()
    results: list[tuple[str, Path]] = []

    if host_dir:
        results.append(("custom", Path(host_dir).expanduser()))
        return results

    targets = browsers or ["chrome"]
    for name in targets:
        if name in _BROWSERS:
            template = _BROWSERS[name].get(system)
            if template:
                results.append((name, Path(template).expanduser()))
            else:
                rprint(f"[yellow]⚠[/yellow]  {name}: no known path for {system}")
        else:
            # Treat unknown browser name as a direct path
            results.append((name, Path(name).expanduser()))

    return results


@app.command()
def init(
    data_dir: Annotated[Optional[str], typer.Option("--data-dir", help="Data directory path")] = None,
    extension_id: Annotated[Optional[str], typer.Option("--extension-id", help="Chrome extension ID")] = None,
    browser: Annotated[Optional[list[str]], typer.Option("--browser", help="Browser(s) to register: chrome, chromium, or a custom path")] = None,
    host_dir: Annotated[Optional[str], typer.Option("--host-dir", help="Custom NativeMessagingHosts directory path")] = None,
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

    dirs = _get_host_dirs(browser, host_dir)
    if dirs:
        for label, dir_path in dirs:
            _register_native_host(dir_path, extension_id)
            rprint(f"[green]✅[/green] Native host registered ({label}): {dir_path}")
        if not extension_id:
            rprint("[yellow]⚠[/yellow]  Run again with --extension-id <ID> after loading the extension")

    rprint("[green]✅[/green] Ready!")


def _register_native_host(host_dir: Path, extension_id: Optional[str] = None) -> None:
    host_dir.mkdir(parents=True, exist_ok=True)

    host_path = shutil.which("lumos-host")
    if not host_path:
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
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    app()
