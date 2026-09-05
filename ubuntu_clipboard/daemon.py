"""
daemon.py — دیمن مانیتور کلیپ‌بورد
- هر 300ms کلیپ‌بورد را چک می‌کند
- تغییرات را در HistoryManager ذخیره می‌کند
- به صورت سرویس پس‌زمینه یا داخل اپ اصلی اجرا می‌شود
"""

from __future__ import annotations
import time
import threading
import hashlib
import base64
import os
from typing import Optional

from .history import HistoryManager
from .config import get_config
from .clipboard import read_text_fallback, read_image_fallback, detect_session

class ClipboardDaemon:
    def __init__(self, history: Optional[HistoryManager] = None, interval: float = 0.35):
        self.history = history or HistoryManager()
        self.interval = interval
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_hash: Optional[str] = None
        self._last_text: Optional[str] = None
        # GTK monitor (if available)
        self._gtk_monitor = None

    def _hash(self, s: str) -> str:
        return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()[:16]

    def _poll_once(self):
        # try image first? text is more common
        # If image mime available and size > threshold we treat as image
        img = None
        # quick detect image via wl-paste type list
        try:
            img = read_image_fallback()
        except Exception:
            img = None

        if img and len(img) > 500:
            # image clipboard
            try:
                b64 = base64.b64encode(img).decode("ascii")
                h = hashlib.sha256(img).hexdigest()[:16]
                if h == self._last_hash:
                    return
                self._last_hash = h
                # we downscale preview later in UI; store full b64 (maybe large)
                # guard size
                if len(b64) > 800_000:  # ~600KB image
                    # skip huge
                    return
                self.history.add_image_base64(b64)
                self._last_text = None
                return
            except Exception:
                pass

        txt = None
        try:
            txt = read_text_fallback()
        except Exception:
            txt = None

        if txt is None:
            return
        # normalize
        txt = txt.replace("\r\n", "\n")
        # ignore empty / whitespace only
        if not txt.strip():
            return
        # ignore if same as last
        h = self._hash(txt)
        if h == self._last_hash:
            return
        # some apps spam clipboard with same selection; also ignore very short if repeated?
        self._last_hash = h
        self._last_text = txt
        self.history.add(txt)

    def _try_gtk_monitor(self) -> bool:
        """اگر GTK4 در دسترس بود، از سیگنال changed استفاده کن (کارآمدتر از polling)."""
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            gi.require_version("Gdk", "4.0")
            from gi.repository import Gdk, GLib
            disp = Gdk.Display.get_default()
            if not disp:
                return False
            cb = disp.get_clipboard()

            def on_changed(clipboard):
                # read asynchronously
                def read_cb(clip, result):
                    try:
                        val = clip.read_text_finish(result)
                        if val:
                            h = self._hash(val)
                            if h == self._last_hash:
                                return
                            self._last_hash = h
                            self.history.add(val)
                    except Exception:
                        pass
                # also try image?
                clipboard.read_text_async(None, read_cb)
                # re-arm?
            cb.connect("changed", on_changed)
            self._gtk_monitor = cb
            return True
        except Exception as e:
            return False

    def start(self, use_gtk: bool = True):
        if self._running:
            return
        self._running = True

        if use_gtk:
            self._try_gtk_monitor()

        def loop():
            while self._running:
                try:
                    self._poll_once()
                except Exception:
                    pass
                time.sleep(self.interval)

        self._thread = threading.Thread(target=loop, name="clipboard-daemon", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def run_forever(self):
        self.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

def main():
    """اجرای مستقل دیمن: python -m ubuntu_clipboard.daemon"""
    import argparse, sys
    ap = argparse.ArgumentParser(description="Ubuntu Clipboard Daemon — Win+V")
    ap.add_argument("--interval", type=float, default=0.35, help="poll interval seconds")
    ap.add_argument("--once", action="store_true", help="single poll for testing")
    args = ap.parse_args()

    hm = HistoryManager()
    d = ClipboardDaemon(history=hm, interval=args.interval)
    if args.once:
        d._poll_once()
        print(f"items: {hm.count()}")
        for it in hm.list(limit=5):
            print(f"[{it.type}] {it.preview[:80]}  pinned={it.pinned}")
        return
    print(f"✓ دیمن کلیپ‌بورد اجرا شد — {detect_session()} — هر {args.interval}s چک می‌شود. Ctrl+C برای خروج.")
    print(f"  DB: {hm.db_path}")
    d.run_forever()

if __name__ == "__main__":
    main()
