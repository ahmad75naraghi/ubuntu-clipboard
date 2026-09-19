"""History storage: CRUD, pruning, search escaping and schema migration."""

from __future__ import annotations

import base64
import sqlite3
import threading
import time

from ubuntu_clipboard.config import Config
from ubuntu_clipboard.models import ContentType
from ubuntu_clipboard.storage import SCHEMA_VERSION, HistoryStore

LEGACY_SCHEMA = """
CREATE TABLE items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hash TEXT UNIQUE,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    preview TEXT NOT NULL,
    pinned INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    metadata TEXT DEFAULT '{}'
);
"""


def test_add_and_fetch_text(store):
    item = store.add_text("hello world")
    assert item is not None
    assert item.type is ContentType.TEXT
    assert item.preview == "hello world"
    assert item.size_bytes == len("hello world")
    assert store.count() == 1
    assert store.get(item.id).content == "hello world"
    assert store.get_text(item.id) == "hello world"


def test_listing_does_not_load_payloads(store):
    store.add_text("x" * 100)
    listed = store.list()[0]
    assert listed.content is None
    assert store.get(listed.id).content is not None


def test_filters_are_applied(store):
    assert store.add_text("") is None
    assert store.add_text("   ") is None
    assert store.add_text("4111 1111 1111 1111") is None
    assert store.count() == 0


def test_duplicates_bubble_up_instead_of_piling_up(store):
    first = store.add_text("same text")
    time.sleep(0.01)
    store.add_text("other")
    time.sleep(0.01)
    again = store.add_text("same text")
    assert store.count() == 2
    assert again.id == first.id
    assert store.list()[0].preview == "same text"


def test_pinned_items_keep_their_position_on_duplicate(store):
    item = store.add_text("pinned text")
    store.toggle_pin(item.id)
    pinned_before = store.get(item.id).created_at
    time.sleep(0.01)
    store.add_text("pinned text")
    assert store.get(item.id).created_at == pinned_before


def test_carriage_returns_are_normalised(store):
    item = store.add_text("a\r\nb\rc")
    assert store.get(item.id).content == "a\nb\nc"


def test_pruning_keeps_the_newest_items():
    store = HistoryStore(config=Config(max_items=10))
    try:
        for index in range(15):
            store.add_text(f"item {index}")
        assert store.count() == 10
        previews = [item.preview for item in store.list()]
        assert "item 14" in previews
        assert "item 0" not in previews
    finally:
        store.close()


def test_pruning_never_removes_pinned_items():
    store = HistoryStore(config=Config(max_items=10))
    try:
        pinned = store.add_text("keep me")
        store.toggle_pin(pinned.id)
        for index in range(20):
            store.add_text(f"item {index}")
        assert store.get(pinned.id) is not None
        assert store.pinned_count() == 1
    finally:
        store.close()


def test_pin_limit_unpins_the_oldest():
    store = HistoryStore(config=Config(pin_limit=2))
    try:
        items = [store.add_text(f"item {index}") for index in range(3)]
        for item in items:
            store.toggle_pin(item.id)
        assert store.pinned_count() == 2
        assert store.get(items[0].id).pinned is False
    finally:
        store.close()


def test_toggle_pin_returns_the_new_state(store):
    item = store.add_text("x")
    assert store.toggle_pin(item.id) is True
    assert store.get(item.id).pinned is True
    assert store.toggle_pin(item.id) is False


def test_toggle_pin_on_missing_item(store):
    assert store.toggle_pin(9999) is False


def test_delete_and_clear(store):
    first = store.add_text("a")
    store.add_text("b")
    pinned = store.add_text("c")
    store.toggle_pin(pinned.id)
    assert store.delete(first.id) is True
    assert store.delete(first.id) is False
    assert store.count() == 2
    assert store.clear() == 1  # only the unpinned one
    assert store.count() == 1
    assert store.clear(keep_pinned=False) == 1
    assert store.count() == 0


def test_touch_moves_an_item_to_the_top(store):
    first = store.add_text("old")
    store.add_text("new")
    store.touch(first.id)
    assert store.list()[0].id == first.id


def test_search_matches_preview_and_content(store):
    store.add_text("https://example.com/path")
    store.add_text("unrelated")
    assert len(store.list(query="example")) == 1
    assert store.list(query="nothing") == []


def test_search_escapes_like_wildcards(store):
    store.add_text("100% done")
    store.add_text("plain")
    assert len(store.list(query="%")) == 1
    assert len(store.list(query="_")) == 0
    assert len(store.list(query="100%")) == 1


def test_search_is_limited(store):
    for index in range(10):
        store.add_text(f"same {index}")
    assert len(store.list(query="same", limit=3)) == 3


def test_images_are_stored_as_blobs_with_metadata(store):
    payload = b"\x89PNG\r\n\x1a\n" + b"0" * 256
    item = store.add_image(payload, 320, 200)
    assert item.type is ContentType.IMAGE
    assert item.metadata["width"] == 320
    assert item.size_label == "320×200"
    assert store.get_image_bytes(item.id) == payload
    assert store.get_text(item.id) is None


def test_oversized_images_are_rejected():
    store = HistoryStore(config=Config(max_image_size_kb=64))
    try:
        assert store.add_image(b"x" * (65 * 1024)) is None
        assert store.count() == 0
    finally:
        store.close()


def test_files_are_joined_and_previewed(store):
    item = store.add_files(["file:///home/user/a.txt", "file:///home/user/b.txt"])
    assert item.type is ContentType.FILE
    assert item.metadata["count"] == 2
    assert "a.txt" in item.preview
    assert store.get_text(item.id).count("\n") == 1
    assert store.add_files([]) is None


def test_stats(store):
    store.add_text("hello")
    stats = store.stats()
    assert stats["items"] == 1
    assert stats["pinned"] == 0
    assert stats["payload_bytes"] == 5
    assert stats["database_bytes"] > 0


def test_listeners_are_notified(store):
    calls = []
    store.add_listener(lambda: calls.append(1))
    store.add_text("a")
    store.add_text("b")
    item = store.get(store.list()[0].id)
    store.delete(item.id)
    assert len(calls) == 3


def test_failing_listener_does_not_break_storage(store):
    def broken():
        raise RuntimeError("boom")

    store.add_listener(broken)
    assert store.add_text("still works") is not None


def test_remove_listener(store):
    calls = []
    listener = lambda: calls.append(1)  # noqa: E731
    store.add_listener(listener)
    store.remove_listener(listener)
    store.add_text("a")
    assert calls == []


def test_concurrent_writes_are_safe(store):
    errors: list[BaseException] = []

    def worker(prefix: str) -> None:
        try:
            for index in range(20):
                store.add_text(f"{prefix}-{index}")
        except BaseException as exc:  # pragma: no cover - only on failure
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("a", "b", "c")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert store.count() == 60


def test_wal_mode_and_timeout_are_enabled(store):
    store.add_text("x")
    connection = store._connection()  # noqa: SLF001 - white box check on purpose
    assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_close_is_idempotent(store):
    store.add_text("x")
    store.close()
    store.close()


def test_schema_version_is_written(store):
    version = store._connection().execute("PRAGMA user_version").fetchone()[0]  # noqa: SLF001
    assert version == SCHEMA_VERSION


def test_legacy_database_is_migrated(isolated_home, tmp_path):
    from ubuntu_clipboard.config import config_dir, database_path

    config_dir().mkdir(parents=True, exist_ok=True)
    legacy = config_dir() / "history.db"
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"legacy"
    with sqlite3.connect(legacy) as connection:
        connection.executescript(LEGACY_SCHEMA)
        connection.execute(
            "INSERT INTO items (hash, type, content, preview, pinned, created_at, metadata) "
            "VALUES ('h1', 'text', 'hello', 'hello', 0, 1.0, '{}')"
        )
        connection.execute(
            "INSERT INTO items (hash, type, content, preview, pinned, created_at, metadata) "
            "VALUES ('h2', 'image', ?, 'image', 1, 2.0, '{}')",
            (base64.b64encode(image_bytes).decode(),),
        )
        connection.commit()

    store = HistoryStore()
    try:
        assert database_path().is_file()
        assert not legacy.exists()
        assert store.count() == 2
        items = store.list()
        image = next(item for item in items if item.type is ContentType.IMAGE)
        assert store.get_image_bytes(image.id) == image_bytes
        assert store.get(image.id).content is None
        assert store.get(store.list()[1].id).size_bytes == len("hello")
    finally:
        store.close()
