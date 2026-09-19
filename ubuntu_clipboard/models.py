"""Clipboard item model, content type detection and previews.

This module has no GUI or database dependency and is therefore fully unit tested.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .config import PREVIEW_LENGTH
from .i18n import t

#: Number of characters of the payload used to fingerprint an image.
HASH_PREFIX = 4096


class ContentType(str, Enum):
    TEXT = "text"
    CODE = "code"
    LINK = "link"
    COLOR = "color"
    IMAGE = "image"
    FILE = "file"

    @classmethod
    def parse(cls, value: str) -> ContentType:
        try:
            return cls(value)
        except ValueError:
            return cls.TEXT


CODE_HINT = re.compile(
    r"(?:^|\n)\s*(?:def |class |import |from \S+ import|function |const |let |var |"
    r"#include|public\s+class|private\s+class|SELECT\s+|INSERT\s+INTO|CREATE\s+TABLE|"
    r"package\s+\w+;|using\s+\w+;|fn\s+\w+\s*\(|func\s+\w+\s*\(|console\.log|"
    r"<\?php|#!/|\)\s*\{|;\s*$)",
    re.IGNORECASE,
)
URL_RE = re.compile(r"^(?:https?|ftp)://\S+$|^www\.\S+\.\S+$", re.IGNORECASE)
LOOSE_URL_RE = re.compile(r"(?:https?|ftp)://\S+", re.IGNORECASE)
COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
FILE_URI_RE = re.compile(r"^file://\S+", re.MULTILINE)
ABSOLUTE_PATH_RE = re.compile(r"^(?:/[^\s/]+){2,}/?$|^/[^\s]*\.[A-Za-z0-9]{1,8}$")
WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")


def content_hash(payload: str | bytes) -> str:
    """Stable 16 character fingerprint of a clipboard payload."""
    if isinstance(payload, str):
        payload = payload.encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()[:16]


def image_hash(png_bytes: bytes) -> str:
    """Cheap fingerprint for images (hashing megabytes on every copy is wasteful)."""
    digest = hashlib.sha256()
    digest.update(png_bytes[:HASH_PREFIX])
    digest.update(str(len(png_bytes)).encode())
    return digest.hexdigest()[:16]


def detect_type(content: str, mime: str = "text/plain", has_image: bool = False) -> ContentType:
    """Classify a clipboard payload."""
    if has_image or mime.startswith("image/"):
        return ContentType.IMAGE
    stripped = content.strip()
    if not stripped:
        return ContentType.TEXT
    if FILE_URI_RE.search(stripped):
        return ContentType.FILE
    if "\n" not in stripped and ABSOLUTE_PATH_RE.match(stripped):
        return ContentType.FILE
    if COLOR_RE.match(stripped):
        return ContentType.COLOR
    single_line = "\n" not in stripped
    if (single_line and URL_RE.match(stripped)) or (
        len(stripped) < 500 and stripped.count(" ") < 3 and LOOSE_URL_RE.match(stripped)
    ):
        return ContentType.LINK
    if CODE_HINT.search(stripped):
        return ContentType.CODE
    return ContentType.TEXT


def preview_text(content: str, limit: int = PREVIEW_LENGTH) -> str:
    """Single line, whitespace collapsed preview used in the list and search."""
    collapsed = WHITESPACE_RE.sub(" ", content.replace("\r", "")).strip()
    collapsed = re.sub(r"\n{2,}", "\n", collapsed)
    collapsed = collapsed.replace("\n", " ⏎ ")
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def uri_to_path(uri: str) -> str:
    """Best effort ``file://`` URI to filesystem path conversion."""
    from urllib.parse import unquote, urlparse

    parsed = urlparse(uri)
    if parsed.scheme in ("", "file"):
        return unquote(parsed.path or uri)
    return uri


def file_preview(uris: list[str], limit: int = 3) -> str:
    """``name.png``, ``a.txt +2 more`` style summary for file lists."""
    names = [uri.rstrip("/").rsplit("/", 1)[-1] or uri for uri in uris]
    head = ", ".join(names[:limit])
    if len(names) > limit:
        head += " " + t("file.more", count=len(names) - limit)
    return head


@dataclass
class ClipboardItem:
    """One entry of the clipboard history.

    ``content`` is *not* loaded by :meth:`HistoryStore.list`; it is fetched on
    demand with :meth:`HistoryStore.get` so that listing never pulls megabytes
    of base64 images from SQLite.
    """

    id: int
    type: ContentType
    preview: str
    created_at: float
    pinned: bool = False
    size_bytes: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    content: str | None = None

    @property
    def display_type(self) -> str:
        return t(f"type.{self.type.value}")

    def relative_time(self, now: float | None = None) -> str:
        """Human readable age, localised."""
        now = time.time() if now is None else now
        delta = max(0.0, now - self.created_at)
        if delta < 60:
            return t("time.now")
        if delta < 3600:
            return t("time.minutes", count=int(delta // 60))
        if delta < 86400:
            return t("time.hours", count=int(delta // 3600))
        if delta < 604800:
            return t("time.days", count=int(delta // 86400))
        return time.strftime("%Y-%m-%d", time.localtime(self.created_at))

    @property
    def size_label(self) -> str:
        if self.type is ContentType.IMAGE:
            width = self.metadata.get("width")
            height = self.metadata.get("height")
            if width and height:
                return f"{width}×{height}"
        return format_size(self.size_bytes)

    def uris(self) -> list[str]:
        if self.type is not ContentType.FILE or not self.content:
            return []
        return [line for line in self.content.splitlines() if line.strip()]


def format_size(num_bytes: int) -> str:
    """Compact byte size, no locale dependency."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB".replace(".0 KB", " KB")
    return f"{num_bytes / (1024 * 1024):.1f} MB".replace(".0 MB", " MB")
