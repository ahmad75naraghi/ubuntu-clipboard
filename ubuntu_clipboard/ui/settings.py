"""Preferences window.

Uses libadwaita's ``PreferencesWindow`` when available and falls back to a plain
GTK window built from the same helper functions. Every change is written
immediately (debounced), so there is no "restart the application" message.
"""

from __future__ import annotations

import logging

from .. import APP_NAME, PROJECT_URL, __version__
from ..config import LANGUAGES, config_path
from ..i18n import set_language, t
from ..install import disable_autostart, enable_autostart
from ..shortcut import ShortcutError, resolve_launch_command
from ..shortcut import install as install_shortcut
from . import HAS_ADW, Adw, Gio, GLib, Gtk

log = logging.getLogger(__name__)

SAVE_DELAY_MS = 400

#: Languages are listed with their own name so the list stays readable when the
#: interface happens to be in a language the user cannot read.
LANGUAGE_LABELS = {"fa": "فارسی", "en": "English"}


class _Section:
    """Uniform wrapper around ``Adw.PreferencesGroup`` and a plain ``Gtk.Box``."""

    def __init__(self, title: str):
        if HAS_ADW:
            self.group = Adw.PreferencesGroup(title=title)
        else:  # pragma: no cover - only on GTK without libadwaita
            self.group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            self.group.add_css_class("section-label")
            heading = Gtk.Label(label=title, xalign=0)
            heading.add_css_class("section-label")
            self.group.append(heading)

    @property
    def widget(self):
        return self.group

    def add(self, row) -> None:
        if HAS_ADW:
            self.group.add(row)
        else:  # pragma: no cover
            self.group.append(row)


class SettingsWindow:
    """Non modal preferences window.

    ``app`` is the :class:`~ubuntu_clipboard.app.ClipboardApplication`; the type
    is intentionally implicit to avoid an import cycle with the UI package.
    """

    def __init__(self, app) -> None:
        self.app = app
        self._save_source = 0
        self._shortcut_label = None
        self._controls: dict[str, object] = {}
        self.window = self._build_window()
        self._build()

    # ── window scaffolding ─────────────────────────────────────────────────
    def _build_window(self):
        if HAS_ADW:
            window = Adw.PreferencesWindow(application=self.app)
            window.set_title(t("settings.title"))
            window.set_default_size(560, 640)
            window.set_search_enabled(False)
            self.page = Adw.PreferencesPage()
            window.add(self.page)
        else:  # pragma: no cover - fallback path
            window = Gtk.Window(application=self.app)
            window.set_title(t("settings.title"))
            window.set_default_size(560, 640)
            self.page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
            self.page.set_margin_top(18)
            self.page.set_margin_bottom(18)
            self.page.set_margin_start(18)
            self.page.set_margin_end(18)
            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scrolled.set_child(self.page)
            window.set_child(scrolled)
        window.connect("close-request", self._on_close)
        return window

    def _add_section(self, section: _Section) -> None:
        if HAS_ADW:
            self.page.add(section.widget)
        else:  # pragma: no cover
            self.page.append(section.widget)

    def present(self) -> None:
        self.window.present()

    # ── building blocks ────────────────────────────────────────────────────
    def _row(self, title: str, subtitle: str, control):
        if HAS_ADW:
            row = Adw.ActionRow(title=title)
            if subtitle:
                row.set_subtitle(subtitle)
            row.add_suffix(control)
            row.set_activatable_widget(control)
            return row
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)  # pragma: no cover
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        labels.append(Gtk.Label(label=title, xalign=0))
        if subtitle:
            note = Gtk.Label(label=subtitle, xalign=0)
            note.add_css_class("subtitle-label")
            labels.append(note)
        box.append(labels)
        box.append(control)
        return box

    def _spin(self, value: int, lower: int, upper: int, step: int, key: str) -> Gtk.SpinButton:
        adjustment = Gtk.Adjustment(value=value, lower=lower, upper=upper, step_increment=step)
        spin = Gtk.SpinButton(adjustment=adjustment, numeric=True)
        spin.set_valign(Gtk.Align.CENTER)
        spin.connect("value-changed", self._on_spin_changed, key)
        self._controls[key] = spin
        return spin

    def _switch(self, active: bool, key: str) -> Gtk.Switch:
        switch = Gtk.Switch(active=active)
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect("notify::active", self._on_switch_changed, key)
        self._controls[key] = switch
        return switch

    def _combo(self, options: list[tuple[str, str]], selected: str, key: str) -> Gtk.DropDown:
        dropdown = Gtk.DropDown.new_from_strings([label for _value, label in options])
        values = [value for value, _label in options]
        dropdown.set_selected(values.index(selected) if selected in values else 0)
        dropdown.set_valign(Gtk.Align.CENTER)
        dropdown.connect("notify::selected", self._on_combo_changed, key, values)
        self._controls[key] = dropdown
        return dropdown

    # ── content ────────────────────────────────────────────────────────────
    def _build(self) -> None:
        config = self.app.config
        self._build_history(config)
        self._build_privacy(config)
        self._build_appearance(config)
        self._build_system(config)
        self._build_about()

    def _build_history(self, config) -> None:
        section = _Section(t("settings.group.history"))
        section.add(
            self._row(
                t("settings.max_items"),
                t("settings.max_items.subtitle"),
                self._spin(config.max_items, 10, 1000, 10, "max_items"),
            )
        )
        section.add(
            self._row(t("settings.pin_limit"), "", self._spin(config.pin_limit, 1, 200, 1, "pin_limit"))
        )
        section.add(
            self._row(
                t("settings.max_item_size"),
                "",
                self._spin(config.max_item_size_kb, 1, 10240, 64, "max_item_size_kb"),
            )
        )
        section.add(
            self._row(
                t("settings.max_image_size"),
                "",
                self._spin(config.max_image_size_kb, 64, 65536, 512, "max_image_size_kb"),
            )
        )
        section.add(
            self._row(
                t("settings.keep_pinned"),
                "",
                self._switch(config.keep_pinned_on_clear, "keep_pinned_on_clear"),
            )
        )
        self._add_section(section)

    def _build_privacy(self, config) -> None:
        section = _Section(t("settings.group.privacy"))
        section.add(
            self._row(
                t("settings.exclude_sensitive"),
                t("settings.exclude_sensitive.subtitle"),
                self._switch(config.exclude_sensitive, "exclude_sensitive"),
            )
        )
        self._add_section(section)

    def _build_appearance(self, config) -> None:
        section = _Section(t("settings.group.appearance"))
        themes = [
            ("system", t("settings.theme.system")),
            ("dark", t("settings.theme.dark")),
            ("light", t("settings.theme.light")),
        ]
        languages = [
            (value, t("settings.language.auto") if value == "auto" else LANGUAGE_LABELS[value])
            for value in LANGUAGES
            if value in {"auto", "fa", "en"}
        ]
        section.add(self._row(t("settings.theme"), "", self._combo(themes, config.theme, "theme")))
        section.add(
            self._row(t("settings.language"), "", self._combo(languages, config.language, "language"))
        )
        section.add(self._row(t("settings.window_size"), "", self._size_controls(config)))
        section.add(
            self._row(
                t("settings.thumbnail_height"),
                "",
                self._spin(config.image_thumb_height, 40, 320, 8, "image_thumb_height"),
            )
        )
        section.add(
            self._row(
                t("settings.close_on_focus_loss"),
                "",
                self._switch(config.close_on_focus_loss, "close_on_focus_loss"),
            )
        )
        self._add_section(section)

    def _size_controls(self, config) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_valign(Gtk.Align.CENTER)
        width = self._spin(config.window_width, 320, 1600, 20, "window_width")
        height = self._spin(config.window_height, 320, 1600, 20, "window_height")
        times = Gtk.Label(label="×")
        box.append(width)
        box.append(times)
        box.append(height)
        return box

    def _build_system(self, config) -> None:
        section = _Section(t("settings.group.system"))
        section.add(self._row(t("settings.auto_start"), "", self._switch(config.auto_start, "auto_start")))
        section.add(
            self._row(
                t("settings.hide_from_dock"),
                t("settings.hide_from_dock.subtitle"),
                self._switch(config.hide_from_dock, "hide_from_dock"),
            )
        )

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_valign(Gtk.Align.CENTER)
        self._shortcut_label = Gtk.Label(label=config.shortcut)
        self._shortcut_label.add_css_class("meta-label")
        button = Gtk.Button(label=t("settings.shortcut.install"))
        button.connect("clicked", self._on_install_shortcut)
        box.append(self._shortcut_label)
        box.append(button)
        section.add(self._row(t("settings.shortcut"), t("settings.shortcut.subtitle"), box))
        self._add_section(section)

    def _build_about(self) -> None:
        section = _Section(t("settings.group.about"))
        version_row = self._row(t("settings.application"), "", Gtk.Label(label=f"{APP_NAME} {__version__}"))
        section.add(version_row)

        link = Gtk.Button(label=PROJECT_URL.replace("https://", ""))
        link.add_css_class("flat")
        link.set_valign(Gtk.Align.CENTER)
        link.connect("clicked", lambda *_: self._open_uri(PROJECT_URL))
        section.add(self._row(t("action.about"), "", link))

        config_label = Gtk.Label(label=str(config_path()))
        config_label.add_css_class("meta-label")
        config_label.set_valign(Gtk.Align.CENTER)
        section.add(self._row(t("settings.config_file"), "", config_label))

        reset = Gtk.Button(label=t("settings.reset"))
        reset.set_valign(Gtk.Align.CENTER)
        reset.connect("clicked", self._on_reset)
        section.add(self._row(t("settings.reset"), "", reset))
        self._add_section(section)

    # ── change handling ────────────────────────────────────────────────────
    def _on_spin_changed(self, spin: Gtk.SpinButton, key: str) -> None:
        setattr(self.app.config, key, int(spin.get_value()))
        self._schedule_save()

    def _on_switch_changed(self, switch: Gtk.Switch, _parameter, key: str) -> None:
        setattr(self.app.config, key, bool(switch.get_active()))
        if key == "auto_start":
            try:
                if switch.get_active():
                    enable_autostart(resolve_launch_command())
                else:
                    disable_autostart()
            except OSError:  # pragma: no cover - permissions
                log.exception("cannot update the autostart entry")
        self._schedule_save()

    def _on_combo_changed(self, dropdown: Gtk.DropDown, _parameter, key: str, values: list[str]) -> None:
        index = dropdown.get_selected()
        if 0 <= index < len(values):
            setattr(self.app.config, key, values[index])
        if key == "language":
            set_language(self.app.config.language)
        self._schedule_save(immediate=True)

    def _schedule_save(self, *, immediate: bool = False) -> None:
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        if immediate:
            self.app.save_config()
            return

        def save() -> bool:
            self._save_source = 0
            self.app.save_config()
            return False

        self._save_source = GLib.timeout_add(SAVE_DELAY_MS, save)

    def _on_install_shortcut(self, _button) -> None:
        try:
            report = install_shortcut(
                binding=self.app.config.shortcut,
                launch_command=resolve_launch_command(),
            )
        except ShortcutError as exc:
            self._shortcut_label.set_label(str(exc))
            return
        if report.ok:
            self._shortcut_label.set_label(t("settings.shortcut.installed"))
            self.app.notify(t("notify.shortcut_installed"))
        else:
            self._shortcut_label.set_label(t("settings.shortcut.failed"))

    def _on_reset(self, _button) -> None:
        from ..config import Config
        from .dialogs import confirm

        if not confirm(self.window, t("settings.reset"), t("dialog.clear.body_all")):
            return
        defaults = Config()
        self.app.config = defaults
        self.app.save_config()
        self.window.close()
        self.app.show_settings()

    def _open_uri(self, uri: str) -> None:
        try:
            if hasattr(Gtk, "UriLauncher"):  # GTK >= 4.10
                Gtk.UriLauncher.new(uri).launch(self.window, None, None)
            else:  # pragma: no cover - older GTK
                Gio.AppInfo.launch_default_for_uri(uri, None)
        except Exception:  # pragma: no cover - no browser
            log.debug("cannot open %s", uri, exc_info=True)

    def _on_close(self, *_args) -> bool:
        self.app.on_settings_closed()
        return False
