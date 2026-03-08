"""Tests for import commands."""

import json
import tempfile
from pathlib import Path

from lumos.cli.import_cmd import _parse_diigo_date, _parse_kindle_clippings, _strip_html


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
