"""Content classification, previews and the item model."""

from __future__ import annotations

import time

import pytest
from ubuntu_clipboard import i18n
from ubuntu_clipboard.models import (
    ClipboardItem,
    ContentType,
    content_hash,
    detect_type,
    file_preview,
    format_size,
    image_hash,
    preview_text,
    uri_to_path,
)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("hello world", ContentType.TEXT),
        ("https://example.com/page", ContentType.LINK),
        ("www.example.com", ContentType.LINK),
        ("#ff5500", ContentType.COLOR),
        ("#Ff5500aa", ContentType.COLOR),
        ("def main():\n    return 0", ContentType.CODE),
        ("SELECT * FROM users", ContentType.CODE),
        ("file:///home/user/report.pdf", ContentType.FILE),
        ("/home/user/Documents/report.pdf", ContentType.FILE),
        ("multi\nline\ntext", ContentType.TEXT),
        ("12345", ContentType.TEXT),
        ("#heading", ContentType.TEXT),
    ],
)
def test_type_detection(payload, expected):
    assert detect_type(payload) is expected


def test_type_detection_prefers_images():
    assert detect_type("", mime="image/png") is ContentType.IMAGE
    assert detect_type("data", has_image=True) is ContentType.IMAGE


def test_content_type_parse_is_forgiving():
    assert ContentType.parse("link") is ContentType.LINK
    assert ContentType.parse("nonsense") is ContentType.TEXT


def test_preview_collapses_whitespace_and_truncates():
    assert preview_text("a\n\n\nb") == "a ⏎ b"
    assert preview_text("hello   world") == "hello world"
    long = preview_text("x" * 500)
    assert len(long) <= 180
    assert long.endswith("…")


def test_hashes_are_stable_and_short():
    assert content_hash("abc") == content_hash("abc")
    assert len(content_hash("abc")) == 16
    assert content_hash("abc") != content_hash("abd")
    assert image_hash(b"a" * 10) == image_hash(b"a" * 10)
    assert image_hash(b"a" * 10) != image_hash(b"a" * 11)


def test_format_size():
    assert format_size(512) == "512 B"
    assert format_size(2048) == "2 KB"
    assert format_size(5 * 1024 * 1024) == "5 MB"


def test_uri_to_path():
    assert uri_to_path("file:///home/user/a%20b.txt") == "/home/user/a b.txt"
    assert uri_to_path("/tmp/plain.txt") == "/tmp/plain.txt"
    assert uri_to_path("https://example.com") == "https://example.com"


def test_file_preview_lists_names():
    assert file_preview(["file:///a/report.pdf"]) == "report.pdf"
    preview = file_preview(["file:///a/1", "file:///b/2", "file:///c/3", "file:///d/4"])
    assert "1" in preview and "more" in preview


def test_relative_time_uses_the_active_language():
    i18n.set_language("en")
    now = time.time()
    item = ClipboardItem(id=1, type=ContentType.TEXT, preview="x", created_at=now)
    assert item.relative_time(now) == "just now"
    assert item.relative_time(now + 120) == "2 min ago"
    assert item.relative_time(now + 7200) == "2 h ago"
    assert item.relative_time(now + 200_000) == "2 d ago"
    assert item.relative_time(now + 2_000_000) == time.strftime("%Y-%m-%d", time.localtime(now))
    i18n.set_language("fa")
    assert item.relative_time(now).startswith("اکنون")
    i18n.set_language("en")


def test_display_type_is_translated():
    i18n.set_language("fa")
    item = ClipboardItem(id=1, type=ContentType.CODE, preview="", created_at=0.0)
    assert item.display_type == "کد"
    i18n.set_language("en")
    assert item.display_type == "CODE"


def test_size_label_for_images_uses_dimensions():
    item = ClipboardItem(
        id=1,
        type=ContentType.IMAGE,
        preview="image",
        created_at=0.0,
        size_bytes=4096,
        metadata={"width": 640, "height": 480},
    )
    assert item.size_label == "640×480"
    plain = ClipboardItem(id=2, type=ContentType.TEXT, preview="x", created_at=0.0, size_bytes=2048)
    assert plain.size_label == "2 KB"


def test_uris_are_only_exposed_for_file_items():
    text = ClipboardItem(id=1, type=ContentType.TEXT, preview="x", created_at=0.0, content="a")
    assert text.uris() == []
    files = ClipboardItem(
        id=2,
        type=ContentType.FILE,
        preview="a.txt",
        created_at=0.0,
        content="file:///a.txt\n\nfile:///b.txt",
    )
    assert files.uris() == ["file:///a.txt", "file:///b.txt"]
