"""SQLite backed clipboard history.

Design notes
------------
* One connection per thread, opened in autocommit mode with ``WAL`` journaling so
  the UI and the clipboard monitor never block each other. Every statement gets
  an explicit ``busy_timeout`` instead of raising ``database is locked``.
* Heavy payloads (images) live in a dedicated ``BLOB`` column and are never
  selected by :meth:`HistoryStore.list`; the window asks for them on demand.
  The v1 schema stored base64 inside the text column, which made every listing
  pull megabytes out of the database and bloated the file by ~33%.
* The schema is versioned with ``PRAGMA user_version`` and migrated in place,
  keeping the history of existing installations.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from .config import Config, database_path, get_config, legacy_database_path
from .models import (
    ClipboardItem,
    ContentType,
    content_hash,
    detect_type,
    file_preview,
    image_hash,
    preview_text,
    uri_to_path,
)

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2

#: Executed one by one: ``executescript`` would commit the surrounding transaction.
_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS items (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        hash        TEXT    NOT NULL UNIQUE,
        type        TEXT    NOT NULL,
        preview     TEXT    NOT NULL DEFAULT '',
        content     TEXT,
        image       BLOB,
        pinned      INTEGER NOT NULL DEFAULT 0,
        size_bytes  INTEGER NOT NULL DEFAULT 0,
        created_at  REAL    NOT NULL,
        metadata    TEXT    NOT NULL DEFAULT '{}'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_items_pinned ON items(pinned, created_at DESC)",
)

_LIST_COLUMNS = "id, hash, type, preview, pinned, size_bytes, created_at, metadata"
#: Same, plus the text payload — but never the image BLOB.
_DETAIL_COLUMNS = "id, hash, type, preview, content, pinned, size_bytes, created_at, metadata"


def _escape_like(value: str) -> str:
    """Escape ``%``, ``_`` and the escape character itself."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class HistoryStore:
    """CRUD facade for the clipboard history database."""

    def __init__(self, db_path: Path | str | None = None, config: Config | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else database_path()
        self._config = config
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._lock = threading.RLock()
        self._listeners: list[Callable[[], None]] = []
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_database_file()
        self._initialize()

    # ── infrastructure ─────────────────────────────────────────────────────
    @property
    def config(self) -> Config:
        return self._config if self._config is not None else get_config()

    def set_config(self, config: Config) -> None:
        self._config = config

    def _migrate_legacy_database_file(self) -> None:
        """v1 kept the database inside the configuration directory."""
        legacy = legacy_database_path()
        if self.db_path.exists() or not legacy.is_file():
            return
        if self.db_path != database_path():
            return
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            legacy.replace(self.db_path)
            log.info("moved history database from %s to %s", legacy, self.db_path)
        except OSError as exc:  # pragma: no cover - defensive
            log.warning("could not move legacy database: %s", exc)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._connect()
            self._local.connection = connection
            with self._lock:
                self._connections.append(connection)
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connection()
        with self._lock:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")

    def _initialize(self) -> None:
        connection = self._connection()
        with self._lock:
            connection.execute("BEGIN IMMEDIATE")
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                tables = {
                    row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                if "items" not in tables:
                    for statement in _SCHEMA_STATEMENTS:
                        connection.execute(statement)
                elif version < SCHEMA_VERSION:
                    self._upgrade(connection, version)
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
        # Housekeeping: keep the on-disk file small after deletions.
        with self._lock:
            connection.execute("PRAGMA optimize")

    def _upgrade(self, connection: sqlite3.Connection, version: int) -> None:
        columns = {row[1]: row for row in connection.execute("PRAGMA table_info(items)")}
        if "size_bytes" not in columns:
            connection.execute("ALTER TABLE items ADD COLUMN size_bytes INTEGER NOT NULL DEFAULT 0")
        if "image" not in columns:
            connection.execute("ALTER TABLE items ADD COLUMN image BLOB")
        connection.execute("UPDATE items SET size_bytes=length(content) WHERE size_bytes=0")
        # The v1 column was TEXT NOT NULL; keep the migration happy either way.
        empty_content = "" if (columns.get("content") and columns["content"][3]) else None
        # v1 stored images as base64 text; move them into the BLOB column.
        rows = connection.execute(
            "SELECT id, content FROM items WHERE type='image' AND image IS NULL AND content IS NOT NULL"
        ).fetchall()
        for row in rows:
            try:
                blob = base64.b64decode(row["content"], validate=False)
            except (ValueError, TypeError):
                blob = None
            if blob:
                connection.execute(
                    "UPDATE items SET image=?, content=?, size_bytes=? WHERE id=?",
                    (blob, empty_content, len(blob), row["id"]),
                )
            else:
                connection.execute("DELETE FROM items WHERE id=?", (row["id"],))
        for statement in _SCHEMA_STATEMENTS[1:]:
            connection.execute(statement)
        log.info("history database upgraded from schema %s to %s", version, SCHEMA_VERSION)

    def close(self) -> None:
        """Close every connection opened by this store."""
        with self._lock:
            for connection in self._connections:
                with contextlib.suppress(sqlite3.Error):  # pragma: no cover - defensive
                    connection.close()
            self._connections.clear()
            self._local = threading.local()

    # ── change notification ────────────────────────────────────────────────
    def add_listener(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        with suppress(ValueError):
            self._listeners.remove(callback)

    def _notify(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:  # pragma: no cover - listener errors must not break storage
                log.exception("history listener failed")

    # ── writes ─────────────────────────────────────────────────────────────
    def add_text(self, text: str, mime: str = "text/plain") -> ClipboardItem | None:
        """Record a text payload. Returns ``None`` when it is filtered out."""
        if not self.config.should_capture(text):
            return None
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        size = len(normalized.encode("utf-8", errors="replace"))
        item_type = detect_type(normalized, mime=mime)
        return self._insert(
            hash_value=content_hash(normalized),
            item_type=item_type,
            preview=preview_text(normalized),
            content=normalized,
            image=None,
            size_bytes=size,
            metadata={},
        )

    def add_image(self, png_bytes: bytes, width: int = 0, height: int = 0) -> ClipboardItem | None:
        """Record an image payload (PNG encoded)."""
        if not png_bytes:
            return None
        if len(png_bytes) > self.config.max_image_bytes():
            log.info("skipping %d byte image (limit %d)", len(png_bytes), self.config.max_image_bytes())
            return None
        dimensions = f"{width}×{height} " if width and height else ""
        return self._insert(
            hash_value=image_hash(png_bytes),
            item_type=ContentType.IMAGE,
            preview=f"image {dimensions}{len(png_bytes) // 1024} KB".strip(),
            content=None,
            image=png_bytes,
            size_bytes=len(png_bytes),
            metadata={"width": width, "height": height, "mime": "image/png"},
        )

    def add_files(self, uris: list[str]) -> ClipboardItem | None:
        """Record a list of clipboard URIs (copied files)."""
        uris = [uri for uri in uris if uri.strip()]
        if not uris:
            return None
        content = "\n".join(uris)
        if len(content.encode("utf-8")) > self.config.max_item_bytes():
            uris = uris[:1]
            content = uris[0]
        return self._insert(
            hash_value=content_hash(content),
            item_type=ContentType.FILE,
            preview=file_preview(uris),
            content=content,
            image=None,
            size_bytes=len(content.encode("utf-8")),
            metadata={"count": len(uris), "paths": [uri_to_path(uri) for uri in uris[:10]]},
        )

    def _insert(
        self,
        *,
        hash_value: str,
        item_type: ContentType,
        preview: str,
        content: str | None,
        image: bytes | None,
        size_bytes: int,
        metadata: dict[str, Any],
    ) -> ClipboardItem | None:
        now = time.time()
        payload = json.dumps(metadata, ensure_ascii=False)
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO items (hash, type, preview, content, image, pinned, size_bytes, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(hash) DO UPDATE SET
                    type       = excluded.type,
                    preview    = excluded.preview,
                    content    = excluded.content,
                    image      = COALESCE(excluded.image, items.image),
                    size_bytes = excluded.size_bytes,
                    metadata   = excluded.metadata,
                    created_at = CASE WHEN items.pinned = 1 THEN items.created_at ELSE excluded.created_at END
                """,
                (hash_value, item_type.value, preview, content, image, size_bytes, now, payload),
            )
            self._prune(connection)
        item = self.get_by_hash(hash_value)
        self._notify()
        return item

    def _prune(self, connection: sqlite3.Connection) -> None:
        limit = max(1, self.config.max_items)
        row = connection.execute("SELECT COUNT(*) FROM items WHERE pinned=0").fetchone()
        excess = (row[0] if row else 0) - limit
        if excess <= 0:
            return
        connection.execute(
            "DELETE FROM items WHERE id IN "
            "(SELECT id FROM items WHERE pinned=0 ORDER BY created_at ASC LIMIT ?)",
            (excess,),
        )
        log.debug("pruned %d old items", excess)

    def touch(self, item_id: int) -> None:
        """Move an item to the top of the list (used after pasting it)."""
        with self._transaction() as connection:
            connection.execute("UPDATE items SET created_at=? WHERE id=?", (time.time(), item_id))
        self._notify()

    def toggle_pin(self, item_id: int) -> bool:
        """Pin/unpin an item, returning the new state."""
        with self._transaction() as connection:
            row = connection.execute("SELECT pinned FROM items WHERE id=?", (item_id,)).fetchone()
            if row is None:
                return False
            new_value = 0 if row["pinned"] else 1
            if new_value == 1:
                limit = max(1, self.config.pin_limit)
                pinned = connection.execute("SELECT COUNT(*) FROM items WHERE pinned=1").fetchone()[0]
                if pinned >= limit:
                    connection.execute(
                        "UPDATE items SET pinned=0 WHERE id IN "
                        "(SELECT id FROM items WHERE pinned=1 ORDER BY created_at ASC LIMIT ?)",
                        (pinned - limit + 1,),
                    )
            connection.execute(
                "UPDATE items SET pinned=?, created_at=? WHERE id=?",
                (new_value, time.time(), item_id),
            )
        self._notify()
        return bool(new_value)

    def delete(self, item_id: int) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute("DELETE FROM items WHERE id=?", (item_id,))
            deleted = cursor.rowcount > 0
        if deleted:
            self._notify()
        return deleted

    def clear(self, keep_pinned: bool | None = None) -> int:
        """Remove history. Returns the number of deleted rows."""
        if keep_pinned is None:
            keep_pinned = self.config.keep_pinned_on_clear
        with self._transaction() as connection:
            if keep_pinned:
                cursor = connection.execute("DELETE FROM items WHERE pinned=0")
            else:
                cursor = connection.execute("DELETE FROM items")
            deleted = cursor.rowcount
        self._notify()
        return max(0, deleted)

    # ── reads ──────────────────────────────────────────────────────────────
    def list(self, query: str = "", limit: int = 200) -> list[ClipboardItem]:
        """List items (most recent first, pinned first) without their payload."""
        limit = max(1, int(limit))
        connection = self._connection()
        if query.strip():
            pattern = f"%{_escape_like(query.strip())}%"
            rows = connection.execute(
                f"SELECT {_LIST_COLUMNS} FROM items "
                "WHERE preview LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\' "
                "ORDER BY pinned DESC, created_at DESC LIMIT ?",
                (pattern, pattern, limit),
            ).fetchall()
        else:
            rows = connection.execute(
                f"SELECT {_LIST_COLUMNS} FROM items ORDER BY pinned DESC, created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def get(self, item_id: int) -> ClipboardItem | None:
        """One item with its text payload (images stay in the database)."""
        row = (
            self._connection()
            .execute(f"SELECT {_DETAIL_COLUMNS} FROM items WHERE id=?", (item_id,))
            .fetchone()
        )
        return self._row_to_item(row) if row else None

    def get_by_hash(self, hash_value: str) -> ClipboardItem | None:
        row = (
            self._connection()
            .execute(f"SELECT {_DETAIL_COLUMNS} FROM items WHERE hash=?", (hash_value,))
            .fetchone()
        )
        return self._row_to_item(row) if row else None

    def get_text(self, item_id: int) -> str | None:
        """Text payload (``None`` for images or missing rows)."""
        row = self._connection().execute("SELECT content FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            return None
        content = row["content"]
        return content if content else None

    def get_image_bytes(self, item_id: int) -> bytes | None:
        """Raw PNG payload of an image item."""
        row = self._connection().execute("SELECT image FROM items WHERE id=?", (item_id,)).fetchone()
        if not row or row["image"] is None:
            return None
        return bytes(row["image"])

    def count(self) -> int:
        """Total number of stored items."""
        row = self._connection().execute("SELECT COUNT(*) FROM items").fetchone()
        return int(row[0]) if row else 0

    def pinned_count(self) -> int:
        row = self._connection().execute("SELECT COUNT(*) FROM items WHERE pinned=1").fetchone()
        return int(row[0]) if row else 0

    def stats(self) -> dict[str, Any]:
        connection = self._connection()
        total = self.count()
        pinned = self.pinned_count()
        images = connection.execute("SELECT COUNT(*) FROM items WHERE type='image'").fetchone()[0]
        payload = connection.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM items").fetchone()[0]
        size = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {
            "items": total,
            "pinned": int(pinned),
            "images": int(images),
            "payload_bytes": int(payload),
            "database_bytes": int(size),
            "database": str(self.db_path),
        }

    def _row_to_item(self, row: sqlite3.Row) -> ClipboardItem:
        keys = row.keys()
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except (ValueError, TypeError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        content = row["content"] if "content" in keys else None
        return ClipboardItem(
            id=row["id"],
            type=ContentType.parse(row["type"]),
            preview=row["preview"] or "",
            created_at=float(row["created_at"]),
            pinned=bool(row["pinned"]),
            size_bytes=int(row["size_bytes"] or 0),
            metadata=metadata,
            # A legacy NOT NULL TEXT column leaves empty strings behind.
            content=content or None,
        )
