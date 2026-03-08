"""Tests for Lumos core models."""

from lumos.core.models import (
    Cache,
    Item,
    ItemType,
    Location,
    Source,
    SourceVia,
    generate_id,
)


def test_generate_id_format():
    id_ = generate_id()
    assert id_.startswith("lm_")
    parts = id_.split("_")
    assert len(parts) == 3
    assert len(parts[1]) == 8  # YYYYMMDD
    assert len(parts[2]) == 6  # random


def test_item_defaults():
    item = Item(type=ItemType.PAGE, url="https://example.com", title="Test")
    assert item.id.startswith("lm_")
    assert item.type == ItemType.PAGE
    assert item.text is None
    assert item.priority == 0
    assert item.source.via == SourceVia.WEB


def test_item_serialization_roundtrip():
    item = Item(
        type=ItemType.HIGHLIGHT,
        url="https://example.com",
        title="Test",
        text="highlighted text",
        source=Source(via=SourceVia.KINDLE, book="My Book", author="Author", page=42),
        location=Location(xpath="/html/body/p", start_offset=0, end_offset=10),
    )
    json_str = item.model_dump_json()
    restored = Item.model_validate_json(json_str)
    assert restored.text == "highlighted text"
    assert restored.source.book == "My Book"
    assert restored.location.xpath == "/html/body/p"


def test_item_image_with_cache():
    item = Item(
        type=ItemType.IMAGE,
        url="https://example.com",
        title="Image",
        media="media/img_20260307_abc123.png",
        cache=Cache(mhtml="cache/lm_xxx.mhtml", readable="cache/lm_xxx.txt"),
    )
    assert item.media == "media/img_20260307_abc123.png"
    assert item.cache.mhtml == "cache/lm_xxx.mhtml"
