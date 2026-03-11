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

# Known Chromium browser native messaging host directories by platform.
_BROWSER_DIRS: dict[str, list[tuple[str, str]]] = {
    "Darwin": [
        ("chrome", "~/Library/Application Support/Google/Chrome/NativeMessagingHosts"),
        ("chrome-beta", "~/Library/Application Support/Google/Chrome Beta/NativeMessagingHosts"),
        ("chromium", "~/Library/Application Support/Chromium/NativeMessagingHosts"),
        ("brave", "~/Library/Application Support/BraveSoftware/Brave-Browser/NativeMessagingHosts"),
        ("edge", "~/Library/Application Support/Microsoft Edge/NativeMessagingHosts"),
        ("arc", "~/Library/Application Support/Arc/User Data/NativeMessagingHosts"),
    ],
    "Linux": [
        ("chrome", "~/.config/google-chrome/NativeMessagingHosts"),
        ("chrome-beta", "~/.config/google-chrome-beta/NativeMessagingHosts"),
        ("chromium", "~/.config/chromium/NativeMessagingHosts"),
        ("brave", "~/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts"),
        ("edge", "~/.config/microsoft-edge/NativeMessagingHosts"),
    ],
}


def _detect_browsers() -> list[tuple[str, Path]]:
    """Find Chromium browser config dirs that exist on this system."""
    system = platform.system()
    candidates = _BROWSER_DIRS.get(system, [])
    found: list[tuple[str, Path]] = []
    for label, template in candidates:
        path = Path(template).expanduser()
        # Check if the browser profile dir exists (parent of NativeMessagingHosts)
        if path.parent.exists():
            found.append((label, path))

    # Also scan for unknown Chromium-based browsers with NativeMessagingHosts dirs
    if system == "Darwin":
        app_support = Path("~/Library/Application Support").expanduser()
        if app_support.exists():
            known_parents = {p.parent for _, p in found}
            for d in app_support.iterdir():
                nmh = d / "NativeMessagingHosts"
                if nmh.exists() and d not in known_parents:
                    # Check if there's already a chromium-style manifest here
                    found.append((d.name, nmh))
    elif system == "Linux":
        config = Path("~/.config").expanduser()
        if config.exists():
            known_parents = {p.parent for _, p in found}
            for d in config.iterdir():
                nmh = d / "NativeMessagingHosts"
                if nmh.exists() and d not in known_parents:
                    found.append((d.name, nmh))

    return found


def _get_host_dirs(browsers: list[str] | None, host_dir: str | None) -> list[tuple[str, Path]]:
    """Return list of (label, path) for native messaging host directories to register."""
    system = platform.system()

    if host_dir:
        return [("custom", Path(host_dir).expanduser())]

    if browsers:
        results: list[tuple[str, Path]] = []
        all_dirs = dict(_BROWSER_DIRS.get(system, []))
        for name in browsers:
            if name in all_dirs:
                results.append((name, Path(all_dirs[name]).expanduser()))
            else:
                # Treat as direct path
                results.append((name, Path(name).expanduser()))
        return results

    # Default: auto-detect all installed browsers
    detected = _detect_browsers()
    if detected:
        return detected

    # Fallback: at least register for Chrome
    chrome_dirs = dict(_BROWSER_DIRS.get(system, []))
    if "chrome" in chrome_dirs:
        return [("chrome", Path(chrome_dirs["chrome"]).expanduser())]

    return []


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
    else:
        rprint("[yellow]⚠[/yellow]  No browsers detected. Use --host-dir to specify your browser's NativeMessagingHosts path")

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
