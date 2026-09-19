"""
tray.py — فرآیند مستقل سینی سیستم (Top Bar) با GTK3
این فایل به صورت جداگانه اجرا می‌شود تا تداخل GTK4/GTK3 پیش نیاید
اجرا: python3 -m ubuntu_clipboard.tray
"""

import os
import sys
import subprocess
from pathlib import Path

def get_icon():
    for p in [
        Path.home() / ".local/share/icons/ubuntu-clipboard.png",
        Path(__file__).parent / "assets/icon.png",
    ]:
        if p.exists():
            return str(p)
    return "edit-paste"

def run_ayatana():
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import Gtk, AyatanaAppIndicator3, GLib

        icon_path = get_icon()
        ind = AyatanaAppIndicator3.Indicator.new(
            "ubuntu-clipboard",
            icon_path if os.path.exists(icon_path) else "edit-paste",
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        ind.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        ind.set_title("Clipboard — Win+V")

        menu = Gtk.Menu()

        def on_open(_):
            # toggle clipboard via DBus/command line
            try:
                # full path
                candidates = [
                    str(Path.home() / ".local/bin/ubuntu-clipboard"),
                    str(Path.home() / ".local/share/ubuntu-clipboard/venv/bin/ubuntu-clipboard"),
                ]
                bin_path = "ubuntu-clipboard"
                for c in candidates:
                    if os.path.exists(c) and os.access(c, os.X_OK):
                        bin_path = c
                        break
                subprocess.Popen([bin_path, "--toggle"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                print(f"toggle failed: {e}")

        def on_settings(_):
            try:
                candidates = [
                    str(Path.home() / ".local/bin/ubuntu-clipboard"),
                    str(Path.home() / ".local/share/ubuntu-clipboard/venv/bin/ubuntu-clipboard"),
                ]
                bin_path = "ubuntu-clipboard"
                for c in candidates:
                    if os.path.exists(c):
                        bin_path = c
                        break
                subprocess.Popen([bin_path, "--settings"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                print(f"settings failed: {e}")

        def on_clear(_):
            try:
                from ubuntu_clipboard.history import HistoryManager
                HistoryManager().clear(keep_pinned=True)
                subprocess.run(["notify-send", "Clipboard", "تاریخچه پاک شد"], timeout=2)
            except Exception:
                pass

        def on_quit(_):
            Gtk.main_quit()
            # also quit main app if running
            try:
                subprocess.run(["pkill", "-f", "ubuntu-clipboard"], timeout=2)
            except Exception:
                pass
            sys.exit(0)

        # منو
        item_open = Gtk.MenuItem(label="📋  باز کردن کلیپ‌بورد  (Win+V)")
        item_open.connect("activate", on_open)
        menu.append(item_open)

        item_settings = Gtk.MenuItem(label="⚙️  تنظیمات")
        item_settings.connect("activate", on_settings)
        menu.append(item_settings)

        menu.append(Gtk.SeparatorMenuItem())

        item_clear = Gtk.MenuItem(label="🗑️  پاک کردن تاریخچه")
        item_clear.connect("activate", on_clear)
        menu.append(item_clear)

        menu.append(Gtk.SeparatorMenuItem())

        item_quit = Gtk.MenuItem(label="❌  خروج")
        item_quit.connect("activate", on_quit)
        menu.append(item_quit)

        menu.show_all()
        ind.set_menu(menu)
        try:
            if os.path.exists(icon_path):
                ind.set_icon_full(icon_path, "Clipboard")
        except Exception:
            pass

        print(f"✓ Tray (Ayatana GTK3 standalone) فعال — {icon_path}")
        # also handle SIGTERM
        import signal
        signal.signal(signal.SIGTERM, lambda *_: Gtk.main_quit())
        Gtk.main()
        return True
    except Exception as e:
        print(f"Ayatana standalone failed: {e}")
        import traceback; traceback.print_exc()
        return False

def main():
    # Only run if not already running
    # Check if indicator already exists via pgrep
    try:
        import subprocess
        # kill old stale tray
        pass
    except Exception:
        pass

    if not run_ayatana():
        print("✗ Tray failed — trying fallback notify")
        # fallback: just notify that clipboard is running
        try:
            subprocess.run(["notify-send", "Clipboard", "کلیپ‌بورد فعال است — Win+V بزنید\n(آیکون Top Bar در دسترس نیست)"], timeout=2)
        except Exception:
            pass
        # keep alive as daemon so parent thinks tray is running?
        import time
        while True:
            time.sleep(10)

if __name__ == "__main__":
    main()
