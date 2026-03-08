"""Tests for import commands."""

import json
import tempfile
from pathlib import Path

from typer.testing import CliRunner

from lumos.cli.import_cmd import (
    _parse_diigo_date,
    _parse_kindle_clippings,
    _strip_html,
    import_app,
)
from lumos.core.config import write_config


runner = CliRunner()


def test_strip_html():
    assert _strip_html("<a href='x'>hello</a> world") == "hello world"
    assert _strip_html("plain text") == "plain text"


def test_parse_diigo_date():
    dt = _parse_diigo_date("2026/03/06 02:50:18 +0000")
    assert dt.year == 2026
    assert dt.month == 3
    assert dt.day == 6


def test_parse_kindle_clippings():
    text = """Thinking, Fast and Slow (Daniel Kahneman)
- Your Highlight on page 62 | Location 940-941 | Added on Friday, January 10, 2026 3:15:00 PM

A reliable way to make people believe in falsehoods is frequent repetition.
==========
Thinking, Fast and Slow (Daniel Kahneman)
- Your Highlight on Location 1234-1256 | Added on Friday, January 10, 2026 3:20:00 PM

Nothing in life is as important as you think it is.
==========
"""
    entries = _parse_kindle_clippings(text)
    assert len(entries) == 2
    assert entries[0]["title"] == "Thinking, Fast and Slow"
    assert entries[0]["author"] == "Daniel Kahneman"
    assert entries[0]["page"] == 62
    assert entries[0]["location"] == "940-941"
    assert "frequent repetition" in entries[0]["text"]
    assert entries[1]["page"] is None
    assert entries[1]["location"] == "1234-1256"


def test_import_kindle_command(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        config_path = data_dir / "config.json"
        clippings_path = data_dir / "My Clippings.txt"
        items_path = data_dir / "items.jsonl"

        # 1. Create fake config
        write_config({"data_dir": str(data_dir)}, config_path=config_path)
        monkeypatch.setenv("LUMOS_CONFIG_PATH", str(config_path))

        # 2. Create fake clippings file
        clippings_content = """Book One (Author A)
- Your Highlight on page 10 | Location 100-101
Content for book one.
==========
Book Two (Author B)
- Your Highlight on Location 200-201
Content for book two.
==========
Book One (Author A)
- Your Highlight on page 20 | Location 300-301
More content for book one.
"""
        clippings_path.write_text(clippings_content)

        # 3. Run the command
        result = runner.invoke(import_app, ["kindle", str(clippings_path)])
        assert result.exit_code == 0
        assert "Imported 4 items" in result.stdout

        # 4. Verify items.jsonl
        lines = items_path.read_text().strip().splitlines()
        assert len(lines) == 4
        
        items = [json.loads(line) for line in lines]

        # First page item
        assert items[0]["type"] == "page"
        assert items[0]["title"] == "Book One"
        assert items[0]["source"]["via"] == "kindle"
        assert items[0]["source"]["author"] == "Author A"

        # First highlight
        assert items[1]["type"] == "highlight"
        assert items[1]["title"] == "Book One"
        assert items[1]["text"] == "Content for book one."
        assert items[1]["source"]["page"] == 10

        # Second page item
        assert items[2]["type"] == "page"
        assert items[2]["title"] == "Book Two"

        # Second highlight
        assert items[3]["type"] == "highlight"
        assert items[3]["title"] == "Book One"
        assert items[3]["text"] == "More content for book one."
        assert items[3]["source"]["page"] == 20
