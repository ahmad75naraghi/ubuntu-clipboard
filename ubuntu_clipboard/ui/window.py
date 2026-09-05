"""
window.py — پنجره اصلی کلیپ‌بورد (Win+V)
سعی می‌کند GTK4 + Libadwaita را بارگذاری کند، در غیر این‌صورت Tkinter fallback زیبا.
"""

from __future__ import annotations
import os
import base64
import html
from pathlib import Path

from ..history import HistoryManager, ClipboardItem
from ..config import get_config
from ..clipboard import write_text, write_image_b64
from ..paste import simulate_paste

# ─── تلاش برای GTK ───
_HAS_GTK = False
try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    try:
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        _HAS_ADW = True
    except Exception:
        _HAS_ADW = False
        Adw = None
    from gi.repository import Gtk, Gdk, GLib, Gio, Pango
    _HAS_GTK = True
except Exception:
    _HAS_GTK = False
    _HAS_ADW = False

# ─── HELPER: رنگ و آیکون نوع ───
TYPE_ICON = {
    "text": "📄",
    "code": "⌘",
    "link": "🔗",
    "image": "🖼️",
    "file": "📁",
    "color": "🎨",
}
TYPE_LABEL = {
    "text": "متن",
    "code": "کد",
    "link": "لینک",
    "image": "تصویر",
    "file": "فایل",
    "color": "رنگ",
}

# ─══════════════════════════════════════════
# GTK4 IMPLEMENTATION
# ═══════════════════════════════════════════
if _HAS_GTK:

    CSS_PATH = Path(__file__).parent / "styles.css"

    class ClipboardWindow(Gtk.ApplicationWindow if not _HAS_ADW else Adw.ApplicationWindow):
        def __init__(self, app, history: HistoryManager):
            cfg = get_config()
            super().__init__(application=app)
            self.history = history
            self.cfg = cfg
            self._query = ""
            self._selected_idx = 0
            self._items: list[ClipboardItem] = []

            self.set_title("Clipboard")
            self.set_default_size(cfg.window_width, cfg.window_height)
            # آیکون برای جلوگیری از Unknown در داک
            try:
                self.set_icon_name("ubuntu-clipboard")
            except Exception:
                pass
            try:
                # WM class برای تطابق با .desktop
                self.set_property("application-id", "com.ubuntu.clipboard")
            except Exception:
                pass
            self.set_resizable(True)
            # شیشه‌ای و شناور
            self.add_css_class("clipboard-window")
            if cfg.theme == "light":
                self.add_css_class("light")
            self.set_decorated(False)

            # سایه و موقعیت وسط صفحه
            # css
            self._load_css()

            # ساختار اصلی
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            self.set_content(outer) if _HAS_ADW else self.set_child(outer)

            # ── HEADER ──
            header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            header.add_css_class("header")
            if cfg.theme == "light":
                header.add_css_class("light")
            outer.append(header)

            top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            top_row.set_margin_start(16); top_row.set_margin_end(16)
            top_row.set_margin_top(14); top_row.set_margin_bottom(8)
            header.append(top_row)

            titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            title_lbl = Gtk.Label(label="Clipboard", xalign=0)
            title_lbl.add_css_class("title-label")
            subtitle = Gtk.Label(label="تاریخچه کلیپ‌بورد  •  Win+V", xalign=0)
            subtitle.add_css_class("subtitle-label")
            titles.append(title_lbl); titles.append(subtitle)
            top_row.append(titles)

            # spacer
            top_row.append(Gtk.Box(hexpand=True))

            # clear button
            clear_btn = Gtk.Button(label="پاک کردن همه")
            clear_btn.add_css_class("clear-btn")
            clear_btn.connect("clicked", lambda *_: self._on_clear())
            top_row.append(clear_btn)

            # settings
            settings_btn = Gtk.Button()
            settings_btn.set_icon_name("emblem-system-symbolic")
            settings_btn.add_css_class("icon-btn")
            settings_btn.set_tooltip_text("تنظیمات")
            settings_btn.connect("clicked", lambda *_: self._open_settings())
            top_row.append(settings_btn)

            # close
            close_btn = Gtk.Button()
            close_btn.set_icon_name("window-close-symbolic")
            close_btn.add_css_class("icon-btn")
            close_btn.connect("clicked", lambda *_: self.close())
            top_row.append(close_btn)

            # search
            search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            search_row.set_margin_start(16); search_row.set_margin_end(16)
            search_row.set_margin_bottom(12)
            header.append(search_row)

            self.search_entry = Gtk.SearchEntry()
            self.search_entry.set_placeholder_text("جستجو در کلیپ‌بورد…")
            self.search_entry.add_css_class("search-entry")
            if cfg.theme == "light":
                self.search_entry.add_css_class("light")
            self.search_entry.set_hexpand(True)
            self.search_entry.connect("search-changed", self._on_search)
            search_row.append(self.search_entry)

            # ── BODY ──
            self.scrolled = Gtk.ScrolledWindow()
            self.scrolled.set_vexpand(True)
            self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            outer.append(self.scrolled)

            self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            self.list_box.set_margin_top(8); self.list_box.set_margin_bottom(8)
            self.scrolled.set_child(self.list_box)

            # pinned label
            self.pinned_label = Gtk.Label(label="📌 سنجاق‌شده", xalign=0)
            self.pinned_label.add_css_class("pin-section-label")

            # empty state
            self.empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            self.empty_box.add_css_class("empty-state")
            self.empty_box.set_halign(Gtk.Align.CENTER)
            self.empty_box.set_valign(Gtk.Align.CENTER)
            self.empty_box.set_margin_top(48)
            icon = Gtk.Label(label="📋")
            icon.add_css_class("empty-icon")
            self.empty_box.append(icon)
            self.empty_box.append(Gtk.Label(label="کلیپ‌بورد خالی است"))
            sub = Gtk.Label(label="هر چیزی کپی کنید اینجا ظاهر می‌شود")
            sub.add_css_class("subtitle-label")
            self.empty_box.append(sub)
            hint = Gtk.Label(label="Ctrl+C  →  Win+V  →  Click to paste")
            hint.add_css_class("meta-label")
            hint.set_margin_top(12)
            self.empty_box.append(hint)

            # ── FOOTER ──
            footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            footer.add_css_class("footer")
            outer.append(footer)
            footer.append(Gtk.Label(label="↵ Paste   ↑↓ حرکت   Del حذف   Ctrl+P سنجاق   Esc بستن"))
            footer.append(Gtk.Box(hexpand=True))
            count_lbl = Gtk.Label(label="")
            self.count_label = count_lbl
            footer.append(count_lbl)

            # key controller
            key = Gtk.EventControllerKey()
            key.connect("key-pressed", self._on_key)
            self.add_controller(key)

            # click outside to close?
            # focus
            self.connect("show", lambda *_: self._refresh())

        def _load_css(self):
            try:
                provider = Gtk.CssProvider()
                provider.load_from_path(str(CSS_PATH))
                Gtk.StyleContext.add_provider_for_display(
                    Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )
            except Exception:
                pass

        # ── data ──
        def _refresh(self):
            items = self.history.list(query=self._query, limit=150)
            self._items = items
            # clear
            while True:
                child = self.list_box.get_first_child()
                if not child:
                    break
                self.list_box.remove(child)

            if not items:
                self.list_box.append(self.empty_box)
                self.count_label.set_label("")
                return

            pinned = [x for x in items if x.pinned]
            recent = [x for x in items if not x.pinned]

            if pinned and not self._query:
                self.list_box.append(self.pinned_label)
                for it in pinned:
                    self.list_box.append(self._row(it))
                sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                sep.set_margin_top(8); sep.set_margin_bottom(4)
                sep.set_opacity(0.12)
                self.list_box.append(sep)
                lbl = Gtk.Label(label="🕘 اخیر", xalign=0)
                lbl.add_css_class("pin-section-label")
                self.list_box.append(lbl)
                for it in recent:
                    self.list_box.append(self._row(it))
            else:
                for it in items:
                    self.list_box.append(self._row(it))

            self.count_label.set_label(f"{len(items)} آیتم")
            # select first
            self._selected_idx = 0
            self._update_selection()

        def _row(self, item: ClipboardItem) -> Gtk.Widget:
            cfg = self.cfg
            card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            card.add_css_class("item-card")
            if cfg.theme == "light":
                card.add_css_class("light")
            if item.pinned:
                card.add_css_class("pinned")

            # clickable
            gesture = Gtk.GestureClick()
            gesture.connect("pressed", lambda g,n,x,y: self._on_paste(item))
            card.add_controller(gesture)

            # icon
            icon_lbl = Gtk.Label(label=TYPE_ICON.get(item.type, "📄"))
            icon_lbl.set_size_request(32, 32)
            icon_lbl.set_valign(Gtk.Align.START)
            icon_lbl.set_margin_top(2)
            card.append(icon_lbl)

            # center
            center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            center.set_hexpand(True)
            card.append(center)

            # preview
            if item.type == "image":
                # thumbnail
                try:
                    raw = base64.b64decode(item.content)
                    # use Gtk.Picture from bytes via GdkPixbuf?
                    from gi.repository import GdkPixbuf
                    import io
                    loader = GdkPixbuf.PixbufLoader()
                    loader.write(raw); loader.close()
                    pix = loader.get_pixbuf()
                    if pix:
                        # scale
                        w = pix.get_width(); h = pix.get_height()
                        maxw = 320
                        if w > maxw:
                            pix = pix.scale_simple(maxw, int(h*maxw/w), GdkPixbuf.InterpType.BILINEAR)
                        pic = Gtk.Picture.new_for_pixbuf(pix)
                        pic.set_size_request(-1, 80)
                        pic.set_keep_aspect_ratio(True)
                        center.append(pic)
                except Exception:
                    pass
                prev = Gtk.Label(label="تصویر  •  کلیک برای Paste", xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
            elif item.type == "color":
                prev = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                swatch = Gtk.Box()
                swatch.set_size_request(18,18)
                # css color via style?
                try:
                    prov = Gtk.CssProvider()
                    prov.load_from_data(f"* {{ background: {item.content.strip()}; border-radius: 4px; }}".encode())
                    swatch.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
                except Exception:
                    pass
                prev.append(swatch)
                lbl = Gtk.Label(label=item.content.strip(), xalign=0)
                lbl.add_css_class("preview-label")
                prev.append(lbl)
                # wrap helper
                center.append(prev)
                prev = None
            else:
                # text/code/link/file
                txt = item.preview
                # for file list, show each file
                if item.type == "file":
                    txt = item.content[:220].replace("file://","").replace("\n","  •  ")
                lbl = Gtk.Label(label=txt, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=3, ellipsize=Pango.EllipsizeMode.END)
                lbl.add_css_class("preview-label")
                if cfg.theme == "light":
                    lbl.add_css_class("light")
                prev = lbl

            if prev is not None:
                center.append(prev)

            # meta row
            meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            badge = Gtk.Label(label=TYPE_LABEL.get(item.type, item.type).upper())
            badge.add_css_class("type-badge"); badge.add_css_class(item.type)
            meta.append(badge)
            time_lbl = Gtk.Label(label=item.time_ago, xalign=0)
            time_lbl.add_css_class("meta-label")
            meta.append(time_lbl)
            # size hint
            if item.type != "image":
                sz = len(item.content.encode("utf-8"))
                if sz > 1024:
                    meta.append(Gtk.Label(label=f"• {sz//1024}KB", xalign=0))
                    meta.get_last_child().add_css_class("meta-label")
            center.append(meta)

            # actions
            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
            actions.set_valign(Gtk.Align.CENTER)
            card.append(actions)

            pin_btn = Gtk.Button()
            pin_btn.set_icon_name("view-pin-symbolic" if not item.pinned else "view-pin-symbolic")
            pin_btn.add_css_class("icon-btn")
            if item.pinned:
                pin_btn.add_css_class("pinned-active")
            pin_btn.set_tooltip_text("برداشتن سنجاق" if item.pinned else "سنجاق کردن  (Ctrl+P)")
            pin_btn.connect("clicked", lambda *_: self._on_pin(item))
            actions.append(pin_btn)

            del_btn = Gtk.Button()
            del_btn.set_icon_name("user-trash-symbolic")
            del_btn.add_css_class("icon-btn")
            del_btn.set_tooltip_text("حذف  (Del)")
            del_btn.connect("clicked", lambda *_: self._on_delete(item))
            actions.append(del_btn)

            # store item id on widget
            card._item_id = item.id
            return card

        def _update_selection(self):
            # highlight first? for keyboard nav we add 'selected' class
            idx = 0
            child = self.list_box.get_first_child()
            while child:
                if child.get_first_child():  # skip labels/separators
                    pass
                # check if is item-card
                if child.has_css_class("item-card"):
                    if idx == self._selected_idx:
                        child.add_css_class("selected")
                    else:
                        child.remove_css_class("selected")
                    idx += 1
                child = child.get_next_sibling()

        # ── events ──
        def _on_search(self, entry):
            self._query = entry.get_text()
            self._refresh()

        def _on_key(self, ctrl, keyval, keycode, state):
            name = Gdk.keyval_name(keyval)
            # Esc close
            if name == "Escape":
                self.close()
                return True
            if name == "Down":
                self._selected_idx = min(self._selected_idx+1, len(self._items)-1)
                self._update_selection()
                return True
            if name == "Up":
                self._selected_idx = max(self._selected_idx-1, 0)
                self._update_selection()
                return True
            if name in ("Return", "KP_Enter"):
                if 0 <= self._selected_idx < len(self._items):
                    self._on_paste(self._items[self._selected_idx])
                return True
            if name == "Delete":
                if 0 <= self._selected_idx < len(self._items):
                    self._on_delete(self._items[self._selected_idx])
                return True
            # Ctrl+P pin
            if (state & Gdk.ModifierType.CONTROL_MASK) and name and name.lower() == "p":
                if 0 <= self._selected_idx < len(self._items):
                    self._on_pin(self._items[self._selected_idx])
                return True
            # numbers paste
            if name and name.isdigit() and (state & Gdk.ModifierType.CONTROL_MASK):
                idx = int(name)-1
                if 0 <= idx < len(self._items):
                    self._on_paste(self._items[idx])
                return True
            return False

        def _on_pin(self, item: ClipboardItem):
            self.history.toggle_pin(item.id)
            self._refresh()

        def _on_delete(self, item: ClipboardItem):
            self.history.delete(item.id)
            self._refresh()

        def _on_clear(self):
            # dialog
            dlg = Gtk.MessageDialog(
                transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK_CANCEL, text="پاک کردن همه؟"
            )
            dlg.set_property("secondary-text", "آیتم‌های سنجاق‌شده نگه داشته می‌شوند. مطمئن هستید؟")
            dlg.connect("response", lambda d, r: (d.close(), self._do_clear() if r == Gtk.ResponseType.OK else None))
            dlg.present()

        def _do_clear(self):
            self.history.clear(keep_pinned=get_config().keep_pinned_on_clear)
            self._refresh()

        def _on_paste(self, item: ClipboardItem):
            # copy to clipboard then simulate paste — hide, paste, then close window so process exits
            self.set_visible(False)
            # small delay to let window hide and focus return
            def do():
                try:
                    if item.type == "image":
                        ok = write_image_b64(item.content)
                    else:
                        ok = write_text(item.content)
                    if ok:
                        GLib.timeout_add(160, lambda: (simulate_paste(delay=0.05), False))
                except Exception:
                    pass
                # close window after paste so lock is cleared and no flicker
                GLib.timeout_add(350, lambda: (self.close(), False)[1])
                return False
            GLib.timeout_add(80, do)

        def _open_settings(self):
            from .settings import show_settings
            show_settings(self, self.history)

        def toggle_visible(self):
            if self.is_visible():
                self.close()
            else:
                self._refresh()
                self.present()
                # center on screen
                self.search_entry.grab_focus()

else:
    # ─══════════════════════════════════════════
    # TKINTER FALLBACK (for CI / preview without GTK)
    # ═══════════════════════════════════════════
    _HAS_TK = False
    _TK_IMPORT_ERROR = None
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
        _HAS_TK = True
    except ImportError as _e:
        _HAS_TK = False
        _TK_IMPORT_ERROR = _e
        tk = None
        messagebox = None
        print(f"⚠️  tkinter نصب نیست: {_e}")
        print("   نصب: sudo apt install python3-tk -y")
        print("   یا:  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1  (پیشنهادی)")
        try:
            import subprocess, shutil
            if shutil.which("notify-send"):
                subprocess.run(["notify-send", "Ubuntu Clipboard", "tkinter نصب نیست\n sudo apt install python3-tk  یا  python3-gi"], timeout=2)
            if shutil.which("zenity"):
                subprocess.Popen(["zenity","--error","--text=tkinter نصب نیست\nsudo apt install python3-tk\nیا python3-gi برای تجربه کامل","--title=Clipboard Error"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    class ClipboardWindow:
        """Fallback زیبا با Tkinter — استایل ویندوز 11"""
        def __init__(self, app=None, history: HistoryManager = None):
            if not _HAS_TK:
                from ..history import HistoryManager as _HM
                self.history = history or _HM()
                self.cfg = get_config()
                self._query = ""
                self._root = None
                self._list_frame = None
                self._search_var = None
                print("✗ GUI toolkit موجود نیست — تنها دیمن فعال است")
                print("  sudo apt install python3-tk python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 -y")
                return
            self.history = history or HistoryManager()
            self.cfg = get_config()
            self._query = ""
            self._root = None
            self._list_frame = None
            self._search_var = None

        def _ensure(self):
            if not _HAS_TK:
                print("✗ tkinter موجود نیست — نمی‌توان پنجره را باز کرد")
                try:
                    import subprocess, shutil
                    if shutil.which("notify-send"):
                        subprocess.run(["notify-send","Clipboard","GUI toolkit موجود نیست — sudo apt install python3-tk"], timeout=2)
                except Exception: pass
                return
            if self._root and self._root.winfo_exists():
                return
            self._root = tk.Tk()
            self._root.title("Clipboard — Win+V")
            self._root.geometry(f"{self.cfg.window_width}x{self.cfg.window_height}")
            self._root.configure(bg="#202124")
            self._root.overrideredirect(False)
            self._root.attributes("-topmost", True)
            # rounded via? Tk doesn't support blur, simulate with colors
            self._build()

        def _build(self):
            r = self._root
            # header
            header = tk.Frame(r, bg="#202124", padx=16, pady=12)
            header.pack(fill="x")
            tk.Label(header, text="Clipboard", fg="white", bg="#202124", font=("Segoe UI", 13, "bold")).pack(anchor="w")
            tk.Label(header, text="تاریخچه کلیپ‌بورد  •  Win+V", fg="#9aa0a6", bg="#202124", font=("Segoe UI", 9)).pack(anchor="w")
            # search
            search_frame = tk.Frame(header, bg="#202124")
            search_frame.pack(fill="x", pady=(10,0))
            self._search_var = tk.StringVar()
            self._search_var.trace_add("write", lambda *_: self._refresh())
            e = tk.Entry(search_frame, textvariable=self._search_var, bg="#303134", fg="white", insertbackground="white", relief="flat", font=("Segoe UI", 10))
            e.pack(fill="x", ipady=6, padx=2)
            e.insert(0, "")
            e.bind("<KeyRelease>", lambda evt: None)

            # controls
            ctrl = tk.Frame(header, bg="#202124")
            ctrl.pack(fill="x", pady=(8,0))
            tk.Button(ctrl, text="پاک کردن همه", command=self._on_clear, bg="#303134", fg="white", relief="flat", padx=10, pady=4).pack(side="right", padx=4)
            tk.Button(ctrl, text="✕ بستن", command=self.hide, bg="#303134", fg="white", relief="flat").pack(side="right")

            # scrolled list
            container = tk.Frame(r, bg="#202124")
            container.pack(fill="both", expand=True, padx=8, pady=8)
            canvas = tk.Canvas(container, bg="#202124", highlightthickness=0)
            scrollbar = tk.Scrollbar(container, orient="vertical", command=canvas.yview)
            self._list_frame = tk.Frame(canvas, bg="#202124")
            self._list_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.create_window((0,0), window=self._list_frame, anchor="nw")
            canvas.configure(yscrollcommand=scrollbar.set)
            canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")

            # footer
            footer = tk.Frame(r, bg="#2a2b2e", padx=12, pady=8)
            footer.pack(fill="x", side="bottom")
            tk.Label(footer, text="↵ Paste   Del حذف   Ctrl+P سنجاق   Esc بستن", fg="#9aa0a6", bg="#2a2b2e", font=("Segoe UI", 8)).pack(side="left")
            self._root.bind("<Escape>", lambda *_: self.hide())
            self._root.bind("<Control-p>", lambda *_: None)

        def _refresh(self, *args):
            if not self._root or not self._root.winfo_exists():
                return
            query = self._search_var.get() if self._search_var else ""
            items = self.history.list(query=query, limit=120)
            for w in self._list_frame.winfo_children():
                w.destroy()
            if not items:
                tk.Label(self._list_frame, text="📋\nکلیپ‌بورد خالی است\nهر چیزی کپی کنید اینجا ظاهر می‌شود", fg="#9aa0a6", bg="#202124", justify="center", font=("Segoe UI", 10)).pack(pady=40)
                return
            for it in items:
                card = tk.Frame(self._list_frame, bg="#303134", padx=10, pady=10)
                card.pack(fill="x", pady=3)
                # type badge
                top = tk.Frame(card, bg="#303134")
                top.pack(fill="x")
                tk.Label(top, text=TYPE_ICON.get(it.type,"📄") + " " + TYPE_LABEL.get(it.type,it.type), fg="#8ab4f8", bg="#202124", font=("Segoe UI", 8, "bold"), padx=6, pady=2).pack(side="left")
                tk.Label(top, text=it.time_ago, fg="#9aa0a6", bg="#303134", font=("Segoe UI", 8)).pack(side="left", padx=8)
                if it.pinned:
                    tk.Label(top, text="📌", bg="#303134", fg="#8ab4f8").pack(side="right")
                preview = it.preview[:220].replace("\n"," ")
                if it.type == "image":
                    preview = "🖼️  تصویر — کلیک برای Paste"
                tk.Label(card, text=preview, fg="white", bg="#303134", anchor="w", justify="left", wraplength=360, font=("Segoe UI", 10)).pack(fill="x", pady=(6,4))
                btns = tk.Frame(card, bg="#303134")
                btns.pack(fill="x")
                tk.Button(btns, text="Paste", command=lambda it=it: self._on_paste(it), bg="#8ab4f8", fg="#202124", relief="flat", padx=10).pack(side="left")
                tk.Button(btns, text="📌 سنجاق" if not it.pinned else "برداشتن", command=lambda it=it: self._on_pin(it), bg="#3c4043", fg="white", relief="flat", padx=8).pack(side="left", padx=4)
                tk.Button(btns, text="🗑️ حذف", command=lambda it=it: self._on_delete(it), bg="#3c4043", fg="white", relief="flat", padx=8).pack(side="left")
                card.bind("<Button-1>", lambda e, it=it: self._on_paste(it))

        def _on_pin(self, it: ClipboardItem):
            self.history.toggle_pin(it.id); self._refresh()
        def _on_delete(self, it: ClipboardItem):
            self.history.delete(it.id); self._refresh()
        def _on_clear(self):
            if messagebox.askyesno("پاک کردن همه؟", "آیتم‌های سنجاق‌شده نگه داشته می‌شوند."):
                self.history.clear(keep_pinned=True); self._refresh()
        def _on_paste(self, it: ClipboardItem):
            self.hide()
            self._root.after(180, lambda: self._do_paste(it))
        def _do_paste(self, it: ClipboardItem):
            try:
                if it.type == "image":
                    write_image_b64(it.content)
                else:
                    write_text(it.content)
                simulate_paste(delay=0.12)
            except Exception:
                pass

        def toggle_visible(self):
            self._ensure()
            if self._root.state() == "withdrawn" or not self._root.winfo_viewable():
                self._root.deiconify(); self._root.lift(); self._root.focus_force()
                self._refresh()
            else:
                self.hide()
        def hide(self):
            if self._root:
                self._root.withdraw()
        def present(self): self.toggle_visible()
        def set_visible(self, v: bool):
            self._ensure()
            if v: self._root.deiconify()
            else: self.hide()
        def is_visible(self):
            return bool(self._root and self._root.winfo_viewable())
        def show(self):
            self._ensure(); self._root.deiconify(); self._root.mainloop()
