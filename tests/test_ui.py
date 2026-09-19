"""UI logic driven through the GTK double (no display required)."""

from __future__ import annotations

import importlib
import time

import pytest
from ubuntu_clipboard.config import Config
from ubuntu_clipboard.i18n import set_language
from ubuntu_clipboard.models import ContentType
from ubuntu_clipboard.storage import HistoryStore

from .gtk_double import FakeMessageDialog, gtk_double


class FakeApp:
    """The slice of ``ClipboardApplication`` the UI talks to."""

    def __init__(self, config: Config, store: HistoryStore) -> None:
        self.config = config
        self.store = store
        self.dark_mode = True
        self.pasted: list[int] = []
        self.notifications: list[str] = []
        self.settings_opened = 0
        self.saved = 0
        self.settings_closed = 0

    def paste_item(self, item) -> None:
        self.pasted.append(item.id)

    def notify(self, message: str) -> None:
        self.notifications.append(message)

    def show_settings(self) -> None:
        self.settings_opened += 1

    def clear_history(self) -> int:
        removed = self.store.clear()
        self.notifications.append("cleared")
        return removed

    def save_config(self) -> None:
        self.saved += 1

    def on_settings_closed(self) -> None:
        self.settings_closed += 1


@pytest.fixture
def ui(store):
    """Import the UI modules with the GTK double installed."""
    set_language("en")
    with gtk_double() as modules:
        window_module = importlib.import_module("ubuntu_clipboard.ui.window")
        settings_module = importlib.import_module("ubuntu_clipboard.ui.settings")
        dialogs_module = importlib.import_module("ubuntu_clipboard.ui.dialogs")
        app = FakeApp(Config(), store)
        window = window_module.ClipboardWindow(app)
        yield (types_ns(modules, window_module, settings_module, dialogs_module, app, window, store))


def types_ns(*values):
    class Namespace:
        pass

    names = (
        "modules",
        "window_module",
        "settings_module",
        "dialogs_module",
        "app",
        "window",
        "store",
    )
    namespace = Namespace()
    for name, value in zip(names, values, strict=True):
        setattr(namespace, name, value)
    return namespace


def rows(window) -> list:
    return window._rows()


def press(window, keyval: int, state: int = 0) -> bool:
    controller = window._controllers[0]
    return window._on_key_pressed(controller, keyval, 0, state)


# ── window construction ────────────────────────────────────────────────────
def test_window_builds_without_a_display(ui):
    assert ui.window.get_child() is not None
    assert ui.window.has_css_class("clipboard-window")
    assert ui.window.has_css_class("dark")
    assert ui.window.clear_button is not None
    assert ui.window.search_entry is not None


def test_empty_history_shows_the_empty_state(ui):
    ui.window._rebuild()
    assert rows(ui.window) == []
    assert ui.window.count_label.get_label() == ""


def test_rows_are_built_for_every_content_type(ui):
    store = ui.store
    store.add_text("hello world")
    store.add_text("https://example.com")
    store.add_text("#ff5500")
    store.add_text("def main():\n    return 0")
    store.add_files(["file:///tmp/report.pdf"])
    store.add_image(b"\x89PNG\r\n\x1a\n" + b"0" * 128, 10, 10)

    ui.window._rebuild()
    assert len(rows(ui.window)) == store.count()
    assert ui.window.count_label.get_label() == f"{store.count()} items"


def test_pinned_items_get_a_section(ui):
    item = ui.store.add_text("keep me")
    ui.store.toggle_pin(item.id)
    ui.store.add_text("plain")
    ui.window._rebuild()
    labels = [
        child.get_label()
        for child in ui.window.list_box._children
        if hasattr(child, "get_label") and child.has_css_class("section-label")
    ]
    assert "Pinned" in labels
    assert "Recent" in labels


def test_empty_state_is_shown_for_a_query_without_matches(ui):
    ui.store.add_text("something")
    ui.window.search_entry.set_text("nothing matches this")
    assert rows(ui.window) == []


def test_search_filters_the_list(ui):
    ui.store.add_text("alpha")
    ui.store.add_text("beta")
    ui.window.search_entry.set_text("beta")
    assert len(rows(ui.window)) == 1
    assert ui.window._items[0].preview == "beta"


def test_selection_highlight_and_movement(ui):
    for index in range(3):
        ui.store.add_text(f"item {index}")
    ui.window._rebuild()
    ui.window._selected = 0
    ui.window._update_selection()
    assert rows(ui.window)[0].has_css_class("selected")
    ui.window._move_selection(1)
    assert rows(ui.window)[1].has_css_class("selected")
    assert not rows(ui.window)[0].has_css_class("selected")
    ui.window._move_selection(-5)
    assert ui.window._selected == 0
    ui.window._move_selection(99)
    assert ui.window._selected == 2


# ── keyboard ───────────────────────────────────────────────────────────────
def test_enter_pastes_the_selected_item(ui):
    ui.store.add_text("first")
    ui.window._rebuild()
    assert press(ui.window, 0xFF0D) is True  # Return
    assert ui.app.pasted == [ui.window._items[0].id]


def test_escape_hides_the_window(ui):
    ui.window.present()
    assert press(ui.window, 0xFF1B) is True  # Escape
    assert ui.window.get_visible() is False


def test_arrow_keys_move_the_selection(ui):
    ui.store.add_text("a")
    ui.store.add_text("b")
    ui.window._rebuild()
    assert press(ui.window, 0xFF54) is True  # Down
    assert ui.window._selected == 1
    assert press(ui.window, 0xFF52) is True  # Up
    assert ui.window._selected == 0


def test_ctrl_number_pastes_the_matching_item(ui):
    for index in range(3):
        ui.store.add_text(f"item {index}")
    ui.window._rebuild()
    assert press(ui.window, 0x0032, 1 << 2) is True  # Ctrl+2
    assert ui.app.pasted == [ui.window._items[1].id]


def test_unknown_keys_are_ignored(ui):
    assert press(ui.window, 0x0061) is False  # plain "a"


def test_delete_removes_the_selected_item(ui):
    item = ui.store.add_text("delete me")
    ui.window._rebuild()
    assert press(ui.window, 0xFFFF) is True  # Delete
    assert ui.store.get(item.id) is None


def test_delete_is_left_to_a_search_entry_with_text(ui):
    item = ui.store.add_text("keep me")
    ui.window.search_entry.set_text("keep")
    ui.window._rebuild()
    assert press(ui.window, 0xFFFF) is False
    assert ui.store.get(item.id) is not None


def test_ctrl_p_toggles_the_pin(ui):
    item = ui.store.add_text("pin me")
    ui.window._rebuild()
    assert press(ui.window, 0x0070, 1 << 2) is True  # Ctrl+P
    assert ui.store.get(item.id).pinned is True


def test_tab_moves_the_selection(ui):
    ui.store.add_text("a")
    ui.store.add_text("b")
    ui.window._rebuild()
    assert press(ui.window, 0xFF09) is True  # Tab
    assert ui.window._selected == 1


# ── actions & buttons ──────────────────────────────────────────────────────
def test_pin_and_delete_buttons(ui):
    item = ui.store.add_text("row action")
    ui.window._rebuild()
    card = rows(ui.window)[0]
    actions = card._children[1]
    pin_button, delete_button = actions._children
    pin_button.click()
    assert ui.store.get(item.id).pinned is True
    delete_button.click()
    assert ui.store.get(item.id) is None


def test_clicking_the_content_pastes(ui):
    item = ui.store.add_text("click me")
    ui.window._rebuild()
    card = rows(ui.window)[0]
    content = card._children[0]
    gesture = content._controllers[0]
    gesture.emit("pressed")
    assert ui.app.pasted == [item.id]


def test_clear_button_asks_for_confirmation(ui, monkeypatch):
    ui.store.add_text("remove")
    monkeypatch.setattr(ui.dialogs_module, "confirm", lambda *a, **k: True)
    ui.window.clear_button.click()
    assert ui.store.count() == 0


def test_clear_button_respects_a_refusal(ui, monkeypatch):
    ui.store.add_text("keep")
    monkeypatch.setattr(ui.dialogs_module, "confirm", lambda *a, **k: False)
    ui.window.clear_button.click()
    assert ui.store.count() == 1


def test_close_request_only_hides(ui):
    ui.window.present()
    ui.window.close()
    assert ui.window.get_visible() is False


# ── focus / theme / config ─────────────────────────────────────────────────
def test_focus_loss_hides_the_window(ui):
    ui.window.present()
    ui.window.set_active(True)
    ui.window._focus_grace_until = 0.0
    ui.window.set_active(False)
    assert ui.window.get_visible() is False


def test_focus_gain_does_not_hide(ui):
    ui.window.present()
    ui.window.set_active(True)
    assert ui.window.get_visible() is True


def test_focus_loss_can_be_disabled(ui):
    ui.app.config.close_on_focus_loss = False
    ui.window.config = ui.app.config
    ui.window.present()
    ui.window.set_active(True)
    ui.window._focus_grace_until = 0.0
    ui.window.set_active(False)
    assert ui.window.get_visible() is True


def test_focus_grace_period_is_respected(ui):
    ui.window.present()
    ui.window.set_active(True)
    ui.window._focus_grace_until = time.monotonic() + 60
    ui.window.set_active(False)
    assert ui.window.get_visible() is True


def test_mapping_refreshes_and_focuses_the_search_entry(ui):
    ui.store.add_text("mapped")
    ui.window._focus_grace_until = 0.0
    ui.window.emit("map")
    assert ui.window._focus_grace_until > 0
    assert rows(ui.window)


def test_apply_theme_switches_classes(ui):
    ui.window.apply_theme(False)
    assert ui.window.has_css_class("light")
    assert not ui.window.has_css_class("dark")
    assert ui.window.search_entry.has_css_class("light")
    ui.window.apply_theme(True)
    assert ui.window.has_css_class("dark")
    assert not ui.window.search_entry.has_css_class("light")


def test_apply_config_refreshes(ui):
    ui.store.add_text("configured")
    ui.app.config.window_width = 500
    ui.window.apply_config()
    assert ui.window.config.window_width == 500


def test_refresh_is_coalesced_through_the_main_loop(ui):
    ui.window.present()
    ui.window.refresh()
    assert len(rows(ui.window)) == ui.store.count()


def test_refresh_does_nothing_while_hidden(ui):
    ui.store.add_text("hidden")
    ui.window._selected = 0
    ui.window.refresh()
    assert rows(ui.window) == []


def test_image_preview_uses_the_thumbnail_cache(ui):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 128
    item = ui.store.add_image(png, 12, 8)
    ui.window._rebuild()
    texture = ui.window._texture_for(ui.store.get(item.id))
    assert texture is not None
    assert ui.window._texture_for(ui.store.get(item.id)) is texture
    assert len(ui.window._thumbnails) == 1


def test_thumbnail_cache_is_bounded(ui, monkeypatch):
    monkeypatch.setattr(ui.window_module, "THUMBNAIL_CACHE", 2)
    for index in range(4):
        ui.store.add_image(b"\x89PNG\r\n\x1a\n" + bytes([index]) * 128, 4, 4)
    for item in ui.store.list():
        ui.window._texture_for(item)
    assert len(ui.window._thumbnails) <= 2


def test_file_preview_falls_back_to_the_stored_uris(ui):
    item = ui.store.add_files(["file:///home/user/a.txt", "file:///home/user/b.txt"])
    ui.window._rebuild()
    assert "a.txt" in ui.window._file_preview(ui.store.get(item.id))


def test_color_swatch_is_drawn(ui):
    item = ui.store.add_text("#00ff00")
    assert item.type is ContentType.COLOR
    ui.window._rebuild()
    swatch = ui.window._build_color_preview(item)
    assert swatch is not None


# ── settings window ────────────────────────────────────────────────────────
def test_settings_window_builds_and_saves(ui):
    window = ui.settings_module.SettingsWindow(ui.app)
    window.present()
    assert window.window.get_visible() is True

    spin = window._controls["max_items"]
    spin.set_value(120)
    assert ui.app.config.max_items == 120
    assert ui.app.saved == 1


def test_settings_switch_updates_the_config(ui):
    window = ui.settings_module.SettingsWindow(ui.app)
    window._controls["exclude_sensitive"].set_active(False)
    assert ui.app.config.exclude_sensitive is False


def test_settings_combo_changes_the_language(ui):
    from ubuntu_clipboard import i18n

    window = ui.settings_module.SettingsWindow(ui.app)
    dropdown = window._controls["language"]
    assert "English" in dropdown._options
    dropdown.set_selected(dropdown._options.index("English"))
    assert ui.app.config.language == "en"
    assert i18n.get_language() == "en"


def test_settings_close_notifies_the_app(ui):
    window = ui.settings_module.SettingsWindow(ui.app)
    window.window.close()
    assert ui.app.settings_closed == 1


def test_settings_auto_start_toggle(ui, monkeypatch):
    calls = []
    monkeypatch.setattr(ui.settings_module, "enable_autostart", lambda *a, **k: calls.append("enable"))
    monkeypatch.setattr(ui.settings_module, "disable_autostart", lambda *a, **k: calls.append("disable"))
    window = ui.settings_module.SettingsWindow(ui.app)
    switch = window._controls["auto_start"]
    switch.set_active(False)
    switch.set_active(True)
    assert calls == ["disable", "enable"]


def test_settings_shortcut_install_reports_success(ui, monkeypatch):
    class Report:
        ok = True

    monkeypatch.setattr(ui.settings_module, "install_shortcut", lambda **kwargs: Report())
    window = ui.settings_module.SettingsWindow(ui.app)
    window._on_install_shortcut(None)
    assert ui.app.notifications


def test_settings_shortcut_install_reports_failure(ui, monkeypatch):
    from ubuntu_clipboard.shortcut import ShortcutError

    def explode(**kwargs):
        raise ShortcutError("gsettings not found")

    monkeypatch.setattr(ui.settings_module, "install_shortcut", explode)
    window = ui.settings_module.SettingsWindow(ui.app)
    window._on_install_shortcut(None)
    assert window._shortcut_label.get_label() == "gsettings not found"


def test_settings_reset_restores_defaults(ui, monkeypatch):
    monkeypatch.setattr(ui.dialogs_module, "confirm", lambda *a, **k: True)
    ui.app.config.max_items = 500
    window = ui.settings_module.SettingsWindow(ui.app)
    window._on_reset(None)
    assert ui.app.config.max_items == 80
    assert ui.app.saved == 1


# ── dialogs ────────────────────────────────────────────────────────────────
def test_confirm_accepts(ui):
    FakeMessageDialog.auto_response = "accept"
    assert ui.dialogs_module.confirm(None, "title", "body") is True


def test_confirm_cancels(ui):
    FakeMessageDialog.auto_response = "cancel"
    assert ui.dialogs_module.confirm(None, "title", "body") is False


def test_confirm_without_libadwaita(ui, monkeypatch):
    monkeypatch.setattr(ui.dialogs_module, "HAS_ADW", False)
    monkeypatch.setattr(ui.dialogs_module, "Adw", None)
    FakeMessageDialog.auto_response = "accept"
    assert ui.dialogs_module.confirm(None, "title", "body") is True


def test_dock_hints_are_optional_and_safe_without_x11(ui):
    """The double has no GdkX11: the hint must fail quietly, never raise."""
    window = ui.window
    assert window.apply_dock_hints() is False
    ui.app.config.hide_from_dock = False
    assert window.apply_dock_hints() is False


def test_map_applies_the_dock_hint(ui, monkeypatch):
    called: list[bool] = []
    monkeypatch.setattr(ui.window, "apply_dock_hints", lambda: called.append(True) or False)
    ui.window._on_mapped()
    assert called == [True]
