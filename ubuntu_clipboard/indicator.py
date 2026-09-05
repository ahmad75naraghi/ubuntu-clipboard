"""
indicator.py — آیکون تسک‌بار / سینی سیستم (Top Bar) برای اوبونتو
- روی همه نسخه‌ها کار می‌کند: AyatanaAppIndicator → AppIndicator → Gtk.StatusIcon → fallback
- کلیک روی آیکون = باز کردن پنجره شیشه‌ای (مثل ویندوز)
- منو: باز کردن کلیپ‌بورد / تنظیمات / پاک کردن / خروج
"""

from __future__ import annotations
import os
import sys
from pathlib import Path

_HAS_GI = False
try:
    import gi
    _HAS_GI = True
except Exception:
    _HAS_GI = False

def _get_icon_path() -> str:
    # اولویت: آیکون نصب‌شده در سیستم
    candidates = [
        Path.home() / ".local/share/icons/ubuntu-clipboard.png",
        Path("/usr/share/icons/hicolor/512x512/apps/ubuntu-clipboard.png"),
        Path(__file__).parent / "assets/icon.png",
        Path(__file__).parent / "assets/preview.png",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return "edit-paste"  # fallback icon name


class TrayIndicator:
    """مدیریت آیکون تسک‌بار"""

    def __init__(self, app):
        self.app = app
        self._indicator = None
        self._status_icon = None
        self._backend = "none"
        self.icon_path = _get_icon_path()

    def setup(self):
        """ساخت آیکون — بهترین backend موجود را انتخاب کن"""
        # سعی کن اول pystray اگر GI با Gtk4 تداخل دارد
        # pystray مستقل است و با Wayland هم بهتر کار می‌کند
        if self._try_pystray():
            return True

        if not _HAS_GI:
            print("⚠️  GI موجود نیست — آیکون تسک‌بار ساخته نشد. pip install pystray را امتحان کنید.")
            return False

        # 1) AyatanaAppIndicator3 (Ubuntu 22.04+ پیش‌فرض) — فقط اگر Gtk4 لود نشده باشد
        # چون Gtk3 و Gtk4 همزمان لود نمی‌شوند
        try:
            import gi
            # اگر قبلاً Gtk 4 لود شده، Ayatana (Gtk3) را اسکیپ کن و برو pystray
            # gi.get_required_version('Gtk') برمی‌گرداند
            pass
        except Exception:
            pass
        # تلاش کن ولی اگر تداخل نسخه بود، pystray قبلاً موفق شده
        if self._try_ayatana():
            return True
        # 2) AppIndicator3 قدیمی
        if self._try_appindicator():
            return True
        # 3) Gtk.StatusIcon (deprecated ولی هنوز کار می‌کند)
        if self._try_status_icon():
            return True

        print("⚠️  هیچ backend برای سینی سیستم پیدا نشد — افزونه Top Bar را نصب کنید: sudo apt install gir1.2-ayatanaappindicator3-0.1 gnome-shell-extension-appindicator")
        print("   یا: pip install pystray Pillow — سپس دوباره اجرا کنید")
        return False

    def _try_pystray(self) -> bool:
        try:
            import pystray
            from PIL import Image
            # load icon
            if os.path.exists(self.icon_path):
                try:
                    img = Image.open(self.icon_path)
                    # resize to 64 for tray
                    img = img.resize((64, 64))
                except Exception:
                    img = Image.new("RGB", (64, 64), "#4f7cff")
            else:
                img = Image.new("RGB", (64, 64), "#4f7cff")
            # منو
            def open_cb(icon, item):
                self._on_open()
            def settings_cb(icon, item):
                self._on_settings()
            def clear_cb(icon, item):
                self._on_clear()
            def quit_cb(icon, item):
                self._on_quit()
                try:
                    icon.stop()
                except Exception:
                    pass

            menu = pystray.Menu(
                pystray.MenuItem("📋  باز کردن کلیپ‌بورد  (Win+V)", open_cb, default=True),
                pystray.MenuItem("⚙️  تنظیمات", settings_cb),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("🗑️  پاک کردن تاریخچه", clear_cb),
                pystray.MenuItem("❌  خروج", quit_cb),
            )
            icon = pystray.Icon("ubuntu-clipboard", img, "Clipboard — Win+V", menu)
            # run detached
            import threading
            t = threading.Thread(target=icon.run, daemon=True, name="tray-pystray")
            t.start()
            self._indicator = icon
            self._backend = "pystray"
            print("✓ آیکون تسک‌بار فعال شد (pystray) — کنار ساعت/وای‌فای ببینید")
            return True
        except Exception as e:
            # print(f"pystray failed: {e}")
            return False

    # ── Ayatana ──
    def _try_ayatana(self) -> bool:
        try:
            import gi
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3, Gtk
            ind = AyatanaAppIndicator3.Indicator.new(
                "ubuntu-clipboard",
                self.icon_path if os.path.exists(self.icon_path) else "edit-paste",
                AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS
            )
            ind.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
            ind.set_title("Clipboard — Win+V")

            # منو
            menu = Gtk.Menu()

            item_open = Gtk.MenuItem(label="📋  باز کردن کلیپ‌بورد  (Win+V)")
            item_open.connect("activate", lambda *_: self._on_open())
            menu.append(item_open)

            item_settings = Gtk.MenuItem(label="⚙️  تنظیمات")
            item_settings.connect("activate", lambda *_: self._on_settings())
            menu.append(item_settings)

            menu.append(Gtk.SeparatorMenuItem())

            item_status = Gtk.MenuItem(label="ℹ️  وضعیت: فعال — کلیک کنید")
            item_status.set_sensitive(False)
            menu.append(item_status)

            item_clear = Gtk.MenuItem(label="🗑️  پاک کردن تاریخچه")
            item_clear.connect("activate", lambda *_: self._on_clear())
            menu.append(item_clear)

            menu.append(Gtk.SeparatorMenuItem())

            item_quit = Gtk.MenuItem(label="❌  خروج")
            item_quit.connect("activate", lambda *_: self._on_quit())
            menu.append(item_quit)

            menu.show_all()
            ind.set_menu(menu)

            # اگر آیکون فایل است، آن را ست کن
            try:
                if os.path.exists(self.icon_path):
                    ind.set_icon_full(self.icon_path, "Clipboard")
            except Exception:
                pass

            self._indicator = ind
            self._backend = "ayatana"
            print(f"✓ آیکون تسک‌بار فعال شد (Ayatana) — {self.icon_path}")
            return True
        except Exception as e:
            # print(f"Ayatana failed: {e}")
            return False

    # ── AppIndicator قدیمی ──
    def _try_appindicator(self) -> bool:
        try:
            import gi
            gi.require_version("AppIndicator3", "0.1")
            from gi.repository import AppIndicator3, Gtk
            ind = AppIndicator3.Indicator.new(
                "ubuntu-clipboard",
                self.icon_path if os.path.exists(self.icon_path) else "edit-paste",
                AppIndicator3.IndicatorCategory.APPLICATION_STATUS
            )
            ind.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
            ind.set_title("Clipboard — Win+V")
            menu = Gtk.Menu()
            for label, cb in [
                ("📋  باز کردن کلیپ‌بورد", self._on_open),
                ("⚙️  تنظیمات", self._on_settings),
                (None, None),
                ("🗑️  پاک کردن", self._on_clear),
                ("❌  خروج", self._on_quit),
            ]:
                if label is None:
                    menu.append(Gtk.SeparatorMenuItem())
                else:
                    it = Gtk.MenuItem(label=label)
                    it.connect("activate", lambda _, c=cb: c())
                    menu.append(it)
            menu.show_all()
            ind.set_menu(menu)
            self._indicator = ind
            self._backend = "appindicator"
            print(f"✓ آیکون تسک‌بار فعال شد (AppIndicator)")
            return True
        except Exception:
            return False

    # ── Gtk.StatusIcon (fallback) ──
    def _try_status_icon(self) -> bool:
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            # Gtk4 StatusIcon ندارد — برای Gtk3 fallback
            # سعی کن Gtk3 را لود کنی
            try:
                gi.require_version("Gtk", "3.0")
            except Exception:
                return False
            from gi.repository import Gtk
            # StatusIcon در Gtk3 موجود است
            if not hasattr(Gtk, "StatusIcon"):
                return False
            icon = Gtk.StatusIcon()
            try:
                if os.path.exists(self.icon_path):
                    icon.set_from_file(self.icon_path)
                else:
                    icon.set_from_icon_name("edit-paste")
            except Exception:
                icon.set_from_icon_name("edit-paste")
            icon.set_tooltip_text("Clipboard — کلیک برای باز کردن (Win+V)")
            icon.set_visible(True)
            icon.connect("activate", lambda *_: self._on_open())
            icon.connect("popup-menu", lambda *_: self._on_open())
            self._status_icon = icon
            self._backend = "statusicon"
            print("✓ آیکون تسک‌بار فعال شد (Gtk.StatusIcon)")
            return True
        except Exception as e:
            return False

    # ── callbacks ──
    def _on_open(self):
        try:
            # GLib idle برای thread-safety
            from gi.repository import GLib
            GLib.idle_add(lambda: (self._do_open(), False)[1])
        except Exception:
            self._do_open()

    def _do_open(self):
        try:
            if hasattr(self.app, "toggle_window"):
                self.app.toggle_window()
            elif hasattr(self.app, "window") and self.app.window:
                self.app.window.toggle_visible()
        except Exception as e:
            print(f"open failed: {e}")

    def _on_settings(self):
        try:
            from gi.repository import GLib
            GLib.idle_add(lambda: (self._do_settings(), False)[1])
        except Exception:
            self._do_settings()

    def _do_settings(self):
        try:
            # اگر پنجره اصلی هست، تنظیمات را همانجا باز کن
            if hasattr(self.app, "window") and self.app.window:
                # present first to ensure parent
                try:
                    self.app.window.present()
                except Exception:
                    pass
                from .ui.settings import show_settings
                show_settings(self.app.window, self.app.history)
            else:
                # standalone settings
                from .ui.settings import show_settings
                from .history import HistoryManager
                show_settings(None, HistoryManager())
        except Exception as e:
            print(f"settings failed: {e}")

    def _on_clear(self):
        try:
            self.app.history.clear(keep_pinned=True)
            print("✓ تاریخچه پاک شد (سنجاق‌ها ماندند)")
        except Exception as e:
            print(f"clear failed: {e}")

    def _on_quit(self):
        try:
            self.app.quit()
        except Exception:
            sys.exit(0)
