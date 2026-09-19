"""The ``Win+V`` popup window (GTK 4, optionally styled by libadwaita)."""

from __future__ import annotations

import contextlib
import logging
import time
from collections import OrderedDict

from .. import APP_ICON
from ..i18n import t
from ..models import ClipboardItem, ContentType, file_preview, format_size
from . import Gdk, GLib, Gtk, Pango

log = logging.getLogger(__name__)

#: Largest number of decoded thumbnails kept in memory.
THUMBNAIL_CACHE = 24
#: Ignore focus changes right after being presented.
FOCUS_GRACE_SECONDS = 0.35

TYPE_ICON = {
    ContentType.TEXT: "📄",
    ContentType.CODE: "⌨",
    ContentType.LINK: "🔗",
    ContentType.IMAGE: "🖼",
    ContentType.FILE: "📁",
    ContentType.COLOR: "🎨",
}


class ClipboardWindow(Gtk.ApplicationWindow):  # type: ignore[misc]
    """Frameless, keyboard driven list of clipboard items."""

    def __init__(self, app) -> None:
        super().__init__(application=app)
        self.app = app
        self.config = app.config
        self.store = app.store
        self._items: list[ClipboardItem] = []
        self._selected = 0
        self._thumbnails: OrderedDict[int, object] = OrderedDict()
        self._refresh_pending = False
        self._was_active = False
        self._focus_grace_until = 0.0
        self._focus_handler = None

        self.set_title(t("app.name"))
        self.set_default_size(self.config.window_width, self.config.window_height)
        self.set_resizable(True)
        self.set_decorated(False)
        with contextlib.suppress(AttributeError, TypeError):  # pragma: no cover - GTK4 always has it
            self.set_icon_name(APP_ICON)
        self.add_css_class("clipboard-window")

        self._build()
        self._install_controllers()
        self.apply_theme(app.dark_mode)
        self.apply_config()

    # ── construction ───────────────────────────────────────────────────────
    def _build(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(root)

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        header.add_css_class("header")

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
        title = Gtk.Label(label=t("app.name"), xalign=0)
        title.add_css_class("title-label")
        subtitle = Gtk.Label(label=t("app.subtitle"), xalign=0)
        subtitle.add_css_class("subtitle-label")
        titles.append(title)
        titles.append(subtitle)
        top_row.append(titles)

        self.clear_button = Gtk.Button(label=t("action.clear"))
        self.clear_button.add_css_class("clear-btn")
        self.clear_button.set_tooltip_text(t("action.clear_history"))
        self.clear_button.connect("clicked", self._on_clear_clicked)
        top_row.append(self.clear_button)

        self.settings_button = Gtk.Button(icon_name="emblem-system-symbolic")
        self.settings_button.add_css_class("icon-btn")
        self.settings_button.set_tooltip_text(t("action.settings"))
        self.settings_button.connect("clicked", lambda *_: self.app.show_settings())
        top_row.append(self.settings_button)

        self.close_button = Gtk.Button(icon_name="window-close-symbolic")
        self.close_button.add_css_class("icon-btn")
        self.close_button.set_tooltip_text(t("action.close"))
        self.close_button.connect("clicked", lambda *_: self.hide_window())
        top_row.append(self.close_button)

        header.append(top_row)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text(t("search.placeholder"))
        self.search_entry.add_css_class("search-entry")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", self._on_search_changed)
        header.append(self.search_entry)

        # A frameless window needs an explicit drag area.
        self.handle = Gtk.WindowHandle()
        self.handle.set_child(header)
        root.append(self.handle)

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled.set_vexpand(True)
        self.scrolled.set_kinetic_scrolling(True)
        root.append(self.scrolled)

        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.list_box.add_css_class("list-area")
        self.scrolled.set_child(self.list_box)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.add_css_class("footer")
        self.hint_label = Gtk.Label(label=t("footer.hints"), xalign=0, hexpand=True)
        self.count_label = Gtk.Label(label="")
        footer.append(self.hint_label)
        footer.append(self.count_label)
        root.append(footer)

    def _install_controllers(self) -> None:
        keys = Gtk.EventControllerKey()
        # Capture phase: the search entry takes focus, but Up/Down/Return/Delete
        # must still drive the list instead of being swallowed by the text widget.
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)
        self.connect("close-request", self._on_close_request)
        self.connect("map", self._on_mapped)
        self.connect("unmap", self._on_unmapped)
        self.connect("notify::is-active", self._on_active_changed)

    # ── public API ─────────────────────────────────────────────────────────
    def apply_config(self) -> None:
        """Re-read sizes/theme after the settings changed."""
        self.config = self.app.config
        self.set_default_size(self.config.window_width, self.config.window_height)
        self.apply_theme(self.app.dark_mode)
        self._thumbnails.clear()
        self.refresh(force=True)

    def apply_theme(self, dark: bool) -> None:
        """Switch between the dark and light stylesheet variants."""
        self.remove_css_class("light")
        self.remove_css_class("dark")
        self.add_css_class("dark" if dark else "light")
        # The search entry is an inner node, so it needs its own class.
        self.search_entry.remove_css_class("light")
        if not dark:
            self.search_entry.add_css_class("light")

    def refresh(self, force: bool = False) -> None:
        """Rebuild the list from the store (coalesced through the main loop)."""
        if not force and not self.get_visible():
            return
        if self._refresh_pending:
            return
        self._refresh_pending = True

        def rebuild() -> bool:
            self._refresh_pending = False
            self._rebuild()
            return False

        GLib.idle_add(rebuild)

    def hide_window(self) -> None:
        self.hide()

    # ── list building ──────────────────────────────────────────────────────
    def _rebuild(self) -> None:
        query = self.search_entry.get_text() if self.search_entry else ""
        items = self.store.list(query=query, limit=200)
        self._items = items
        self._selected = min(self._selected, max(0, len(items) - 1))

        child = self.list_box.get_first_child()
        while child is not None:
            self.list_box.remove(child)
            child = self.list_box.get_first_child()

        if not items:
            self.list_box.append(self._build_empty_state(query))
            self.count_label.set_label("")
            return

        pinned = [item for item in items if item.pinned]
        recent = [item for item in items if not item.pinned]
        if pinned and not query:
            self.list_box.append(self._section_label(t("section.pinned")))
            for item in pinned:
                self.list_box.append(self._build_row(item))
            if recent:
                self.list_box.append(self._section_label(t("section.recent")))
            display = recent
        else:
            display = items
        for item in display:
            self.list_box.append(self._build_row(item))

        self.count_label.set_label(t("count.items", count=len(items)))
        self._update_selection()
        self.scrolled.get_vadjustment().set_value(0)

    def _build_empty_state(self, query: str) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("empty-state")
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.set_vexpand(True)
        icon = Gtk.Label(label="📋")
        icon.add_css_class("empty-icon")
        box.append(icon)
        box.append(Gtk.Label(label=t("empty.title") if not query else t("empty.hint")))
        hint = Gtk.Label(label=t("empty.tip"))
        hint.add_css_class("subtitle-label")
        box.append(hint)
        return box

    def _section_label(self, text: str) -> Gtk.Widget:
        label = Gtk.Label(label=text, xalign=0)
        label.add_css_class("section-label")
        return label

    def _build_row(self, item: ClipboardItem) -> Gtk.Widget:
        dark = self.app.dark_mode
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        card.add_css_class("item-card")
        if not dark:
            card.add_css_class("light")
        if item.pinned:
            card.add_css_class("pinned")

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        content.set_hexpand(True)
        content.set_valign(Gtk.Align.CENTER)
        card.append(content)

        icon = Gtk.Label(label=TYPE_ICON.get(item.type, "📄"))
        icon.add_css_class("item-icon")
        icon.set_valign(Gtk.Align.START)
        content.append(icon)

        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        center.set_hexpand(True)
        content.append(center)
        center.append(self._build_payload(item, dark))
        center.append(self._build_meta(item))

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        actions.set_valign(Gtk.Align.CENTER)
        card.append(actions)

        pin_button = Gtk.Button(icon_name="view-pin-symbolic")
        pin_button.add_css_class("icon-btn")
        if item.pinned:
            pin_button.add_css_class("pinned-active")
        pin_button.set_tooltip_text(t("action.unpin") if item.pinned else t("action.pin"))
        pin_button.connect("clicked", lambda *_: self._on_pin(item))
        actions.append(pin_button)

        delete_button = Gtk.Button(icon_name="user-trash-symbolic")
        delete_button.add_css_class("icon-btn")
        delete_button.set_tooltip_text(t("action.delete"))
        delete_button.connect("clicked", lambda *_: self._on_delete(item))
        actions.append(delete_button)

        # The gesture lives on the content area only: the v1 code attached it to
        # the whole card, so clicking pin/trash pasted the item as a side effect.
        gesture = Gtk.GestureClick()
        gesture.set_button(1)
        gesture.connect("pressed", lambda *_: self._activate(item))
        content.add_controller(gesture)
        return card

    def _build_payload(self, item: ClipboardItem, dark: bool) -> Gtk.Widget:
        if item.type is ContentType.IMAGE:
            preview = self._build_image_preview(item)
            if preview is not None:
                return preview
        if item.type is ContentType.COLOR:
            return self._build_color_preview(item)
        label = Gtk.Label(xalign=0, wrap=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, lines=3)
        label.add_css_class("preview-label")
        if not dark:
            label.add_css_class("light")
        if item.type is ContentType.IMAGE:
            label.set_label(f"{t('preview.image')} — {t('preview.image_hint')}")
        elif item.type is ContentType.FILE:
            label.set_label(self._file_preview(item))
        else:
            label.set_label(item.preview)
            if item.type is ContentType.CODE:
                label.add_css_class("mono")
        label.set_ellipsize(Pango.EllipsizeMode.END)
        return label

    def _build_image_preview(self, item: ClipboardItem) -> Gtk.Widget | None:
        texture = self._texture_for(item)
        if texture is None:
            return None
        picture = Gtk.Picture.new_for_paintable(texture)
        picture.set_can_shrink(True)
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        picture.set_size_request(180, self.config.image_thumb_height)
        return picture

    def _texture_for(self, item: ClipboardItem):
        cached = self._thumbnails.get(item.id)
        if cached is not None:
            self._thumbnails.move_to_end(item.id)
            return cached
        try:
            data = self.store.get_image_bytes(item.id)
            if not data:
                return None
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except Exception:
            log.debug("cannot decode thumbnail for item %s", item.id, exc_info=True)
            return None
        self._thumbnails[item.id] = texture
        while len(self._thumbnails) > THUMBNAIL_CACHE:
            self._thumbnails.popitem(last=False)
        return texture

    def _build_color_preview(self, item: ClipboardItem) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        color = item.preview.strip()
        swatch = Gtk.DrawingArea()
        swatch.set_content_width(18)
        swatch.set_content_height(18)
        swatch.set_draw_func(self._draw_swatch, color)
        row.append(swatch)
        label = Gtk.Label(label=color, xalign=0)
        label.add_css_class("preview-label")
        row.append(label)
        return row

    @staticmethod
    def _draw_swatch(_area: Gtk.DrawingArea, cr, width: int, height: int, color: str) -> None:
        rgba = Gdk.RGBA()
        if not rgba.parse(color):
            rgba.parse("#888888")
        cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, rgba.alpha)
        cr.rectangle(0, 0, width, height)
        cr.fill()
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.4)
        cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, max(0, width - 1), max(0, height - 1))
        cr.stroke()

    def _build_meta(self, item: ClipboardItem) -> Gtk.Widget:
        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        badge = Gtk.Label(label=item.display_type)
        badge.add_css_class("type-badge")
        badge.add_css_class(item.type.value)
        meta.append(badge)
        time_label = Gtk.Label(label=item.relative_time(), xalign=0)
        time_label.add_css_class("meta-label")
        meta.append(time_label)
        size = item.size_label if item.type is ContentType.IMAGE else format_size(item.size_bytes)
        if size:
            size_label = Gtk.Label(label=f"• {size}", xalign=0)
            size_label.add_css_class("meta-label")
            meta.append(size_label)
        return meta

    def _file_preview(self, item: ClipboardItem) -> str:
        text = self.store.get_text(item.id) or item.content or ""
        uris = [line for line in text.splitlines() if line.strip()] or item.uris()
        return file_preview(uris) if uris else item.preview

    # ── selection & scrolling ──────────────────────────────────────────────
    def _rows(self) -> list[Gtk.Widget]:
        rows: list[Gtk.Widget] = []
        child = self.list_box.get_first_child()
        while child is not None:
            if child.has_css_class("item-card"):
                rows.append(child)
            child = child.get_next_sibling()
        return rows

    def _update_selection(self) -> None:
        rows = self._rows()
        for index, row in enumerate(rows):
            if index == self._selected:
                if not row.has_css_class("selected"):
                    row.add_css_class("selected")
            elif row.has_css_class("selected"):
                row.remove_css_class("selected")
        if 0 <= self._selected < len(rows):
            self._scroll_into_view(rows[self._selected])

    def _move_selection(self, delta: int) -> None:
        rows = self._rows()
        if not rows:
            return
        self._selected = max(0, min(len(rows) - 1, self._selected + delta))
        self._update_selection()

    def _scroll_into_view(self, row: Gtk.Widget) -> None:
        try:
            ok, bounds = row.compute_bounds(self.list_box)
        except (TypeError, AttributeError):  # pragma: no cover - ancient GTK
            return
        if not ok:
            return
        adjustment = self.scrolled.get_vadjustment()
        top = bounds.get_y()
        bottom = top + bounds.get_height()
        value = adjustment.get_value()
        page = adjustment.get_page_size()
        if top < value:
            adjustment.set_value(max(0.0, top - 8))
        elif bottom > value + page:
            adjustment.set_value(bottom - page + 8)

    # ── events ─────────────────────────────────────────────────────────────
    def _on_search_changed(self, _entry) -> None:
        self._selected = 0
        self._rebuild()

    def _on_key_pressed(self, _controller, keyval, _keycode, state) -> bool:
        key = Gdk.keyval_name(keyval) or ""
        control = bool(state & Gdk.ModifierType.CONTROL_MASK)
        selected = self._items[self._selected] if 0 <= self._selected < len(self._items) else None

        if key == "Escape":
            self.hide_window()
            return True
        if key in {"Up", "KP_Up"}:
            self._move_selection(-1)
            return True
        if key in {"Down", "KP_Down"}:
            self._move_selection(1)
            return True
        if key in {"Return", "KP_Enter"}:
            if selected is not None:
                self._activate(selected)
            return True
        if control and key in {"p", "P"}:
            if selected is not None:
                self._on_pin(selected)
            return True
        if control and key.isdigit() and key != "0":
            index = int(key) - 1
            if 0 <= index < len(self._items):
                self._activate(self._items[index])
            return True
        if key in {"Delete", "KP_Delete"}:
            # While the user is editing a search term, Delete belongs to the
            # text field; only an empty search box may delete a history item.
            editing = bool(self.search_entry.has_focus() and self.search_entry.get_text())
            if not editing and selected is not None:
                self._on_delete(selected)
                return True
            return False
        if key == "Tab" and self._rows():
            backwards = bool(state & Gdk.ModifierType.SHIFT_MASK)
            self._move_selection(-1 if backwards else 1)
            return True
        return False

    def _activate(self, item: ClipboardItem) -> None:
        log.debug("pasting item %s", item.id)
        self.app.paste_item(item)

    def _on_pin(self, item: ClipboardItem) -> None:
        self.store.toggle_pin(item.id)
        self.refresh(force=True)

    def _on_delete(self, item: ClipboardItem) -> None:
        self.store.delete(item.id)
        self.refresh(force=True)

    def _on_clear_clicked(self, _button) -> None:
        from .dialogs import confirm

        keep_pinned = self.config.keep_pinned_on_clear
        body = t("dialog.clear.body") if keep_pinned else t("dialog.clear.body_all")
        if confirm(self, t("dialog.clear.title"), body):
            self.app.clear_history()
            self.refresh(force=True)

    def _on_close_request(self, *_args) -> bool:
        self.hide_window()
        return True  # keep the window (and the process) alive

    def _on_mapped(self, *_args) -> None:
        self._focus_grace_until = time.monotonic() + FOCUS_GRACE_SECONDS
        self.refresh(force=True)
        self.search_entry.grab_focus()

    def _on_unmapped(self, *_args) -> None:
        self._was_active = False

    def _on_active_changed(self, *_args) -> None:
        """Hide the popup when it loses focus, like Windows 11 does."""
        if self.is_active():
            self._was_active = True
            return
        if (
            self.config.close_on_focus_loss
            and self._was_active
            and time.monotonic() > self._focus_grace_until
        ):
            self._was_active = False
            self.hide_window()
