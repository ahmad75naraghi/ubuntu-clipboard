"""Event driven clipboard monitoring built on ``Gdk.Clipboard``.

The v1 daemon polled the clipboard four times per second by spawning up to four
``wl-paste`` processes per iteration. GDK already tells us when the selection
changes and reads it asynchronously, so this module does exactly that: no
polling, no child processes, no busy loop.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable

from .models import ClipboardItem
from .storage import HistoryStore

log = logging.getLogger(__name__)

try:  # pragma: no cover - exercised only with GTK present
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, GLib

    HAS_GTK = True
except (ImportError, ValueError):  # pragma: no cover
    Gdk = GLib = None  # type: ignore[assignment]
    HAS_GTK = False

#: MIME type used by KDE/GNOME password managers to mark "do not record this".
PASSWORD_HINT_MIME = "x-kde-passwordManagerHint"
#: A suppression that never sees a matching change event expires after this.
SUPPRESS_TIMEOUT_SECONDS = 2.0
#: Coalesce the burst of "changed" signals some applications emit.
DEBOUNCE_MS = 80
#: Delay before reading whatever is already on the clipboard at startup.
INITIAL_DELAY_MS = 600


def is_password_hint(formats: object) -> bool:
    """Whether the payload is marked as sensitive by a password manager."""
    try:
        return bool(formats.contain_mime_type(PASSWORD_HINT_MIME))  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        return False


class ClipboardMonitor:
    """Watches a :class:`Gdk.Clipboard` and forwards new payloads to the store."""

    def __init__(
        self,
        store: HistoryStore,
        on_capture: Callable[[ClipboardItem], None] | None = None,
    ) -> None:
        self.store = store
        self._on_capture = on_capture
        self._clipboard = None
        self._handler_id: int | None = None
        self._timeout_id: int = 0
        self._suppress = 0
        self._suppress_until = 0.0
        self._token = 0
        self._captured_count = 0

    # ── lifecycle ──────────────────────────────────────────────────────────
    @property
    def attached(self) -> bool:
        return self._clipboard is not None

    @property
    def captured_count(self) -> int:
        return self._captured_count

    def attach(self, clipboard: object) -> None:
        """Start listening on ``clipboard`` (a ``Gdk.Clipboard``)."""
        if not HAS_GTK:
            raise RuntimeError("GTK is not available")
        self.detach()
        self._clipboard = clipboard
        self._handler_id = clipboard.connect("changed", self._on_changed)  # type: ignore[attr-defined]
        log.debug("clipboard monitor attached")
        self._schedule_read(INITIAL_DELAY_MS)

    def detach(self) -> None:
        self._cancel_pending()
        if self._clipboard is not None and self._handler_id is not None:
            with contextlib.suppress(TypeError, ValueError):  # pragma: no cover - already gone
                self._clipboard.disconnect(self._handler_id)  # type: ignore[attr-defined]
        self._clipboard = None
        self._handler_id = None

    def suppress(self, count: int = 1) -> None:
        """Ignore the next ``count`` change notifications (our own writes)."""
        self._suppress += max(0, count)
        self._suppress_until = time.monotonic() + SUPPRESS_TIMEOUT_SECONDS

    # ── signal handling ────────────────────────────────────────────────────
    def _on_changed(self, _clipboard: object) -> None:
        self._schedule_read(DEBOUNCE_MS)

    def _schedule_read(self, delay_ms: int) -> None:
        self._cancel_pending()
        self._timeout_id = GLib.timeout_add(delay_ms, self._read_now)

    def _cancel_pending(self) -> None:
        if self._timeout_id:
            with contextlib.suppress(TypeError, ValueError):  # pragma: no cover
                GLib.source_remove(self._timeout_id)
            self._timeout_id = 0

    def _read_now(self) -> bool:
        self._timeout_id = 0
        clipboard = self._clipboard
        if clipboard is None:
            return False
        if self._suppress > 0 and time.monotonic() > self._suppress_until:
            self._suppress = 0  # the expected change event never arrived
        if self._suppress > 0:
            self._suppress -= 1
            log.debug("ignoring our own clipboard write (%d left)", self._suppress)
            return False
        try:
            formats = clipboard.get_formats()  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - display going away
            log.debug("cannot query clipboard formats", exc_info=True)
            return False
        if is_password_hint(formats):
            log.info("ignoring clipboard marked as a password by the source application")
            return False
        self._token += 1
        token = self._token
        try:
            if formats.contain_gtype(Gdk.Texture):
                clipboard.read_texture_async(
                    None, lambda clip, res, tok=token: self._finish_texture(tok, clip, res)
                )
            elif formats.contain_gtype(Gdk.FileList):
                clipboard.read_value_async(
                    Gdk.FileList,
                    GLib.PRIORITY_DEFAULT,
                    None,
                    lambda clip, res, tok=token: self._finish_files(tok, clip, res),
                )
            else:
                clipboard.read_text_async(
                    None, lambda clip, res, tok=token: self._finish_text(tok, clip, res)
                )
        except Exception:  # pragma: no cover - defensive
            log.debug("cannot start clipboard read", exc_info=True)
        return False

    def _stale(self, token: int) -> bool:
        return token != self._token

    def _finish_text(self, token: int, clipboard: object, result: object) -> None:
        if self._stale(token):
            return
        try:
            text = clipboard.read_text_finish(result)  # type: ignore[attr-defined]
        except Exception as exc:
            log.debug("clipboard text read failed: %s", exc)
            return
        self._capture_text(text)

    def _finish_texture(self, token: int, clipboard: object, result: object) -> None:
        if self._stale(token):
            return
        try:
            texture = clipboard.read_texture_finish(result)  # type: ignore[attr-defined]
        except Exception as exc:
            log.debug("clipboard image read failed: %s", exc)
            return
        if texture is None:
            return
        png = texture_png_bytes(texture)
        if png is None:  # pragma: no cover - GTK < 4.6 without PNG encoding
            log.warning("this GTK version cannot encode clipboard images")
            return
        try:
            item = self.store.add_image(png, texture.get_width(), texture.get_height())
        except Exception:  # pragma: no cover - encoding failure
            log.warning("could not store clipboard image", exc_info=True)
            return
        self._after_capture(item, "image")

    def _finish_files(self, token: int, clipboard: object, result: object) -> None:
        if self._stale(token):
            return
        try:
            value = clipboard.read_value_finish(result)  # type: ignore[attr-defined]
        except Exception as exc:
            log.debug("clipboard file read failed: %s", exc)
            return
        file_list = value
        if not hasattr(file_list, "get_files") and hasattr(value, "get_value"):
            file_list = value.get_value()
        uris: list[str] = []
        try:
            for gfile in file_list.get_files():
                uri = gfile.get_uri() if hasattr(gfile, "get_uri") else str(gfile)
                if uri:
                    uris.append(uri)
        except (AttributeError, TypeError):  # pragma: no cover - unexpected type
            uris = []
        self._capture_files(uris)

    # ── capture helpers (also used by the "capture current" CLI path) ──────
    def _capture_text(self, text: str | None) -> None:
        if not text or not text.strip():
            return
        item = self.store.add_text(text)
        self._after_capture(item, "text")

    def _capture_files(self, uris: list[str]) -> None:
        if not uris:
            return
        item = self.store.add_files(uris)
        self._after_capture(item, "files")

    def _after_capture(self, item: ClipboardItem | None, kind: str) -> None:
        if item is None:
            log.debug("clipboard %s payload was filtered out", kind)
            return
        self._captured_count += 1
        log.info("captured %s item #%s (%d bytes)", kind, item.id, item.size_bytes)
        if self._on_capture is not None:
            try:
                self._on_capture(item)
            except Exception:  # pragma: no cover - listener errors are not fatal
                log.exception("capture callback failed")


def texture_png_bytes(texture: object) -> bytes | None:
    """PNG bytes of a ``Gdk.Texture`` (``save_to_png_bytes`` needs GTK 4.6)."""
    save = getattr(texture, "save_to_png_bytes", None)
    if save is None:  # pragma: no cover - GTK 4.0 .. 4.4
        return None
    try:
        return bytes(save().get_data())
    except Exception:  # pragma: no cover - defensive
        log.debug("cannot encode the clipboard image", exc_info=True)
        return None


def display_clipboard() -> object | None:
    """The clipboard of the default display, or ``None`` when headless."""
    if not HAS_GTK:
        return None
    display = Gdk.Display.get_default()
    if display is None:
        return None
    return display.get_clipboard()
