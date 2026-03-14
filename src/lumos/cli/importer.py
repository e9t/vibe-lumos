"""Lumos Importer — for Kindle and Diigo."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich import print as rprint

from lumos.core.config import load_config
from lumos.core.models import Item, ItemType, Source, SourceVia
from lumos.core.store import append_item, get_by_url

app = typer.Typer(
    name="lumos-import",
    help="Import highlights and bookmarks from external services.\n\n  x       Sync liked tweets from X (Twitter)\n  kindle  Sync Kindle highlights\n  diigo   Import Diigo bookmarks",
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


# ── X (Twitter) Likes ─────────────────────────────────────────────────────

def _parse_x_likes_js(file: Path) -> list[dict]:
    """Parse X data archive like.js file."""
    raw = file.read_text(encoding="utf-8")
    # Strip the JS variable assignment: window.YTD.like.part0 = [...]
    json_str = re.sub(r"^.*?=\s*", "", raw, count=1)
    entries = json.loads(json_str)
    return [e.get("like", e) for e in entries]


@app.command("x-file")
def import_x_file(
    file: Annotated[Path, typer.Argument(help="Path to like.js from X data archive")],
):
    """Import liked tweets from X (Twitter) data archive file."""
    config = load_config()
    items_path = config.items_path()

    if not file.exists():
        rprint(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)

    likes = _parse_x_likes_js(file)
    if not likes:
        rprint("[yellow]No likes found in file.[/yellow]")
        raise typer.Exit(0)

    count = 0
    skipped = 0
    for like in likes:
        tweet_id = like.get("tweetId", "")
        if not tweet_id:
            continue

        url = f"https://x.com/i/status/{tweet_id}"
        full_text = like.get("fullText", "")

        # Skip if already imported
        existing = get_by_url(items_path, url)
        if existing:
            skipped += 1
            continue

        item = Item(
            type=ItemType.PAGE,
            url=url,
            title=full_text[:80] + ("…" if len(full_text) > 80 else "") if full_text else f"Tweet {tweet_id}",
            text=full_text or None,
            source=Source(via=SourceVia.X),
        )
        append_item(items_path, item)
        count += 1

    rprint(f"[green]✓[/green] Imported {count} likes from X.{f' ({skipped} already existed)' if skipped else ''}")


# ── X (Twitter) Sync via Playwright ──────────────────────────────────────

def _x_state_path(data_dir: Path) -> Path:
    return data_dir / "x-state.json"


def _find_chrome_executable() -> str:
    """Find the system Chrome/Chromium executable."""
    import platform, shutil
    if platform.system() == "Darwin":
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        if Path(chrome).exists():
            return chrome
    for name in ("google-chrome", "google-chrome-stable", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _x_profile_dir(data_dir: Path) -> Path:
    return data_dir / "x-chrome-profile"


_X_USERNAME_FILE = "x-username.txt"


def _x_login(state_path: Path) -> None:
    """Open persistent Chrome profile for X login."""
    sync_playwright = _get_playwright()
    chrome = _find_chrome_executable()
    data_dir = state_path.parent
    profile_dir = _x_profile_dir(data_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)

    rprint("[dim]Opening Chrome for X login...[/dim]")
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            executable_path=chrome or None,
            channel="chrome" if chrome else None,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://x.com/login")
        rprint("[bold]Please log in to X...[/bold]")

        # Wait for login to complete (URL leaves /login and /i/flow paths)
        while True:
            page.wait_for_timeout(1000)
            url = page.url
            if "x.com" in url and "/login" not in url and "/i/flow" not in url:
                break

        # Detect username from profile nav link
        page.wait_for_timeout(2000)
        username = page.evaluate("""() => {
            const link = document.querySelector('a[data-testid="AppTabBar_Profile_Link"]');
            if (link) {
                const m = link.getAttribute('href')?.match(/^\\/([^/]+)$/);
                if (m) return m[1];
            }
            return '';
        }""")

        if not username:
            rprint("[yellow]Could not detect username automatically.[/yellow]")
            import click
            username = click.prompt("X username (without @)")

        rprint(f"[dim]Logged in as @{username}[/dim]")
        (data_dir / _X_USERNAME_FILE).write_text(username)

        # Auto-navigate to likes page
        page.goto(f"https://x.com/{username}/likes")
        page.wait_for_selector('article[data-testid="tweet"]', timeout=30_000)
        rprint("[dim]Likes page loaded.[/dim]")

        state_path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(state_path))
        context.close()
    rprint("[green]✓ Session saved[/green]")


def _x_fetch_likes(state_path: Path, max_scrolls: int = 50, days: int = 7) -> list[dict]:
    """Fetch liked tweets using persistent Chrome profile. Stops at tweets older than `days`."""
    sync_playwright = _get_playwright()
    chrome = _find_chrome_executable()
    data_dir = state_path.parent
    profile_dir = _x_profile_dir(data_dir)

    if not profile_dir.exists():
        return []

    # Read saved username
    username_file = data_dir / _X_USERNAME_FILE
    if not username_file.exists():
        rprint("[red]No username saved. Run --login first.[/red]")
        return []
    username = username_file.read_text().strip()
    likes_url = f"https://x.com/{username}/likes"

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=True,
            executable_path=chrome or None,
            channel="chrome" if chrome else None,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        page.goto(likes_url, wait_until="domcontentloaded", timeout=60_000)
        rprint(f"[dim]  URL after load: {page.url}[/dim]")
        if "login" in page.url or "signin" in page.url:
            rprint("[dim]  Redirected to login page[/dim]")
            context.close()
            return []

        # Wait for tweets to appear
        try:
            page.wait_for_selector('article[data-testid="tweet"]', timeout=15_000)
            rprint("[dim]  Tweets found on page[/dim]")
        except Exception:
            # Debug: dump page content snippet
            title = page.title()
            body_text = page.evaluate("() => document.body?.innerText?.slice(0, 500) || 'empty'")
            rprint(f"[dim]  No tweets found. Title: {title}[/dim]")
            rprint(f"[dim]  Body: {body_text[:200]}[/dim]")
            context.close()
            return []

        tweets = {}
        no_new_count = 0

        for _ in range(max_scrolls):
            batch = page.evaluate("""() => {
                const articles = document.querySelectorAll('article[data-testid="tweet"]');
                const results = [];
                for (const article of articles) {
                    const links = article.querySelectorAll('a[href*="/status/"]');
                    let tweetUrl = '';
                    for (const link of links) {
                        const href = link.getAttribute('href');
                        if (href && /^\\/[^/]+\\/status\\/\\d+$/.test(href)) {
                            tweetUrl = href;
                            break;
                        }
                    }
                    if (!tweetUrl) continue;

                    const textEl = article.querySelector('div[data-testid="tweetText"]');
                    const text = textEl ? textEl.innerText : '';

                    const timeEl = article.querySelector('time[datetime]');
                    const datetime = timeEl ? timeEl.getAttribute('datetime') : '';

                    results.push({ url: tweetUrl, text: text, datetime: datetime });
                }
                return results;
            }""")

            from datetime import timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            too_old = False

            prev_count = len(tweets)
            for t in batch:
                # Check if tweet is older than cutoff
                if t.get("datetime"):
                    try:
                        dt = datetime.fromisoformat(t["datetime"].replace("Z", "+00:00"))
                        if dt < cutoff:
                            too_old = True
                            continue
                    except ValueError:
                        pass
                url = t["url"]
                if url not in tweets:
                    tweets[url] = t

            if too_old:
                break

            if len(tweets) == prev_count:
                no_new_count += 1
                if no_new_count >= 3:
                    break
            else:
                no_new_count = 0

            page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
            page.wait_for_timeout(1500)

        # Save updated state
        context.storage_state(path=str(state_path))
        context.close()

    return list(tweets.values())


@app.command("x")
def import_x_sync(
    login: Annotated[bool, typer.Option("--login", help="Force re-login to X")] = False,
):
    """Sync liked tweets from X (Twitter) via browser automation."""
    config = load_config()
    items_path = config.items_path()
    data_dir = config.get_data_dir()
    state_path = _x_state_path(data_dir)

    if login or not state_path.exists():
        _x_login(state_path)

    rprint("[dim]Fetching likes...[/dim]")
    tweets = _x_fetch_likes(state_path)
    rprint(f"[dim]Fetched {len(tweets)} tweets[/dim]")

    if not tweets:
        rprint("[red]Session expired. Re-authenticating...[/red]")
        _x_login(state_path)
        tweets = _x_fetch_likes(state_path)
        if not tweets:
            rprint("[red]Failed to fetch likes.[/red]")
            raise typer.Exit(1)

    # Debug: check if datetime is being extracted
    with_dt = sum(1 for t in tweets if t.get("datetime"))
    rprint(f"[dim]{len(tweets)} tweets ({with_dt} with datetime)[/dim]")

    count = 0
    skipped = 0
    for t in tweets:
        url = f"https://x.com{t['url']}"
        text = t.get("text", "")

        existing = get_by_url(items_path, url)
        if existing:
            skipped += 1
            continue

        # Use tweet's published datetime if available
        created_dt = None
        if t.get("datetime"):
            try:
                created_dt = datetime.fromisoformat(t["datetime"].replace("Z", "+00:00"))
            except ValueError:
                pass

        first_line = text.split("\n")[0] if text else ""
        title = first_line[:80] + ("…" if len(first_line) > 80 else "") if first_line else url.split("/")[-1]

        item = Item(
            type=ItemType.PAGE,
            url=url,
            title=title,
            text=text or None,
            source=Source(via=SourceVia.X),
            **({"created_at": created_dt, "updated_at": created_dt} if created_dt else {}),
        )
        append_item(items_path, item)
        count += 1

    rprint(f"[green]✓ Synced: {count} new, {skipped} already existed[/green]")


# ── Kindle Sync (headless via read.amazon.com) ─────────────────────────────

_KINDLE_NOTEBOOK_URL = "https://read.amazon.com/notebook"
_KINDLE_STATE_FILE = "kindle-state.json"


def _kindle_state_path(data_dir: Path) -> Path:
    return data_dir / _KINDLE_STATE_FILE


def _get_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        rprint("[red]playwright is required: pip install playwright && playwright install chromium[/red]")
        raise typer.Exit(1)


def _kindle_login(state_path: Path) -> None:
    """Open browser for Amazon login, save session state (incl. httpOnly cookies)."""
    sync_playwright = _get_playwright()

    rprint("[dim]Opening browser for Amazon login...[/dim]")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(_KINDLE_NOTEBOOK_URL)
        # Wait for user to log in and notebook to load
        rprint("[bold]Please log in to Amazon. Waiting for notebook to load...[/bold]")
        page.wait_for_selector("#kp-notebook-library", timeout=300_000)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(state_path))
        browser.close()
    rprint(f"[green]✓ Session saved[/green]")


def _kindle_fetch_all(state_path: Path) -> tuple[str, dict[str, str]]:
    """Fetch notebook + all book annotations. Returns (main_html, {asin: ann_html})."""
    sync_playwright = _get_playwright()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(state_path))
        page = context.new_page()

        # Fetch main notebook page
        page.goto(_KINDLE_NOTEBOOK_URL, wait_until="networkidle")
        if "ap/signin" in page.url:
            browser.close()
            return "", {}

        main_html = page.content()

        # Extract ASINs from the page
        asins = page.eval_on_selector_all(
            '#kp-notebook-library > div[id]',
            "els => els.map(e => e.id).filter(id => /^B[A-Z0-9]{9}$/.test(id))",
        )

        # Fetch annotations for each book
        annotations = {}
        for asin in asins:
            resp = page.evaluate(
                """async (asin) => {
                    const r = await fetch('/notebook?asin=' + asin + '&contentLimitState=&');
                    return await r.text();
                }""",
                asin,
            )
            if resp and "ap/signin" not in resp:
                annotations[asin] = resp

        # Save updated state
        context.storage_state(path=str(state_path))
        browser.close()

    return main_html, annotations


def _parse_notebook_library(html: str) -> list[dict]:
    """Parse the main notebook page to extract book ASINs and titles."""
    books = []
    # Pattern: <div id="ASIN"> ... <h2 ...>Title</h2> ... <p ...>By: Author</p>
    asin_re = re.compile(
        r'<div\s+id="(B[A-Z0-9]{9})"[^>]*>.*?'
        r'<h2[^>]*class="[^"]*kp-notebook-searchable[^"]*"[^>]*>(.*?)</h2>.*?'
        r'<p[^>]*class="[^"]*kp-notebook-searchable[^"]*"[^>]*>(?:By:\s*)?(.*?)</p>',
        re.DOTALL,
    )
    for m in asin_re.finditer(html):
        books.append({
            "asin": m.group(1),
            "title": _strip_html(m.group(2)).strip(),
            "author": _strip_html(m.group(3)).strip(),
        })
    return books


def _parse_notebook_annotations(html: str) -> list[dict]:
    """Parse annotation page HTML to extract highlights."""
    highlights = []
    # Each highlight block has location, text, and optional note
    # Note: value= may appear before or after id= in the input tag
    block_re = re.compile(
        r'<input[^>]*value="(\d+)"[^>]*id="kp-annotation-location"[^>]*/?>.*?'
        r'<span\s+id="highlight"[^>]*>(.*?)</span>',
        re.DOTALL,
    )
    # Notes follow highlights: <span id="note">...</span>
    note_re = re.compile(
        r'<span\s+id="note"[^>]*>(.*?)</span>',
        re.DOTALL,
    )

    for m in block_re.finditer(html):
        location = m.group(1)
        text = _strip_html(m.group(2)).strip()
        if not text:
            continue

        # Look for a note after this highlight
        note = None
        rest = html[m.end():]
        # Note should appear before the next highlight block
        next_hl = rest.find('id="kp-annotation-location"')
        search_region = rest[:next_hl] if next_hl > 0 else rest[:2000]
        note_match = note_re.search(search_region)
        if note_match:
            note_text = _strip_html(note_match.group(1)).strip()
            if note_text:
                note = note_text

        highlights.append({
            "location": location,
            "text": text,
            "note": note,
        })

    return highlights


@app.command("kindle")
def import_kindle_sync(
    login: Annotated[bool, typer.Option("--login", help="Force re-login to Amazon")] = False,
):
    """Sync Kindle highlights from read.amazon.com/notebook."""
    config = load_config()
    items_path = config.items_path()
    data_dir = config.get_data_dir()
    state_path = _kindle_state_path(data_dir)

    # Login if needed
    if login or not state_path.exists():
        _kindle_login(state_path)

    # Fetch notebook + all annotations in one browser session
    rprint("[dim]Fetching notebook...[/dim]")
    main_html, annotations = _kindle_fetch_all(state_path)
    if not main_html:
        rprint("[red]Session expired. Re-authenticating...[/red]")
        _kindle_login(state_path)
        main_html, annotations = _kindle_fetch_all(state_path)
        if not main_html:
            rprint("[red]Failed to fetch notebook.[/red]")
            raise typer.Exit(1)

    books = _parse_notebook_library(main_html)
    if not books:
        rprint("[yellow]No books found.[/yellow]")
        raise typer.Exit(1)

    rprint(f"[dim]Found {len(books)} books, {len(annotations)} with highlights[/dim]")

    total_new = 0
    total_skipped = 0

    for book in books:
        asin = book["asin"]
        title = book["title"]
        author = book["author"]
        url = f"kindle://book/{asin}"

        ann_html = annotations.get(asin)
        if not ann_html:
            continue

        highlights = _parse_notebook_annotations(ann_html)
        if not highlights:
            continue

        # Check existing items for this book
        existing = get_by_url(items_path, url)
        existing_texts = {it.text for it in existing if it.text}

        # Ensure page item exists
        has_page = any(it.type == ItemType.PAGE for it in existing)
        if not has_page:
            page_item = Item(
                type=ItemType.PAGE,
                url=url,
                title=title,
                source=Source(via=SourceVia.KINDLE, book=title, author=author),
            )
            append_item(items_path, page_item)
            total_new += 1

        # Add new highlights
        book_new = 0
        for hl in highlights:
            if hl["text"] in existing_texts:
                total_skipped += 1
                continue

            hl_item = Item(
                type=ItemType.HIGHLIGHT,
                url=url,
                title=title,
                text=hl["text"],
                note=hl.get("note"),
                source=Source(
                    via=SourceVia.KINDLE,
                    book=title,
                    author=author,
                    location=hl["location"],
                ),
            )
            append_item(items_path, hl_item)
            book_new += 1

        if book_new > 0:
            rprint(f"  [green]+{book_new}[/green] {title}")
            total_new += book_new

    rprint(f"[green]✓ Synced: {total_new} new, {total_skipped} already existed[/green]")


if __name__ == "__main__":
    app()
