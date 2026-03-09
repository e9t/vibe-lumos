"""Textual TUI for browsing Lumos items."""

from __future__ import annotations

import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rich.text import Text


def _wcswidth(s: str) -> int:
    """Display width — W/F count as 2 (fullwidth/wide CJK chars)."""
    width = 0
    for ch in s:
        cat = unicodedata.east_asian_width(ch)
        width += 2 if cat in ("W", "F") else 1
    return width


def _wctruncate(s: str, max_width: int) -> str:
    """Truncate string to fit within max_width display columns, adding … if needed."""
    ellipsis_w = _wcswidth("…")  # 2 on CJK terminals
    width = 0
    for i, ch in enumerate(s):
        cat = unicodedata.east_asian_width(ch)
        cw = 2 if cat in ("W", "F") else 1
        if width + cw > max_width - ellipsis_w:
            return s[:i] + "…"
        width += cw
    return s
def _wrap_text(text: str, max_width: int, continuation_indent: str = "") -> list[str]:
    """Wrap text into lines that fit within max_width display columns (word-based)."""
    if _wcswidth(text) <= max_width:
        return [text]
    # Preserve leading whitespace, split rest into words
    stripped = text.lstrip()
    leading = text[: len(text) - len(stripped)]
    words = stripped.split()
    if not words:
        return [text]
    result: list[str] = []
    current = leading + words[0]
    current_w = _wcswidth(current)
    indent_w = _wcswidth(continuation_indent)
    for word in words[1:]:
        word_w = _wcswidth(word)
        limit = max_width if not result else max_width - indent_w
        if current_w + 1 + word_w > limit:
            result.append(current)
            current = word
            current_w = word_w
        else:
            current += " " + word
            current_w += 1 + word_w
    if current:
        result.append(current)
    return [result[0]] + [continuation_indent + l for l in result[1:]]


from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Static


def _parse_query_terms(query: str) -> list[str]:
    """Parse query into terms: quoted phrases stay together, bare words split."""
    import shlex
    try:
        return shlex.split(query.strip())
    except ValueError:
        return query.strip().split()


def _highlight_append(target: Text, text: str, query: str, style: str = "", hl_style: str = "bold on yellow") -> None:
    """Append *text* to *target* Rich Text, highlighting all occurrences of query terms.

    Quoted phrases (e.g. '"upstage url"') are matched as-is.
    Bare words are matched independently.
    """
    if not query or not query.strip():
        target.append(text, style=style)
        return
    terms = _parse_query_terms(query)
    lower_text = text.lower()
    # Build a list of (start, end) highlight spans for all terms
    spans: list[tuple[int, int]] = []
    for term in terms:
        lt = term.lower()
        pos = 0
        while True:
            idx = lower_text.find(lt, pos)
            if idx < 0:
                break
            spans.append((idx, idx + len(lt)))
            pos = idx + len(lt)
    if not spans:
        target.append(text, style=style)
        return
    # Merge overlapping spans
    spans.sort()
    merged: list[tuple[int, int]] = [spans[0]]
    for s, e in spans[1:]:
        if s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    pos = 0
    for s, e in merged:
        if s > pos:
            target.append(text[pos:s], style=style)
        target.append(text[s:e], style=hl_style)
        pos = e
    if pos < len(text):
        target.append(text[pos:], style=style)


class FocusableStatic(Static, can_focus=True):
    pass

from lumos.core.models import Item, ItemType
from lumos.core.store import (
    delete_item,
    delete_items,
    get_by_url,
    search,
    update_item,
)


# ── Data grouping ──────────────────────────────────────────────────────────

class PageGroup:
    """A page item with its child highlights/images."""

    def __init__(self, page: Item, children: list[Item]):
        self.page = page
        self.children = children
        self.expanded = False
        self.visible_children = 0  # how many children are shown
        self.children_page_size = 10  # initial show count

    def toggle_expand(self):
        if self.expanded:
            self.expanded = False
            self.visible_children = 0
        else:
            self.expanded = True
            self.visible_children = min(self.children_page_size, len(self.children))

    def show_more(self, count: int = 10):
        self.visible_children = min(
            self.visible_children + count, len(self.children)
        )

    @property
    def has_more(self) -> bool:
        return self.expanded and self.visible_children < len(self.children)

    @property
    def remaining(self) -> int:
        return len(self.children) - self.visible_children


def group_items(items: list[Item]) -> list[PageGroup]:
    """Group items by URL: page items collect their highlights/images."""
    pages: dict[str, PageGroup] = {}
    orphan_children: dict[str, list[Item]] = {}

    for item in items:
        if item.type == ItemType.PAGE:
            if item.url not in pages:
                pages[item.url] = PageGroup(item, [])
        else:
            orphan_children.setdefault(item.url, []).append(item)

    # Attach children to pages
    for url, children in orphan_children.items():
        if url in pages:
            pages[url].children.extend(children)
        else:
            # Create a virtual page for orphan highlights
            first = children[0]
            virtual_page = Item(
                id=f"virtual_{first.id}",
                type=ItemType.PAGE,
                url=url,
                title=first.title,
                source=first.source,
                created_at=first.created_at,
                updated_at=first.updated_at,
            )
            pages[url] = PageGroup(virtual_page, children)

    return list(pages.values())


# ── Cursor row types ───────────────────────────────────────────────────────

class CursorRow:
    pass


class PageRow(CursorRow):
    def __init__(self, index: int, group: PageGroup):
        self.index = index
        self.group = group


class HighlightRow(CursorRow):
    def __init__(self, group: PageGroup, child_index: int):
        self.group = group
        self.child_index = child_index

    @property
    def item(self) -> Item:
        return self.group.children[self.child_index]


class MoreRow(CursorRow):
    def __init__(self, group: PageGroup):
        self.group = group


# ── Confirm screen ─────────────────────────────────────────────────────────

class ConfirmScreen(ModalScreen[bool]):
    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        yield Static(f"{self.message} [y/n]", id="confirm-msg")

    def key_y(self):
        self.dismiss(True)

    def key_enter(self):
        self.dismiss(True)

    def key_n(self):
        self.dismiss(False)

    def key_escape(self):
        self.dismiss(False)


# ── Note edit screen ───────────────────────────────────────────────────────

class NoteScreen(ModalScreen[str | None]):
    def __init__(self, current: str = ""):
        super().__init__()
        self.current = current

    def compose(self) -> ComposeResult:
        yield Static("Edit note (Enter to save, Esc to cancel):")
        yield Input(value=self.current, id="note-input")

    def on_mount(self):
        self.query_one("#note-input", Input).focus()

    @on(Input.Submitted)
    def on_submit(self, event: Input.Submitted):
        self.dismiss(event.value)

    def key_escape(self):
        self.dismiss(None)


# ── Help screen ───────────────────────────────────────────────────────────

class HelpScreen(ModalScreen[None]):
    CSS = """
    #help-box {
        padding: 1 2;
    }
    """

    HELP_TEXT = """\
[bold]Lumos — Keyboard Shortcuts[/bold]

 [bold]j / ↓[/bold]   Move down          [bold]k / ↑[/bold]   Move up
 [bold]h[/bold]       Go to first         [bold]l[/bold]       Go to last
 [bold]J / S-↓[/bold] Next page          [bold]K / S-↑[/bold] Previous page
 [bold]Enter[/bold]   Expand / collapse   [bold]e[/bold]       Expand all
 [bold]/[/bold]       Search              [bold]x[/bold]       Delete
 [bold]+/-[/bold]     Priority up/down    [bold]n[/bold]       Edit note
 [bold]o[/bold]       Open URL in browser [bold]r[/bold]       Refresh
 [bold]?[/bold]       This help           [bold]Esc[/bold]     Quit / Cancel

Press any key to close."""

    def compose(self) -> ComposeResult:
        yield Static(self.HELP_TEXT, id="help-box")

    def on_key(self, event):
        self.dismiss(None)


# ── Main TUI App ───────────────────────────────────────────────────────────

class LumosApp(App):
    CSS = """
    Screen {
        background: $surface;
    }
    #content {
        height: 1fr;
        overflow-y: auto;
        scrollbar-size: 0 0;
    }
    #table:focus {
        border: none;
    }
    #search-bar {
        dock: top;
        height: 1;
        display: none;
    }
    #search-bar.visible {
        display: block;
    }
    #confirm-msg {
        text-align: center;
        padding: 1;
    }
    #confirm-hint {
        text-align: center;
    }
    """

    BINDINGS = [
        Binding("j,down", "cursor_down", "Down", key_display="j/↓"),
        Binding("k,up", "cursor_up", "Up", key_display="k/↑"),
        Binding("J,shift+down", "page_down", "Page", key_display="J"),
        Binding("K,shift+up", "page_up", "Page", key_display="K"),
        Binding("H", "first_page", "First Page", key_display="H"),
        Binding("L", "last_page", "Last Page", key_display="L"),
        Binding("enter", "select", "Expand"),
        Binding("e", "expand_all", "Expand All"),
        Binding("x", "delete", "Delete"),
        Binding("h,home", "go_first", "First", key_display="h"),
        Binding("l,end", "go_last", "Last", key_display="l"),
        Binding("slash", "search_mode", "Search", key_display="/"),
        Binding("plus,equal", "priority_up", "Priority", key_display="+/-"),
        Binding("minus,underscore,hyphen_minus", "priority_down", "Priority", show=False),
        Binding("n", "edit_note", "Note", show=False),
        Binding("o", "open_url", "Open", key_display="o"),
        Binding("r", "refresh", "Refresh", key_display="r"),
        Binding("question_mark", "show_help", "Help", key_display="?"),
        Binding("q", "quit_or_cancel", "Quit", key_display="q"),
        Binding("escape", "quit_or_cancel", "Quit", show=False),
    ]

    # Korean IME: j→ㅓ, k→ㅏ, e→ㄷ, x→ㅌ, n→ㅜ, h→ㅗ, l→ㅣ, q→ㅂ
    # Korean IME: j→ㅓ, k→ㅏ, e→ㄷ, x→ㅌ, n→ㅜ, h→ㅗ, l→ㅣ, q→ㅂ, o→ㅐ
    _KO_KEY_MAP = {
        "ㅓ": "action_cursor_down",
        "ㅏ": "action_cursor_up",
        "ㄷ": "action_expand_all",
        "ㅌ": "action_delete",
        "ㅜ": "action_edit_note",
        "ㅗ": "action_go_first",
        "ㅣ": "action_go_last",
        "ㅂ": "action_quit_or_cancel",
        "ㅐ": "action_open_url",
        "ㄱ": "action_refresh",
    }

    def __init__(
        self,
        items_path: Path,
        data_dir: Path,
        query: str = "",
        source: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 10,
        sort_by: str = "date",
        descending: bool = True,
        search_in: list[str] | None = None,
        case_sensitive: bool = False,
        hl_style: str = "bold on yellow",
        sel_style: str = "reverse yellow",
        expanded_terms: list[str] | None = None,
    ):
        super().__init__()
        self.items_path = items_path
        self.data_dir = data_dir
        self.query = query
        self.source = source
        self.since = since
        self.until = until
        self.limit = limit
        self.sort_by = sort_by
        self.descending = descending
        self.search_in = search_in
        self.case_sensitive = case_sensitive
        self.hl_style = hl_style
        self.sel_style = sel_style
        self.expanded_terms = expanded_terms
        # Build highlight query: include expanded terms so they get highlighted
        if expanded_terms:
            self._hl_query = " ".join(
                f'"{t}"' if " " in t else t for t in expanded_terms
            )
        else:
            self._hl_query = query

        self.groups: list[PageGroup] = []
        self.rows: list[CursorRow] = []
        self.cursor = 0
        self.total = 0
        self.page_offset = 0
        self.all_expanded = False
        self.search_active = False

    TITLE = "Lumos"
    SHOW_HEADER = False

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search...", id="search-bar")
        yield Vertical(FocusableStatic(id="table"), id="content")
        yield Footer()

    def on_mount(self):
        self._load_data()
        self._render()
        # Ensure the table area has focus, not the hidden Input
        self.query_one("#table", FocusableStatic).focus()

    _CHAR_KEY_MAP = {
        "+": "action_priority_up",
        "=": "action_priority_up",
        "-": "action_priority_down",
    }

    def on_key(self, event) -> None:
        if self.search_active:
            return
        # Korean IME map
        action = self._KO_KEY_MAP.get(event.character)
        # Direct character map (for keys that Textual bindings may miss)
        if not action:
            action = self._CHAR_KEY_MAP.get(event.character)
        if not action:
            # Also check event.key for keys like "plus", "minus"
            action = {
                "plus": "action_priority_up",
                "equal": "action_priority_up",
                "minus": "action_priority_down",
                "hyphen_minus": "action_priority_down",
            }.get(event.key)
        if action:
            getattr(self, action)()
            event.prevent_default()
            event.stop()

    def _load_data(self):
        # Save expand state by URL before reload
        old_expand = {g.page.url: (g.expanded, g.visible_children) for g in self.groups}

        items, self.total = search(
            self.items_path,
            query=self.query,
            source=self.source,
            since=self.since,
            until=self.until,
            search_in=self.search_in,
            case_sensitive=self.case_sensitive,
            sort_by=self.sort_by,
            descending=self.descending,
            limit=self.limit,
            offset=self.page_offset,
            expanded_terms=self.expanded_terms,
        )
        self.groups = group_items(items)

        # Restore expand state
        for g in self.groups:
            if g.page.url in old_expand:
                expanded, vis = old_expand[g.page.url]
                g.expanded = expanded
                g.visible_children = min(vis, len(g.children))
        self._build_rows()

    def _build_rows(self):
        self.rows = []
        for i, group in enumerate(self.groups):
            self.rows.append(PageRow(i, group))
            if group.expanded:
                for ci in range(group.visible_children):
                    self.rows.append(HighlightRow(group, ci))
                if group.has_more:
                    self.rows.append(MoreRow(group))
        if self.cursor >= len(self.rows):
            self.cursor = max(0, len(self.rows) - 1)

    def on_resize(self, event):
        self._render()

    def _should_hl(self, field: str) -> bool:
        """Whether search highlight should apply to this field."""
        if not self.search_in:
            return True  # no filter → highlight everywhere
        return field in self.search_in

    def _render(self):
        w = self.size.width or 80
        # Content width: w minus left marker(1) and right marker(1)
        cw = w - 2
        indent = "  "  # 2 chars
        suffix_len = 22  # "source     YYYY-MM-DD"
        lines: list[Text] = []
        cursor_y = 0  # line number where cursor row starts

        if self.query:
            search_line = Text(f' 🔍 "{self.query}", {self.total} results')
            if self.expanded_terms and len(self.expanded_terms) > 1:
                extra = [t for t in self.expanded_terms if t.lower() != self.query.strip('"').lower()]
                if extra:
                    search_line.append(f"  ← {', '.join(extra)}", style="dim italic")
            lines.append(search_line)

        # Table header — align with data rows
        # Data row suffix: f"{source_str:<11}{date_str}" where date_str = "YYYY-MM-DD" (10 chars)
        # So suffix is always 11 + 10 = 21 chars wide
        hdr_prefix = f"{'':>2}  "  # 4 chars, same as f"{num:>2}  "
        title_label = "Title"
        pri_label = f"{'Priority':<10}  "  # 12 chars, matches priority column
        type_label = f"{'Type':<8}  "  # 10 chars, matches source column
        date_label = f"{'Updated':<10}"  # 10 chars, matches YYYY-MM-DD
        hdr_suffix = pri_label + type_label + date_label
        hdr_prefix_w = _wcswidth(hdr_prefix)
        hdr_suffix_w = _wcswidth(hdr_suffix)
        hdr_pad = max(1, cw - hdr_prefix_w - _wcswidth(title_label) - hdr_suffix_w)
        header_line = Text()
        header_line.append(" ")
        header_line.append(hdr_prefix)
        header_line.append(title_label, style="bold dim")
        header_line.append(" " * hdr_pad)
        header_line.append(hdr_suffix, style="bold dim")
        lines.append(header_line)
        lines.append(Text("-" * w, style="dim"))

        for ri, row in enumerate(self.rows):
            is_selected = ri == self.cursor
            if is_selected:
                cursor_y = len(lines)

            if isinstance(row, PageRow):
                g = row.group
                num = row.index + 1
                source_str = g.page.source.via.value
                date_str = g.page.updated_at.strftime("%Y-%m-%d")
                suffix = f"{source_str:<8}  {date_str:<10}"
                suffix_w = _wcswidth(suffix)

                prefix = f"{num:>2}  "
                prefix_w = _wcswidth(prefix)

                title = g.page.title
                hl_count = len(g.children)
                pri = g.page.priority + sum(c.priority for c in g.children)
                pri_col = f"{pri:<10}  " if pri != 0 else f"{'·':<10}  "
                pri_col_w = _wcswidth(pri_col)
                hl_badge = f" ({hl_count})" if hl_count else ""
                badge_w = _wcswidth(hl_badge)
                # Available width for title: cw - prefix - suffix - pri_col - badge - 1(min padding)
                avail = cw - prefix_w - suffix_w - pri_col_w - badge_w - 1
                title_w = _wcswidth(title)
                if title_w > avail:
                    title = _wctruncate(title, avail)
                    title_w = _wcswidth(title)

                used = prefix_w + title_w + badge_w + pri_col_w + suffix_w
                padding = max(1, cw - used)

                line = Text()
                if is_selected:
                    line.append(" ", style=self.sel_style)
                else:
                    line.append(" ")
                line.append(prefix, style="bold" if is_selected else "")
                title_style = "bold" if is_selected else ""
                title_q = self._hl_query if self._should_hl("title") else ""
                _highlight_append(line, title, title_q, style=title_style, hl_style=self.hl_style)
                if hl_badge:
                    line.append(hl_badge, style="dim")
                line.append(" " * padding)
                line.append(pri_col, style="dim" if pri == 0 else "bold cyan")
                line.append(suffix, style="dim")
                if is_selected:
                    line.append(" ", style=self.sel_style)

                lines.append(line)

                # Search snippet in collapsed mode
                if self.query and not g.expanded:
                    terms = _parse_query_terms(self._hl_query)
                    snip_prefix = f"{indent}  "
                    snip_w = cw - _wcswidth(snip_prefix)
                    snip_shown = False
                    # Check URL match
                    if self._should_hl("url") and any(t.lower() in g.page.url.lower() for t in terms):
                        snippet = _excerpt(g.page.url, terms[0], width=snip_w)
                        snip_line = Text(snip_prefix, style="dim")
                        _highlight_append(snip_line, snippet, self._hl_query, style="dim italic", hl_style=self.hl_style)
                        lines.append(snip_line)
                        snip_shown = True
                    # Check child fields (text, note, ocr)
                    if not snip_shown:
                        child_fields = [
                            ("text", "text"),
                            ("note", "note"),
                            ("ocr", "ocr_text"),
                        ]
                        for field_name, attr_name in child_fields:
                            if not self._should_hl(field_name):
                                continue
                            for child in g.children:
                                val = getattr(child, attr_name, None)
                                if val and any(t.lower() in val.lower() for t in terms):
                                    snippet = _excerpt(val, terms[0], width=snip_w)
                                    snip_line = Text(snip_prefix + "· ", style="dim")
                                    _highlight_append(snip_line, snippet, self._hl_query, style="dim italic", hl_style=self.hl_style)
                                    lines.append(snip_line)
                                    snip_shown = True
                                    break
                            if snip_shown:
                                break

                # URL + separator when expanded
                if g.expanded:
                    url_prefix = indent + "  "
                    url_text = url_prefix + g.page.url
                    url_lines = _wrap_text(url_text, cw, url_prefix)
                    url_q = self._hl_query if self._should_hl("url") else ""
                    for ul in url_lines:
                        url_line = Text(" ")
                        _highlight_append(url_line, ul, url_q, style="dim", hl_style=self.hl_style)
                        lines.append(url_line)
                    sep_count = (w - _wcswidth(indent)) // _wcswidth("┄")
                    lines.append(Text(indent + "┄" * sep_count, style="dim"))

            elif isinstance(row, HighlightRow):
                item = row.item
                hl_indent = indent + "  "  # align under text after "· "

                if item.type == ItemType.IMAGE:
                    raw = f"{indent}· 📷 {item.media or ''}"
                else:
                    raw = f"{indent}· {item.text or ''}"

                # Split on newlines first, then wrap each segment
                hl_lines = []
                for si, segment in enumerate(raw.split("\n")):
                    seg = hl_indent + segment if si > 0 else segment
                    hl_lines.extend(_wrap_text(seg, cw, hl_indent))
                for li, hl_line_text in enumerate(hl_lines):
                    line = Text()
                    if li == 0:
                        if is_selected:
                            line.append(" ", style=self.sel_style)
                        else:
                            line.append(" ")
                    else:
                        line.append(" ")
                    line_w = _wcswidth(hl_line_text)
                    hl_q = self._hl_query if any(self._should_hl(f) for f in ("text", "note", "ocr")) else ""
                    _highlight_append(line, hl_line_text, hl_q, hl_style=self.hl_style)
                    if is_selected and li == 0:
                        pad = cw - line_w
                        if pad > 0:
                            line.append(" " * pad)
                        line.append(" ", style=self.sel_style)
                    lines.append(line)

                # Note for images
                if item.type == ItemType.IMAGE and item.note:
                    note_lines = _wrap_text(hl_indent + item.note, cw, hl_indent)
                    for nl in note_lines:
                        lines.append(Text(" " +nl, style="dim"))

                # Metadata line
                meta_parts = []
                if item.priority != 0:
                    meta_parts.append(f"Priority: {item.priority}")
                if item.source.page:
                    meta_parts.append(f"Page: {item.source.page}")
                if item.source.location:
                    meta_parts.append(f"Location: {item.source.location}")
                if item.note and item.type != ItemType.IMAGE:
                    meta_parts.append(f"Note: {item.note}")
                if meta_parts:
                    meta_raw = hl_indent + " | ".join(meta_parts)
                    meta_wrapped = _wrap_text(meta_raw, cw, hl_indent)
                    for ml in meta_wrapped:
                        lines.append(Text(" " +ml, style="dim"))
                lines.append(Text())

            elif isinstance(row, MoreRow):
                remaining = row.group.remaining
                line = Text()
                if is_selected:
                    line.append(" ", style=self.sel_style)
                else:
                    line.append(" ")
                line.append(
                    f"{indent}{remaining} more (enter for next 10)", style="dim italic"
                )
                lines.append(line)

        lines.append(Text("-" * w, style="dim"))

        # Page info (total = number of page groups)
        end = min(self.page_offset + self.limit, self.total)
        page_info = Text(
            f" {self.page_offset + 1}-{end} of {self.total} pages",
            style="dim",
        )
        lines.append(page_info)

        output = Text("\n").join(lines)
        self.query_one("#table", FocusableStatic).update(output)

        # Scroll to keep cursor visible
        content = self.query_one("#content")
        vh = content.size.height
        if vh > 0:
            # Keep cursor row roughly centered
            target = max(0, cursor_y - vh // 3)
            content.scroll_to(y=target, animate=False)

    # ── Actions ────────────────────────────────────────────────────────────

    def action_cursor_down(self):
        if self.cursor < len(self.rows) - 1:
            self.cursor += 1
            self._render()
        elif self.page_offset + self.limit < self.total:
            self.page_offset += self.limit
            self.cursor = 0
            self._load_data()
            self._render()

    def action_cursor_up(self):
        if self.cursor > 0:
            self.cursor -= 1
            self._render()
        elif self.page_offset > 0:
            self.page_offset = max(0, self.page_offset - self.limit)
            self._load_data()
            self.cursor = max(0, len(self.rows) - 1)
            self._render()

    def action_go_first(self):
        self.cursor = 0
        self._render()

    def action_go_last(self):
        if self.rows:
            self.cursor = len(self.rows) - 1
            self._render()

    def action_page_down(self):
        if self.page_offset + self.limit < self.total:
            self.page_offset += self.limit
            self.cursor = 0
            self._load_data()
            self._render()

    def action_page_up(self):
        if self.page_offset > 0:
            self.page_offset = max(0, self.page_offset - self.limit)
            self.cursor = 0
            self._load_data()
            self._render()

    def action_first_page(self):
        if self.page_offset > 0:
            self.page_offset = 0
            self.cursor = 0
            self._load_data()
            self._render()

    def action_last_page(self):
        last_offset = max(0, ((self.total - 1) // self.limit) * self.limit)
        if self.page_offset != last_offset:
            self.page_offset = last_offset
            self.cursor = 0
            self._load_data()
            self._render()

    def action_select(self):
        if not self.rows:
            return
        row = self.rows[self.cursor]
        if isinstance(row, PageRow):
            row.group.toggle_expand()
            self._build_rows()
            self._render()
        elif isinstance(row, MoreRow):
            row.group.show_more()
            self._build_rows()
            self._render()

    def action_expand_all(self):
        self.all_expanded = not self.all_expanded
        for g in self.groups:
            if self.all_expanded:
                g.expanded = True
                g.visible_children = min(g.children_page_size, len(g.children))
            else:
                g.expanded = False
                g.visible_children = 0
        self._build_rows()
        self._render()

    def action_delete(self):
        if not self.rows:
            return
        row = self.rows[self.cursor]

        if isinstance(row, PageRow):
            group = row.group
            child_count = len(group.children)
            msg = f"Delete '{group.page.title}'"
            if child_count:
                msg += f" and {child_count} highlight(s)/image(s)"
            msg += "?"

            def on_confirm(result: bool):
                if result:
                    ids = {group.page.id} | {c.id for c in group.children}
                    delete_items(self.items_path, ids)
                    # Clean up media/cache files
                    for child in group.children:
                        if child.media:
                            p = self.data_dir / child.media
                            if p.exists():
                                p.unlink()
                    if group.page.cache:
                        for path in [group.page.cache.mhtml, group.page.cache.readable]:
                            if path:
                                p = self.data_dir / path
                                if p.exists():
                                    p.unlink()
                    self._load_data()
                    self._render()

            self.push_screen(ConfirmScreen(msg), on_confirm)

        elif isinstance(row, HighlightRow):
            item = row.item
            preview = (item.text or item.media or "")[:40]
            msg = f"Delete highlight '{preview}…'?"

            def on_confirm_hl(result: bool):
                if result:
                    delete_item(self.items_path, item.id)
                    if item.media:
                        p = self.data_dir / item.media
                        if p.exists():
                            p.unlink()
                    self._load_data()
                    self._render()

            self.push_screen(ConfirmScreen(msg), on_confirm_hl)

    def action_priority_up(self):
        row = self.rows[self.cursor] if self.rows else None
        if isinstance(row, (HighlightRow, PageRow)):
            item_id = row.item.id if isinstance(row, HighlightRow) else row.group.page.id
            update_item(
                self.items_path,
                item_id,
                lambda it: it.model_copy(update={"priority": it.priority + 1}),
            )
            self._load_data()
            self._render()

    def action_priority_down(self):
        row = self.rows[self.cursor] if self.rows else None
        if isinstance(row, (HighlightRow, PageRow)):
            item_id = row.item.id if isinstance(row, HighlightRow) else row.group.page.id
            update_item(
                self.items_path,
                item_id,
                lambda it: it.model_copy(update={"priority": it.priority - 1}),
            )
            self._load_data()
            self._render()

    def action_open_url(self):
        if not self.rows:
            return
        row = self.rows[self.cursor]
        url = None
        if isinstance(row, PageRow):
            url = row.group.page.url
        elif isinstance(row, HighlightRow):
            url = row.group.page.url
        if url:
            import webbrowser
            webbrowser.open(url)

    def action_edit_note(self):
        row = self.rows[self.cursor] if self.rows else None
        if isinstance(row, HighlightRow):
            current_note = row.item.note or ""

            def on_note(result: str | None):
                if result is not None:
                    update_item(
                        self.items_path,
                        row.item.id,
                        lambda item: item.model_copy(update={"note": result or None}),
                    )
                    self._load_data()
                    self._render()

            self.push_screen(NoteScreen(current_note), on_note)

    def action_refresh(self):
        self._load_data()
        self._render()

    def action_show_help(self):
        self.push_screen(HelpScreen())

    def action_search_mode(self):
        search_bar = self.query_one("#search-bar", Input)
        search_bar.add_class("visible")
        search_bar.value = self.query
        search_bar.focus()
        self.search_active = True

    @on(Input.Submitted, "#search-bar")
    def on_search_submit(self, event: Input.Submitted):
        self.query = event.value
        self.page_offset = 0
        self.cursor = 0
        search_bar = self.query_one("#search-bar", Input)
        search_bar.remove_class("visible")
        self.search_active = False
        self._load_data()
        self._render()

    def action_quit_or_cancel(self):
        if self.search_active:
            search_bar = self.query_one("#search-bar", Input)
            search_bar.remove_class("visible")
            self.search_active = False
        else:
            self.exit()


def _excerpt(text: str, query: str, width: int = 70) -> str:
    lower = text.lower()
    idx = lower.find(query.lower())
    if idx < 0:
        return text[:width] + ("…" if len(text) > width else "")
    start = max(0, idx - 20)
    end = min(len(text), idx + len(query) + 50)
    excerpt = text[start:end]
    if start > 0:
        excerpt = "…" + excerpt
    if end < len(text):
        excerpt += "…"
    return excerpt


def run_tui(
    items_path: Path,
    data_dir: Path,
    query: str = "",
    source: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 10,
    sort_by: str = "date",
    descending: bool = True,
    search_in: list[str] | None = None,
    case_sensitive: bool = False,
    hl_style: str = "bold on yellow",
    sel_style: str = "reverse yellow",
    expanded_terms: list[str] | None = None,
):
    app = LumosApp(
        items_path=items_path,
        data_dir=data_dir,
        query=query,
        source=source,
        since=since,
        until=until,
        limit=limit,
        sort_by=sort_by,
        descending=descending,
        search_in=search_in,
        case_sensitive=case_sensitive,
        hl_style=hl_style,
        sel_style=sel_style,
        expanded_terms=expanded_terms,
    )
    app.run()
