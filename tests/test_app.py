"""The application object and the clipboard monitor, driven by the GTK double."""

from __future__ import annotations

import importlib
import time

import pytest
from ubuntu_clipboard.config import Config
from ubuntu_clipboard.i18n import set_language
from ubuntu_clipboard.models import ContentType

from .gtk_double import FakeClipboard, FakeTexture, gtk_double


class Environment:
    """Imported modules plus a configured clipboard."""

    def __init__(self, app_module, monitor_module, application, clipboard, store) -> None:
        self.app_module = app_module
        self.monitor_module = monitor_module
        self.app = application
        self.clipboard = clipboard
        self.store = store


@pytest.fixture
def env(store):
    set_language("en")
    with gtk_double() as modules:
        app_module = importlib.import_module("ubuntu_clipboard.app")
        monitor_module = importlib.import_module("ubuntu_clipboard.monitor")
        clipboard = FakeClipboard()
        modules.gdk.Display.clipboard = clipboard
        application = app_module.ClipboardApplication(config=Config(), store=store)
        yield Environment(app_module, monitor_module, application, clipboard, store)
        modules.gdk.Display.clipboard = None


# ── pure helpers ───────────────────────────────────────────────────────────
def test_parse_command_maps_arguments():
    from ubuntu_clipboard.app import parse_command

    assert parse_command(["--toggle"]) == "toggle"
    assert parse_command(["--settings"]) == "settings"
    assert parse_command(["--background", "--debug"]) == "background"
    assert parse_command(["--unknown", "-x"]) == "none"
    assert parse_command([]) == "none"


def test_gtk_available_reports_the_double(env):
    assert env.app_module.gtk_available() is True


# ── clipboard monitor ──────────────────────────────────────────────────────
def attach(env, **payload) -> object:
    env.clipboard.__init__(**payload)  # type: ignore[misc]
    monitor = env.monitor_module.ClipboardMonitor(env.store)
    monitor.attach(env.clipboard)
    return monitor


def test_monitor_captures_text(env):
    monitor = attach(env, text="copied text")
    assert env.store.count() == 1
    assert env.store.list()[0].preview == "copied text"
    assert monitor.captured_count == 1


def test_monitor_ignores_blank_payloads(env):
    attach(env, text="   \n ")
    assert env.store.count() == 0


def test_monitor_ignores_password_manager_marker(env):
    attach(env, text="hunter2", mimes={env.monitor_module.PASSWORD_HINT_MIME})
    assert env.store.count() == 0


def test_monitor_skips_our_own_writes(env):
    monitor = attach(env, text="first")
    assert env.store.count() == 1
    monitor.suppress()
    env.clipboard.emit("changed")
    assert env.store.count() == 1


def test_suppression_expires(env, monkeypatch):
    monitor = attach(env, text="first")
    monitor.suppress()
    real_monotonic = time.monotonic
    monkeypatch.setattr(env.monitor_module.time, "monotonic", lambda: real_monotonic() + 10)
    env.clipboard._text = "second"  # different payload, so dedup is not involved
    env.clipboard.emit("changed")
    assert env.store.count() == 2  # the stale suppression was dropped


def test_monitor_captures_images(env):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 200
    attach(env, texture=FakeTexture(png, 20, 10))
    item = env.store.list()[0]
    assert item.type is ContentType.IMAGE
    assert env.store.get_image_bytes(item.id) == png
    assert item.metadata["width"] == 20


def test_monitor_captures_files(env):
    attach(env, files=["file:///home/user/a.txt"])
    item = env.store.list()[0]
    assert item.type is ContentType.FILE
    assert "a.txt" in item.preview


def test_monitor_detach_stops_listening(env):
    monitor = attach(env, text="one")
    monitor.detach()
    assert monitor.attached is False
    env.clipboard._handlers.clear()
    env.clipboard.emit("changed")
    assert env.store.count() == 1


def test_monitor_capture_callback_errors_are_isolated(env):
    def explode(_item):
        raise RuntimeError("listener")

    env.clipboard.__init__(text="x")  # type: ignore[misc]
    monitor = env.monitor_module.ClipboardMonitor(env.store, on_capture=explode)
    monitor.attach(env.clipboard)
    assert env.store.count() == 1


def test_display_clipboard_returns_the_display_clipboard(env):
    assert env.monitor_module.display_clipboard() is env.clipboard


def test_display_clipboard_without_a_display(env):
    env.modules = None
    from gi.repository import Gdk

    Gdk.Display.clipboard = None
    assert env.monitor_module.display_clipboard() is None


def test_texture_png_bytes_handles_old_gtk(env):
    assert env.monitor_module.texture_png_bytes(object()) is None
    assert env.monitor_module.texture_png_bytes(FakeTexture(b"png", 1, 1)) == b"png"


# ── application ────────────────────────────────────────────────────────────
def test_application_starts_hidden_in_background(env):
    application = env.app_module.ClipboardApplication(config=Config(), store=env.store, start_hidden=True)
    application.do_activate()
    assert application.window is None


def test_activate_shows_the_window(env):
    env.app.do_activate()
    assert env.app.window is not None
    assert env.app.window.get_visible() is True


def test_toggle_hides_and_shows(env):
    env.app.dispatch("toggle")
    assert env.app.window.get_visible() is True
    env.app.dispatch("toggle")
    assert env.app.window.get_visible() is False


def test_dispatch_commands(env):
    assert env.app.dispatch("show") == 0
    assert env.app.window.get_visible() is True
    assert env.app.dispatch("hide") == 0
    assert env.app.window.get_visible() is False
    assert env.app.dispatch("background") == 0
    assert env.app.dispatch("none") == 0


def test_show_settings_is_reused(env):
    first = env.app.show_settings()
    assert env.app.show_settings() is first
    env.app.on_settings_closed()
    assert env.app.settings_window is None


def with_clipboard(env) -> None:
    env.app._clipboard = env.clipboard
    env.app.monitor = env.monitor_module.ClipboardMonitor(env.store)


def test_set_clipboard_for_text(env):
    item = env.store.add_text("payload")
    with_clipboard(env)
    assert env.app.set_clipboard(env.store.get(item.id)) is True
    assert env.clipboard.content is not None


def test_set_clipboard_for_images(env):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    item = env.store.add_image(png, 5, 5)
    with_clipboard(env)
    assert env.app.set_clipboard(env.store.get(item.id)) is True
    assert env.clipboard.content is not None


def test_set_clipboard_for_files(env):
    item = env.store.add_files(["file:///home/user/report.pdf"])
    with_clipboard(env)
    assert env.app.set_clipboard(env.store.get(item.id)) is True
    assert env.clipboard.content is not None


def test_set_clipboard_without_a_display(env):
    item = env.store.add_text("payload")
    env.app._clipboard = None
    assert env.app.set_clipboard(env.store.get(item.id)) is False


def notifications(env, monkeypatch) -> list:
    """Capture desktop notifications instead of sending them."""
    collected: list = []
    monkeypatch.setattr(env.app, "send_notification", lambda *args: collected.append(args))
    return collected


def test_paste_item_touches_and_hides(env, monkeypatch):
    item = env.store.add_text("paste me")
    env.store.add_text("other")
    env.app.dispatch("show")
    with_clipboard(env)
    scheduled: list = []
    monkeypatch.setattr(
        env.app_module.GLib, "timeout_add", lambda delay, callback, *args: scheduled.append(callback)
    )
    env.app.paste_item(env.store.get(item.id))
    assert env.store.list()[0].id == item.id
    assert env.app.window.get_visible() is False
    assert scheduled


def test_paste_item_without_a_clipboard_notifies(env, monkeypatch):
    collected = notifications(env, monkeypatch)
    item = env.store.add_text("no clipboard")
    env.app._clipboard = None
    env.app.paste_item(env.store.get(item.id))
    assert collected


def test_paste_worker_reports_failures(env, monkeypatch):
    collected = notifications(env, monkeypatch)
    monkeypatch.setattr(env.app_module, "send_paste", lambda *a, **k: (False, None))
    env.app._paste_worker(env.app._paste_token)
    assert collected


def test_paste_worker_is_silent_when_the_helper_works(env, monkeypatch):
    collected = notifications(env, monkeypatch)
    monkeypatch.setattr(env.app_module, "send_paste", lambda *a, **k: (True, "xdotool"))
    env.app._paste_worker(env.app._paste_token)
    assert collected == []


def test_notify_tolerates_a_missing_notification_daemon(env):
    env.app.notify("hello")  # must not raise


def test_clear_history(env):
    env.store.add_text("a")
    assert env.app.clear_history() == 1
    assert env.store.count() == 0


def test_history_listener_refreshes_the_window(env):
    env.app.do_activate()
    env.store.add_text("listener")
    assert env.app.window is not None


def test_save_config_applies_and_guards(env):
    env.app.do_activate()
    env.app.config.max_items = 42
    env.app.save_config()
    assert env.app._save_guard_until > time.monotonic() - 1
    assert env.app.config.max_items == 42


def test_config_change_on_disk_is_ignored_while_saving(env):
    env.app._save_guard_until = time.monotonic() + 60
    env.app._on_config_file_changed(None, None, None, None)
    assert env.app._reload_pending is False


def test_config_change_for_another_file_is_ignored(env):
    from gi.repository import Gio

    env.app._save_guard_until = 0.0
    other = Gio.File.new_for_path("/tmp/not-the-config.json")
    env.app._on_config_file_changed(None, other, None, 0)
    assert env.app._reload_pending is False


def test_config_watch_is_created(env):
    env.app._watch_config_file()
    assert env.app._config_monitor is not None
    env.app._cancel_config_watch()
    assert env.app._config_monitor is None


def test_signal_handler_quits(env):
    assert env.app._on_signal(15) is False


def test_startup_wires_everything(env):
    env.clipboard.__init__(text="already on the clipboard")  # type: ignore[misc]
    env.app.do_startup()
    assert env.app.monitor is not None and env.app.monitor.attached is True
    assert env.app._css_provider is not None
    assert env.app._config_monitor is not None
    assert env.store.count() == 1  # the initial clipboard read captured it
    env.app.do_shutdown()
    assert env.app.monitor.attached is False


def test_icons_search_path_is_registered(env):
    env.app._setup_icons()  # must not raise without a display or icon theme


def test_bundled_icon_is_a_hicolor_tree():
    from ubuntu_clipboard.install import ICON_RELATIVE_PATH, assets_dir, icon_source

    source = icon_source()
    assert source.is_file()
    assert source == assets_dir() / ICON_RELATIVE_PATH.removeprefix("assets/")
    assert source.name == "ubuntu-clipboard.png"


def test_a_pending_paste_is_cancelled_when_the_window_opens_again(env, monkeypatch):
    """Reopening the window must not let a stale Ctrl+V land somewhere else."""
    item = env.store.add_text("paste me")
    env.app.dispatch("show")
    with_clipboard(env)
    monkeypatch.setattr("ubuntu_clipboard.paste.send_paste", lambda *a, **k: pytest.fail("must not paste"))
    scheduled: list = []
    monkeypatch.setattr(
        env.app_module.GLib, "timeout_add", lambda delay, callback, *args: scheduled.append((callback, args))
    )
    env.app.paste_item(env.store.get(item.id))

    # the user presses Win+V again while the paste is on its way
    env.app.dispatch("show")
    callback, args = scheduled[0]
    callback(*args)
    assert env.app.window.get_visible() is True


def test_a_stale_paste_worker_does_nothing(env, monkeypatch):
    env.app.dispatch("show")
    with_clipboard(env)
    stale = env.app._paste_token - 1
    monkeypatch.setattr("ubuntu_clipboard.paste.send_paste", lambda *a, **k: pytest.fail("must not paste"))
    monkeypatch.setattr(env.app, "_ensure_clipboard_content", lambda: None)
    env.app._paste_worker(stale)


def test_paste_cancellation_is_logged(env, caplog):
    with caplog.at_level("DEBUG", logger="ubuntu_clipboard.app"):
        env.app._cancel_pending_paste()
        assert env.app._deliver_paste(env.app._paste_token - 1) is False
    assert "cancelled" in caplog.text


def test_paste_worker_returns_focus_and_pastes(env, monkeypatch):
    """The happy path: focus goes back to where it was, then Ctrl+V is sent."""
    focused: list = []
    pasted: list = []
    monkeypatch.setattr(env.app_module, "activate_window", lambda wid: focused.append(wid) or True)
    monkeypatch.setattr(
        env.app_module, "send_paste", lambda *a, **k: pasted.append(True) or (True, "ydotool")
    )
    with_clipboard(env)
    env.app._expected_text = None  # nothing to verify
    env.app._previous_window = "1234567"
    env.app._paste_worker(env.app._paste_token)
    assert focused == ["1234567"]
    assert pasted == [True]
    assert env.app._previous_window is None


def test_paste_worker_skips_focus_without_a_previous_window(env, monkeypatch):
    monkeypatch.setattr(
        env.app_module, "activate_window", lambda *a: pytest.fail("there is no window to focus")
    )
    monkeypatch.setattr(env.app_module, "send_paste", lambda *a, **k: (True, "ydotool"))
    with_clipboard(env)
    env.app._expected_text = None
    env.app._previous_window = None
    env.app._paste_worker(env.app._paste_token)


def test_a_cancelled_paste_leaves_focus_alone(env, monkeypatch):
    """This is the flicker: a cancelled paste must not pull focus away again."""
    monkeypatch.setattr(env.app_module, "activate_window", lambda *a: pytest.fail("focus must not move"))
    monkeypatch.setattr("ubuntu_clipboard.paste.send_paste", lambda *a, **k: pytest.fail("no paste"))
    with_clipboard(env)
    env.app._previous_window = "1234567"
    env.app._cancel_pending_paste()
    env.app._paste_worker(env.app._paste_token - 1)
    assert env.app._previous_window == "1234567"


def test_show_window_remembers_where_focus_was(env, monkeypatch):
    monkeypatch.setattr(env.app_module, "active_window", lambda: "9999")
    env.app.show_window()
    assert env.app._previous_window == "9999"
    # a second show while it is already visible must not overwrite it
    monkeypatch.setattr(env.app_module, "active_window", lambda: "8888")
    env.app.show_window()
    assert env.app._previous_window == "9999"


def test_monitor_ignores_a_password_the_xwayland_backend_cannot_see(env, monkeypatch):
    """The marker only exists on the Wayland side; the history must stay clean."""
    monkeypatch.setattr(env.monitor_module, "password_hint_hidden_by_xwayland", lambda: True)
    attach(env, text="hunter2")
    assert env.store.count() == 0


def test_the_hidden_password_probe_runs_only_on_the_xwayland_backend(env, monkeypatch):
    asked: list[bool] = []
    monkeypatch.setattr(
        env.monitor_module, "wayland_offers_password_hint", lambda: asked.append(True) or True
    )
    monkeypatch.setattr("ubuntu_clipboard.clipboard.detect_session", lambda *a, **k: "wayland")
    monkeypatch.setattr("ubuntu_clipboard.cli.display_backend", lambda *a, **k: "x11")
    assert env.monitor_module.password_hint_hidden_by_xwayland() is True

    monkeypatch.setattr("ubuntu_clipboard.cli.display_backend", lambda *a, **k: "wayland")
    assert env.monitor_module.password_hint_hidden_by_xwayland() is False

    monkeypatch.setattr("ubuntu_clipboard.clipboard.detect_session", lambda *a, **k: "x11")
    monkeypatch.setattr("ubuntu_clipboard.cli.display_backend", lambda *a, **k: "x11")
    assert env.monitor_module.password_hint_hidden_by_xwayland() is False
    assert len(asked) == 1


def test_the_monitor_still_captures_ordinary_text(env, monkeypatch):
    """The added probe must never swallow a normal copy."""
    monkeypatch.setattr(env.monitor_module, "password_hint_hidden_by_xwayland", lambda: False)
    attach(env, text="ordinary text")
    assert env.store.count() == 1


def test_windows_are_tied_to_the_desktop_entry(env):
    """With the X11 backend GNOME matches windows by ``WM_CLASS``."""
    from ubuntu_clipboard import APP_ID

    assert env.app_module.GLib.application_name == "Ubuntu Clipboard"
    assert env.app_module.GLib.prgname == APP_ID
