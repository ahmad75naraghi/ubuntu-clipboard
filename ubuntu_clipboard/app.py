"""The application object: one resident process that owns the clipboard history.

Architecture
------------
* ``Gio.ApplicationFlags.HANDLES_COMMAND_LINE`` gives us a *single* instance per
  session and an IPC channel for free (GApplication's D-Bus name). ``Win+V``
  runs ``ubuntu-clipboard --toggle``, which either becomes the primary instance
  (first start) or hands the command to the running one and exits immediately —
  no lock files, no ``SIGKILL``, no debounce hack, no flicker.
* Because the process stays alive it keeps ownership of the selection it writes,
  so a pasted item survives (with the v1 code the window exited 350 ms after
  writing to the clipboard and the content disappeared with it).
* Clipboard monitoring is signal based (see :mod:`ubuntu_clipboard.monitor`).
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from . import APP_ICON, APP_ID, APP_NAME, __version__
from .config import Config, config_path, get_config
from .i18n import is_rtl, set_language, t
from .models import ClipboardItem, ContentType
from .paste import activate_window, active_window, send_paste
from .storage import HistoryStore

log = logging.getLogger(__name__)

try:  # pragma: no cover - import guard
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, Gio, GLib, Gtk

    HAS_GTK = True
    try:
        gi.require_version("Adw", "1")
        from gi.repository import Adw

        HAS_ADW = True
    except (ImportError, ValueError):
        Adw = None  # type: ignore[assignment]
        HAS_ADW = False
except (ImportError, ValueError):  # pragma: no cover - no GTK installed
    Gdk = Gio = GLib = Gtk = None  # type: ignore[assignment]
    Adw = None  # type: ignore[assignment]
    HAS_GTK = HAS_ADW = False

COMMANDS = ("toggle", "show", "hide", "settings", "quit", "background", "none")
#: Delay between hiding the window and synthesising Ctrl+V.
PASTE_DELAY_MS = 180
#: How long our own configuration writes are ignored by the file monitor.
CONFIG_GUARD_SECONDS = 1.5


def parse_command(arguments: Iterable[str]) -> str:
    """Map application arguments to one of :data:`COMMANDS`."""
    for argument in arguments:
        if argument.startswith("--"):
            name = argument[2:].split("=", 1)[0]
            if name in COMMANDS:
                return name
    return "none"


if HAS_GTK:
    _ApplicationBase = Adw.Application if HAS_ADW else Gtk.Application

    class ClipboardApplication(_ApplicationBase):  # type: ignore[misc,valid-type]
        """Owns the history store, the clipboard monitor and the popup window."""

        def __init__(
            self,
            *,
            config: Config | None = None,
            store: HistoryStore | None = None,
            command: str = "none",
            debug: bool = False,
            start_hidden: bool = False,
        ) -> None:
            super().__init__(
                application_id=APP_ID,
                flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
            )
            self.config = config or get_config()
            self.store = store or HistoryStore(config=self.config)
            self.command = command if command in COMMANDS else "none"
            self.debug = debug
            self.start_hidden = start_hidden
            self.window = None
            self.monitor = None
            self.settings_window = None
            self._clipboard = None
            self._css_provider = None
            self._config_monitor = None
            self._previous_window = None
            self._save_guard_until = 0.0
            self._started = False
            self._reload_pending = False
            self.set_application_name(APP_NAME)

        # ── startup ────────────────────────────────────────────────────────
        def do_startup(self) -> None:  # noqa: N802 - GObject vfunc
            _ApplicationBase.do_startup(self)
            log.info("%s %s starting (pid %d)", APP_NAME, __version__, os.getpid())
            set_language(self.config.language)
            direction = Gtk.TextDirection.RTL if is_rtl() else Gtk.TextDirection.LTR
            Gtk.Widget.set_default_direction(direction)
            self._load_css()
            self._setup_icons()
            self._install_actions()
            self._apply_theme()
            self._watch_config_file()
            self._setup_signals()
            self.store.add_listener(self._on_history_changed)
            self._start_monitor()
            # Keep running without any window (background/autostart mode).
            self.hold()
            self._started = True

        def do_activate(self) -> None:  # noqa: N802 - GObject vfunc
            if self.start_hidden:
                return
            self.show_window()

        def do_command_line(self, command_line) -> int:  # noqa: N802 - GObject vfunc
            arguments = command_line.get_arguments()[1:]
            command = parse_command(arguments) if arguments else self.command
            try:
                return self.dispatch(command or "none")
            except Exception:  # pragma: no cover - never crash the IPC path
                log.exception("command %r failed", command)
                return 1

        def do_shutdown(self) -> None:  # noqa: N802 - GObject vfunc
            log.info("shutting down")
            self._cancel_config_watch()
            if self.monitor is not None:
                self.monitor.detach()
            self.store.remove_listener(self._on_history_changed)
            self.store.close()
            _ApplicationBase.do_shutdown(self)

        # ── actions ────────────────────────────────────────────────────────
        def _install_actions(self) -> None:
            actions = {
                "toggle": lambda *_: self.toggle_window(),
                "show": lambda *_: self.show_window(),
                "hide": lambda *_: self.hide_window(),
                "settings": lambda *_: self.show_settings(),
                "clear-history": lambda *_: self.clear_history(),
                "quit": lambda *_: self.quit(),
            }
            for name, callback in actions.items():
                action = Gio.SimpleAction.new(name, None)
                action.connect("activate", callback)
                self.add_action(action)
            self.set_accels_for_action("app.settings", ["<Primary>comma"])
            self.set_accels_for_action("app.quit", ["<Primary>q"])

        def dispatch(self, command: str) -> int:
            log.debug("dispatching command %r", command)
            if command == "toggle":
                self.toggle_window()
            elif command == "show":
                self.show_window()
            elif command == "hide":
                self.hide_window()
            elif command == "settings":
                self.show_settings()
            elif command == "quit":
                self.quit()
            elif command == "background":
                log.info("running in background (clipboard history active)")
            return 0

        # ── window ─────────────────────────────────────────────────────────
        def ensure_window(self):
            if self.window is None:
                from .ui.window import ClipboardWindow

                self.window = ClipboardWindow(self)
            return self.window

        def show_window(self):
            window = self.ensure_window()
            if not window.get_visible():
                # Remember where focus was so X11 users get it back after pasting.
                self._previous_window = active_window()
            window.refresh(force=True)
            window.present()
            return window

        def hide_window(self) -> None:
            if self.window is not None:
                self.window.hide_window()

        def toggle_window(self) -> None:
            window = self.ensure_window()
            if window.get_visible():
                window.hide_window()
            else:
                self.show_window()

        def show_settings(self):
            from .ui.settings import SettingsWindow

            if self.settings_window is not None:
                self.settings_window.present()
                return self.settings_window
            self.settings_window = SettingsWindow(self)
            self.settings_window.present()
            return self.settings_window

        def on_settings_closed(self) -> None:
            self.settings_window = None

        # ── clipboard ──────────────────────────────────────────────────────
        def _start_monitor(self) -> None:
            from .monitor import ClipboardMonitor, display_clipboard

            clipboard = display_clipboard()
            if clipboard is None:
                log.error("no display available — clipboard history is not active")
                return
            self._clipboard = clipboard
            self.monitor = ClipboardMonitor(self.store, config_provider=lambda: self.config)
            self.monitor.attach(clipboard)

        def set_clipboard(self, item: ClipboardItem) -> bool:
            """Put ``item`` on the system clipboard (keeps ownership of it)."""
            if self._clipboard is None or self.monitor is None:
                return False
            self.monitor.suppress()
            try:
                if item.type is ContentType.IMAGE:
                    data = self.store.get_image_bytes(item.id)
                    if not data:
                        return False
                    texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
                    provider = Gdk.ContentProvider.new_for_value(texture)
                elif item.type is ContentType.FILE:
                    uris = item.uris() or [
                        line for line in (self.store.get_text(item.id) or "").splitlines() if line
                    ]
                    text = "\n".join(uris)
                    files = [Gio.File.new_for_uri(uri) for uri in uris]
                    try:
                        provider = Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_list(files))
                    except Exception:  # pragma: no cover - older GTK/backend
                        provider = Gdk.ContentProvider.new_for_bytes(
                            "text/uri-list", GLib.Bytes.new(text.encode("utf-8"))
                        )
                else:
                    text = self.store.get_text(item.id)
                    if text is None:
                        return False
                    try:
                        provider = Gdk.ContentProvider.new_for_value(text)
                    except Exception:  # pragma: no cover - defensive
                        provider = Gdk.ContentProvider.new_for_bytes(
                            "text/plain;charset=utf-8", GLib.Bytes.new(text.encode("utf-8"))
                        )
                self._clipboard.set_content(provider)
            except Exception:
                log.exception("cannot write item #%s to the clipboard", item.id)
                return False
            return True

        def paste_item(self, item: ClipboardItem) -> None:
            """Copy ``item`` and deliver ``Ctrl+V`` to the previously focused app."""
            self.hide_window()
            if not self.set_clipboard(item):
                self.notify(t("notify.copied"))
                return
            self.store.touch(item.id)
            GLib.timeout_add(PASTE_DELAY_MS, self._deliver_paste)

        def _deliver_paste(self) -> bool:
            threading.Thread(target=self._paste_worker, name="paste", daemon=True).start()
            return False

        def _paste_worker(self) -> None:
            if self._previous_window:
                activate_window(self._previous_window)
                self._previous_window = None
            succeeded, _tool = send_paste()
            if not succeeded:
                self.notify(t("notify.copied"))

        def clear_history(self) -> int:
            removed = self.store.clear()
            self.notify(t("notify.history_cleared"))
            return removed

        def notify(self, message: str) -> None:
            """Best effort desktop notification (ignored when unavailable)."""
            try:
                notification = Gio.Notification.new(APP_NAME)
                notification.set_body(message)
                notification.set_icon(Gio.ThemedIcon.new(APP_ICON))
                self.send_notification(None, notification)
            except Exception:  # pragma: no cover - notification daemon missing
                log.debug("cannot send notification", exc_info=True)

        # ── theming & css ──────────────────────────────────────────────────
        def _load_css(self) -> None:
            from importlib.resources import files

            css_path = files("ubuntu_clipboard.ui").joinpath("styles.css")
            stylesheet = css_path.read_text(encoding="utf-8")
            provider = Gtk.CssProvider()
            try:
                if hasattr(provider, "load_from_string"):  # GTK >= 4.12
                    provider.load_from_string(stylesheet)
                else:  # pragma: no cover - GTK 4.0 .. 4.10
                    provider.load_from_data(stylesheet.encode("utf-8"))
            except GLib.Error as exc:
                log.error("stylesheet is invalid: %s", exc)
                return
            display = Gdk.Display.get_default()
            if display is None:
                return
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            self._css_provider = provider

        def _setup_icons(self) -> None:
            """Let GTK resolve ``ubuntu-clipboard`` from the bundled hicolor tree."""
            from .install import assets_dir

            display = Gdk.Display.get_default()
            if display is None:  # pragma: no cover - headless
                return
            with contextlib.suppress(AttributeError, TypeError, GLib.Error):
                Gtk.IconTheme.get_for_display(display).add_search_path(str(assets_dir()))

        def _apply_theme(self) -> None:
            theme = self.config.theme
            if HAS_ADW:
                schemes = {
                    "dark": Adw.ColorScheme.FORCE_DARK,
                    "light": Adw.ColorScheme.FORCE_LIGHT,
                }
                Adw.StyleManager.get_default().set_color_scheme(schemes.get(theme, Adw.ColorScheme.DEFAULT))
            else:
                settings = Gtk.Settings.get_default()
                if settings is not None and theme in {"dark", "light"}:
                    settings.set_property("gtk-application-prefer-dark-theme", theme == "dark")
            if self.window is not None:
                self.window.apply_theme(self.dark_mode)

        @property
        def dark_mode(self) -> bool:
            """Whether the effective theme is dark."""
            if HAS_ADW:
                return bool(Adw.StyleManager.get_default().get_dark())
            settings = Gtk.Settings.get_default()
            if settings is None:  # pragma: no cover - headless
                return True
            if self.config.theme == "dark":
                return True
            if self.config.theme == "light":
                return False
            theme_name = settings.get_property("gtk-theme-name") or ""
            return "dark" in theme_name.lower()

        # ── configuration ──────────────────────────────────────────────────
        def save_config(self) -> None:
            """Persist the current configuration, ignoring the resulting event."""
            self._save_guard_until = time.monotonic() + CONFIG_GUARD_SECONDS
            try:
                self.config.save()
            except OSError:
                log.exception("cannot save the configuration")
            self.apply_config(reload_store=False)

        def apply_config(self, *, reload_store: bool = True) -> None:
            set_language(self.config.language)
            Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL if is_rtl() else Gtk.TextDirection.LTR)
            self._apply_theme()
            if reload_store:
                self.store.set_config(self.config)
            if self.window is not None:
                self.window.apply_config()

        def reload_config(self) -> None:
            log.info("configuration changed on disk — reloading")
            self.config = get_config(reload=True)
            self.store.set_config(self.config)
            self.apply_config(reload_store=False)

        def _watch_config_file(self) -> None:
            """Watch the *directory*: the config is replaced atomically."""
            try:
                gfile = Gio.File.new_for_path(str(config_path().parent))
                self._config_monitor = gfile.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
                self._config_monitor.connect("changed", self._on_config_file_changed)
            except (GLib.Error, TypeError):  # pragma: no cover - unsupported backend
                log.debug("cannot watch the configuration directory", exc_info=True)

        def _cancel_config_watch(self) -> None:
            if self._config_monitor is not None:
                self._config_monitor.cancel()
                self._config_monitor = None

        def _on_config_file_changed(self, _monitor, gfile, _other, _event, *_args) -> None:
            path = gfile.get_path() if gfile is not None else None
            if path is None or Path(path) != config_path():
                return
            if time.monotonic() < self._save_guard_until or self._reload_pending:
                return
            self._reload_pending = True

            def reload_later() -> bool:
                self._reload_pending = False
                self.reload_config()
                return False

            GLib.timeout_add(300, reload_later)

        def _on_history_changed(self) -> None:
            # Listeners run on whichever thread mutated the store.
            def refresh_later() -> bool:
                if self.window is not None:
                    self.window.refresh()
                return False

            GLib.idle_add(refresh_later)

        # ── signals ────────────────────────────────────────────────────────
        def _setup_signals(self) -> None:
            for signum in (signal.SIGINT, signal.SIGTERM):
                try:
                    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, self._on_signal, signum)
                except Exception:  # pragma: no cover - not a unix main context
                    log.debug("cannot install handler for signal %s", signum)

        def _on_signal(self, signum: int) -> bool:
            log.info("received signal %d — quitting", signum)
            self.quit()
            return False

else:  # pragma: no cover - no GTK available
    ClipboardApplication = None  # type: ignore[assignment,misc]


def gtk_available() -> bool:
    return HAS_GTK
