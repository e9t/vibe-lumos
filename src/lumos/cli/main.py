"""Lumos CLI — main entry point."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Optional, List

import typer

try:
    from dotenv import load_dotenv
    load_dotenv(Path.home() / ".env")
except ImportError:
    pass


class _LumosGroup(typer.core.TyperGroup):
    def format_help(self, ctx, formatter):
        super().format_help(ctx, formatter)
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table

        tbl = Table(show_header=False, box=None, padding=(0, 1))
        tbl.add_column(style="bold", no_wrap=True)
        tbl.add_column()
        tbl.add_row("lumos-init", "Initialize Lumos and register the native messaging host.")
        tbl.add_row("lumos-import", "Import data from Kindle and Diigo.")
        tbl.add_row("lumos-update-priorities", "Apply priority-updates.jsonl to items.jsonl.")

        Console().print(Panel(tbl, title="External Commands", title_align="left", border_style="dim"))


app = typer.Typer(
    name="lumos",
    cls=_LumosGroup,
    help="A command-line tool for capturing and searching your digital knowledge.",
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings={"help_option_names": ["-h", "--help"], "allow_interspersed_args": True},
)


def _parse_sort(value: str) -> tuple[str, bool]:
    """Parse sort spec like 'date', 'date:asc', 'priority:desc'."""
    if ":" in value:
        field, direction = value.split(":", 1)
        return field, direction != "asc"
    return value, True


_COLOR_MAP: dict[str, str] = {
    "yellow": "bright_yellow",
    "blue": "dodger_blue2",
    "orange": "dark_orange",
    "purple": "medium_purple",
}

_LIGHT_BACKGROUNDS = {"yellow", "green", "bright_yellow"}


def _rich_color(name: str) -> str:
    """Map user-friendly color names to valid Rich color names."""
    return _COLOR_MAP.get(name, name)


def _hl_style(color: str) -> str:
    """Build highlight style — dark text on light backgrounds."""
    rc = _rich_color(color)
    fg = "black " if rc in _LIGHT_BACKGROUNDS or color in _LIGHT_BACKGROUNDS else ""
    return f"{fg}bold on {rc}"


def _parse_since(value: str) -> datetime:
    if value.endswith("d"):
        days = int(value[:-1])
        return datetime.now(timezone.utc) - timedelta(days=days)
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    query: Annotated[Optional[List[str]], typer.Argument(help="Search terms (quoted phrases kept together)")] = None,
    source: Annotated[Optional[str], typer.Option("--type", "-t", help="Source: web, kindle", show_default="all")] = None,
    begin: Annotated[Optional[str], typer.Option("--begin", help="Begin date (e.g. 7d, 2026-01-01)", show_default="none")] = None,
    end: Annotated[Optional[str], typer.Option("--end", help="End date", show_default="none")] = None,
    limit: Annotated[Optional[int], typer.Option("--limit", "-l", help="Items per page", show_default="from config")] = None,
    sort: Annotated[str, typer.Option("--sort", "-s", help="Sort field:dir — field: date,priority,title / dir: asc,desc (e.g. date:asc)", show_default="date:desc")] = "date",
    include: Annotated[Optional[str], typer.Option("--include", "-i", help="Search in: title,url,text,note,ocr", show_default="all")] = None,
    exclude: Annotated[Optional[str], typer.Option("--exclude", "-e", help="Exclude: title,url,text,note,ocr", show_default="none")] = None,
    case: Annotated[Optional[str], typer.Option("--case", "-c", help="Case: smart, sensitive, ignore", show_default="smart")] = "smart",
    match: Annotated[str, typer.Option("--match", "-m", help="Match mode: smart (LLM query expansion), exact", show_default="smart")] = "smart",
):
    """Personal knowledge capture tool."""
    if ctx.invoked_subcommand is None:
        from lumos.cli.interactive import run_tui
        from lumos.core.config import load_config

        config = load_config()
        sort_by, desc = _parse_sort(sort)
        lim = limit or config.list.default_limit
        since_dt = _parse_since(begin) if begin else None
        until_dt = _parse_since(end) if end else None
        all_fields = ["title", "url", "text", "note", "ocr"]
        if include:
            fields = include.split(",")
        elif exclude:
            excluded = set(exclude.split(","))
            fields = [f for f in all_fields if f not in excluded]
        else:
            fields = None

        # Build query string: phrases with spaces get quoted so shlex.split reproduces them
        if query:
            query_str = " ".join(
                f'"{q}"' if " " in q else q for q in query
            )
        else:
            query_str = ""

        # Smart match: expand query via LLM
        expanded = None
        if match == "smart" and query_str:
            from lumos.core.llm import expand_query
            expanded, llm_err = expand_query(query_str, config.models.llm)
            if llm_err:
                import sys
                from rich import print as rprint
                rprint(f"[red]⚠ {llm_err}[/red]", file=sys.stderr)

        run_tui(
            items_path=config.items_path(),
            data_dir=config.get_data_dir(),
            query=query_str,
            source=source,
            since=since_dt,
            until=until_dt,
            limit=lim,
            sort_by=sort_by,
            descending=desc,
            search_in=fields,
            case_sensitive=case == "sensitive",
            hl_style=_hl_style(config.theme.highlight_color),
            sel_style=f"reverse {_rich_color(config.theme.selection_color)}",
            expanded_terms=expanded,
            llm_config=config.models.llm if match == "smart" else None,
        )


if __name__ == "__main__":
    app()
