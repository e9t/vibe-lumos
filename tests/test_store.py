"""Tests for Lumos JSONL store."""

import tempfile
from pathlib import Path

from lumos.core.models import Item, ItemType, Source, SourceVia
from lumos.core.store import (
    append_item,
    build_url_index,
    delete_item,
    delete_items,
    get_all,
    get_by_id,
    get_by_url,
    search,
    update_item,
)


def _tmp_path() -> Path:
    f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    f.close()
    return Path(f.name)


def _make_item(title: str = "Test", url: str = "https://example.com", **kwargs) -> Item:
    return Item(type=ItemType.PAGE, url=url, title=title, **kwargs)


def test_append_and_get_all():
    path = _tmp_path()
    item = _make_item("Hello")
    append_item(path, item)
    items = get_all(path)
    assert len(items) == 1
    assert items[0].title == "Hello"


def test_get_by_id():
    path = _tmp_path()
    item = _make_item("Find Me")
    append_item(path, item)
    found = get_by_id(path, item.id)
    assert found is not None
    assert found.title == "Find Me"
    assert get_by_id(path, "nonexistent") is None


def test_get_by_url():
    path = _tmp_path()
    append_item(path, _make_item("A", url="https://a.com"))
    append_item(path, _make_item("B", url="https://b.com"))
    append_item(path, _make_item("A2", url="https://a.com"))
    results = get_by_url(path, "https://a.com")
    assert len(results) == 2


def test_update_item():
    path = _tmp_path()
    item = _make_item("Original")
    append_item(path, item)
    updated = update_item(path, item.id, lambda i: i.model_copy(update={"title": "Updated"}))
    assert updated is not None
    assert updated.title == "Updated"
    assert get_by_id(path, item.id).title == "Updated"


def test_delete_item():
    path = _tmp_path()
    item = _make_item("Delete Me")
    append_item(path, item)
    assert delete_item(path, item.id) is True
    assert get_all(path) == []
    assert delete_item(path, "nonexistent") is False


def test_delete_items():
    path = _tmp_path()
    items = [_make_item(f"Item {i}") for i in range(5)]
    for item in items:
        append_item(path, item)
    ids_to_delete = {items[0].id, items[2].id, items[4].id}
    removed = delete_items(path, ids_to_delete)
    assert removed == 3
    remaining = get_all(path)
    assert len(remaining) == 2


def test_search_text():
    path = _tmp_path()
    append_item(path, _make_item("Python Tutorial"))
    append_item(path, _make_item("Rust Guide"))
    append_item(path, _make_item("Python Advanced"))
    results, total = search(path, query="python", limit=10)
    assert total == 2
    assert len(results) == 2


def test_search_source_filter():
    path = _tmp_path()
    append_item(path, _make_item("Web Page", source=Source(via=SourceVia.WEB)))
    append_item(path, _make_item("Kindle Book", source=Source(via=SourceVia.KINDLE)))
    results, total = search(path, source="kindle", limit=10)
    assert total == 1
    assert results[0].title == "Kindle Book"


def test_build_url_index():
    path = _tmp_path()
    i1 = _make_item("A", url="https://a.com")
    i2 = Item(type=ItemType.HIGHLIGHT, url="https://a.com", title="A", text="hl")
    i3 = _make_item("B", url="https://b.com")
    for item in [i1, i2, i3]:
        append_item(path, item)
    index = build_url_index(path)
    assert len(index["https://a.com"]) == 2
    assert len(index["https://b.com"]) == 1
