"""
history.py — لایه ذخیره‌سازی کلیپ‌بورد (SQLite)
- تشخیص نوع محتوا: text / code / link / color / image / file
- Pin / Unpin / Delete / Clear
- جستجو و مرتب‌سازی
"""

from __future__ import annotations
import sqlite3
import hashlib
import json
import time
import re
import base64
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List
from .config import CONFIG_DIR, DB_PATH, get_config

# ─────────── تشخیص نوع ───────────
CODE_HINT = re.compile(r"(\bdef |class |import |from |function|=>|#include|public\s+class|console\.log|SELECT\s+\*|{\s*\n|\bfor\s*\(|\bif\s*\()")
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
COLOR_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
FILE_RE = re.compile(r"^file://")

def detect_type(content: str, has_image: bool = False, mime: str = "") -> str:
    if has_image or mime.startswith("image/"):
        return "image"
    s = content.strip()
    if not s:
        return "text"
    if FILE_RE.match(s) or (s.startswith("/") and "\n" not in s and ("." in s or s.count("/") > 2)):
        # heuristic for file uri list
        if "file://" in s or s.startswith("/home") or s.startswith("/tmp"):
            return "file"
    if COLOR_RE.match(s.strip()):
        return "color"
    if URL_RE.fullmatch(s.strip()) or (URL_RE.search(s) and len(s) < 500 and s.count(" ") < 3):
        return "link"
    if CODE_HINT.search(s) and ("\n" in s or len(s) > 80):
        return "code"
    return "text"

def preview_text(content: str, limit: int = 180) -> str:
    t = content.replace("\r", "").strip()
    # collapse whitespace for preview
    t = re.sub(r"\s+", " ", t)
    return t[:limit] + ("…" if len(t) > limit else "")

def content_hash(content: str | bytes) -> str:
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()[:16]

@dataclass
class ClipboardItem:
    id: int
    hash: str
    type: str  # text|image|file|code|link|color
    content: str  # text or base64 image or json file list
    preview: str
    pinned: bool
    created_at: float
    metadata: dict

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.created_at
        if diff < 60:
            return "اکنون"
        if diff < 3600:
            return f"{int(diff//60)} دقیقه پیش"
        if diff < 86400:
            return f"{int(diff//3600)} ساعت پیش"
        if diff < 604800:
            return f"{int(diff//86400)} روز پیش"
        return time.strftime("%Y/%m/%d", time.localtime(self.created_at))

class HistoryManager:
    def __init__(self, db_path: Path = DB_PATH):
        # handle str path for robustness
        if isinstance(db_path, str):
            db_path = Path(db_path)
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self):
        c = sqlite3.connect(str(self.db_path), check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self):
        with self._conn() as con:
            con.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hash TEXT UNIQUE,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                preview TEXT NOT NULL,
                pinned INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                metadata TEXT DEFAULT '{}'
            )
            """)
            con.execute("CREATE INDEX IF NOT EXISTS idx_pinned ON items(pinned)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_created ON items(created_at DESC)")

    # ── CRUD ──
    def add(self, content: str, mime: str = "text/plain", has_image: bool = False, metadata: dict | None = None) -> Optional[ClipboardItem]:
        cfg = get_config()
        if not content:
            return None
        # size guard
        if len(content.encode("utf-8")) > cfg.max_item_size_kb * 1024:
            # truncate for text
            if not has_image:
                content = content[: cfg.max_item_size_kb * 1024]

        if not has_image and cfg.should_ignore(content):
            return None

        h = content_hash(content if not has_image else content[:1024])
        typ = detect_type(content, has_image, mime)
        prev = preview_text(content) if not has_image else "تصویر"
        meta = metadata or {}
        if typ == "image" and "width" not in meta and not has_image:
            # estimate
            pass

        now = time.time()
        with self._conn() as con:
            # deduplicate: if same hash exists, bump its time and move to top (unless pinned)
            row = con.execute("SELECT id, pinned FROM items WHERE hash=?", (h,)).fetchone()
            if row:
                # move to top by updating created_at, keep pinned status
                con.execute("UPDATE items SET created_at=?, preview=?, content=? WHERE hash=?", (now, prev, content, h))
                con.commit()
                return self.get_by_hash(h)
            # enforce limit
            count = con.execute("SELECT COUNT(*) FROM items WHERE pinned=0").fetchone()[0]
            if count >= cfg.max_items:
                # delete oldest non-pinned
                con.execute("DELETE FROM items WHERE id IN (SELECT id FROM items WHERE pinned=0 ORDER BY created_at ASC LIMIT 1)")
            con.execute(
                "INSERT INTO items (hash,type,content,preview,pinned,created_at,metadata) VALUES (?,?,?,?,?,?,?)",
                (h, typ, content, prev, 0, now, json.dumps(meta, ensure_ascii=False))
            )
            con.commit()
            return self.get_by_hash(h)

    def add_image_base64(self, b64: str, width: int = 0, height: int = 0) -> Optional[ClipboardItem]:
        # store as data uri style base64
        h = content_hash(b64[:2048].encode())
        with self._conn() as con:
            row = con.execute("SELECT id FROM items WHERE hash=?", (h,)).fetchone()
            if row:
                con.execute("UPDATE items SET created_at=? WHERE hash=?", (time.time(), h))
                con.commit()
                return self.get_by_hash(h)
            cfg = get_config()
            count = con.execute("SELECT COUNT(*) FROM items WHERE pinned=0").fetchone()[0]
            if count >= cfg.max_items:
                con.execute("DELETE FROM items WHERE id IN (SELECT id FROM items WHERE pinned=0 ORDER BY created_at ASC LIMIT 1)")
            meta = json.dumps({"width": width, "height": height}, ensure_ascii=False)
            con.execute("INSERT INTO items (hash,type,content,preview,pinned,created_at,metadata) VALUES (?,?,?,?,?,?,?)",
                        (h, "image", b64, "تصویر", 0, time.time(), meta))
            con.commit()
            return self.get_by_hash(h)

    def get_by_hash(self, h: str) -> Optional[ClipboardItem]:
        with self._conn() as con:
            r = con.execute("SELECT * FROM items WHERE hash=?", (h,)).fetchone()
            if not r:
                return None
            return self._row_to_item(r)

    def _row_to_item(self, r) -> ClipboardItem:
        return ClipboardItem(
            id=r["id"], hash=r["hash"], type=r["type"], content=r["content"],
            preview=r["preview"], pinned=bool(r["pinned"]),
            created_at=r["created_at"], metadata=json.loads(r["metadata"] or "{}")
        )

    def list(self, query: str = "", limit: int = 200) -> List[ClipboardItem]:
        with self._conn() as con:
            if query.strip():
                q = f"%{query.strip()}%"
                rows = con.execute(
                    "SELECT * FROM items WHERE preview LIKE ? OR content LIKE ? ORDER BY pinned DESC, created_at DESC LIMIT ?",
                    (q, q, limit)
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM items ORDER BY pinned DESC, created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            return [self._row_to_item(r) for r in rows]

    def list_pinned(self) -> List[ClipboardItem]:
        with self._conn() as con:
            rows = con.execute("SELECT * FROM items WHERE pinned=1 ORDER BY created_at DESC").fetchall()
            return [self._row_to_item(r) for r in rows]

    def toggle_pin(self, item_id: int) -> bool:
        cfg = get_config()
        with self._conn() as con:
            r = con.execute("SELECT pinned FROM items WHERE id=?", (item_id,)).fetchone()
            if not r:
                return False
            new_val = 0 if r["pinned"] else 1
            if new_val == 1:
                pinned_count = con.execute("SELECT COUNT(*) FROM items WHERE pinned=1").fetchone()[0]
                if pinned_count >= cfg.pin_limit:
                    # unpin oldest
                    con.execute("UPDATE items SET pinned=0 WHERE id=(SELECT id FROM items WHERE pinned=1 ORDER BY created_at ASC LIMIT 1)")
            con.execute("UPDATE items SET pinned=?, created_at=? WHERE id=?", (new_val, time.time(), item_id))
            con.commit()
            return bool(new_val)

    def delete(self, item_id: int):
        with self._conn() as con:
            con.execute("DELETE FROM items WHERE id=?", (item_id,))
            con.commit()

    def clear(self, keep_pinned: bool = True):
        with self._conn() as con:
            if keep_pinned:
                con.execute("DELETE FROM items WHERE pinned=0")
            else:
                con.execute("DELETE FROM items")
            con.commit()

    def get(self, item_id: int) -> Optional[ClipboardItem]:
        with self._conn() as con:
            r = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            return self._row_to_item(r) if r else None

    def count(self) -> int:
        with self._conn() as con:
            return con.execute("SELECT COUNT(*) FROM items").fetchone()[0]

    def seed_demo_if_empty(self):
        """اگر DB خالی بود، چند آیتم نمونه زیبا اضافه کن برای نمایش اولیه."""
        if self.count() > 0:
            return
        samples = [
            ("سلام دنیا! این کلیپ‌بورد اوبونتو است — با Win+V باز می‌شود ✨", {}),
            ("https://github.com/ahmad75naraghi/ubuntu-clipboard", {}),
            ("#4f7cff", {}),
            ("def fibonacci(n):\n    a, b = 0, 1\n    for _ in range(n):\n        yield a\n        a, b = b, a+b", {}),
            ("file:///home/user/Documents/report.pdf\nfile:///home/user/Pictures/screenshot.png", {}),
            ("git commit -m \"feat: clipboard like Windows 11 — Win+V on Ubuntu\" && git push", {}),
            ("لورم ایپسوم متن ساختگی با تولید سادگی نامفهوم از صنعت چاپ — برای تست پیش‌نمایش متن‌های طولانی و فارسی.", {}),
        ]
        for content, meta in samples:
            try:
                self.add(content, metadata=meta)
                time.sleep(0.02)
            except Exception:
                pass
        # یک تصویر نمونه (پیکسل آبی)
        try:
            # یک PNG آبی 120x80 کوچک به صورت base64
            import base64
            # minimal 1x1 blue png base64 (we'll generate via Pillow if available)
            try:
                from PIL import Image
                import io
                img = Image.new("RGB", (320, 180), "#4f7cff")
                # draw some text? simple
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode()
                self.add_image_base64(b64, width=320, height=180)
            except Exception:
                # fallback 1x1
                tiny_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
                self.add_image_base64(tiny_b64, width=1, height=1)
        except Exception:
            pass
        # سنجاق کردن اولین آیتم به عنوان نمونه
        try:
            first = self.list(limit=1)
            if first:
                self.toggle_pin(first[0].id)
        except Exception:
            pass
