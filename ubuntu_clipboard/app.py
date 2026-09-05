"""
app.py — ورودی اصلی برنامه
- اجرا به صورت اپ گرافیکی با دیمن داخلی
- Win+V را از طریق D-Bus / keybinding کنترل می‌کند
- fallback: اگر GTK نیست، Tkinter اجرا می‌شود
"""

from __future__ import annotations
import sys
import os
import signal
import threading
import argparse

from .history import HistoryManager
from .daemon import ClipboardDaemon
from .config import get_config

_HAS_GTK = False
try:
    import gi
    gi.require_version("Gtk","4.0")
    from gi.repository import Gtk, Gdk, GLib, Gio
    try:
        gi.require_version("Adw","1")
        from gi.repository import Adw
        _HAS_ADW = True
    except Exception:
        _HAS_ADW=False
    _HAS_GTK=True
except Exception:
    _HAS_GTK=False
    _HAS_ADW=False

def _print_banner():
    print(r"""
  _    _ _                 _           _____ _ _       _                         _
 | |  | | |               | |         / ____| (_)     | |                       | |
 | |  | | |__  _   _ _ __ | |_ _   _ | |    | |_ _ __ | |__   ___   __ _ _ __ __| |
 | |  | | '_ \| | | | '_ \| __| | | || |    | | | '_ \| '_ \ / _ \ / _` | '__/ _` |
 | |__| | |_) | |_| | | | | |_| |_| || |____| | | |_) | |_) | (_) | (_| | | | (_| |
  \____/|_.__/ \__,_|_| |_|\__|\__,_| \_____|_|_| .__/|_.__/ \___/ \__,_|_|  \__,_|
                                                | |
  Win+V  —  Windows 11-like Clipboard for Ubuntu |_|  v1.0.0
    """)

# ─── GTK App ───
if _HAS_GTK:
    class ClipboardApp(Adw.Application if _HAS_ADW else Gtk.Application):
        def __init__(self):
            super().__init__(
                application_id="com.ubuntu.clipboard",
                flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE
            )
            self.history = HistoryManager()
            self.daemon = ClipboardDaemon(history=self.history)
            self.window = None
            self._first_activated = False
            self._setup_actions()
            self.add_main_option("hidden", ord("h"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "شروع مخفی", None)
            self.add_main_option("toggle", ord("t"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "تغییر نمایش پنجره", None)

        def _setup_actions(self):
            act = Gio.SimpleAction.new("toggle", None)
            act.connect("activate", lambda *_: self.toggle_window())
            self.add_action(act)
            quit_act = Gio.SimpleAction.new("quit", None)
            quit_act.connect("activate", lambda *_: self.quit())
            self.add_action(quit_act)
            self.set_accels_for_action("app.toggle", ["<Super>v"])
            self.set_accels_for_action("app.quit", ["<Control>q"])

        def do_startup(self):
            if _HAS_ADW:
                Adw.Application.do_startup(self)
            else:
                Gtk.Application.do_startup(self)
            # seed demo if first run
            try:
                self.history.seed_demo_if_empty()
            except Exception:
                pass
            # daemon starts once at startup
            try:
                self.daemon.start()
            except Exception as e:
                print(f"daemon start failed: {e}")
            # ── tray icon in top bar ──
            try:
                from .indicator import TrayIndicator
                self.tray = TrayIndicator(self)
                # delay a bit to ensure GTK is ready
                from gi.repository import GLib
                GLib.timeout_add(400, lambda: (self.tray.setup(), False)[1])
            except Exception as e:
                print(f"tray setup failed: {e}")
                self.tray = None

        def do_activate(self):
            # Called on first launch and when second instance calls activate
            if not self.window:
                from .ui.window import ClipboardWindow
                self.window = ClipboardWindow(self, self.history)
            # first run: show unless --hidden was passed initially
            if not self._first_activated:
                self._first_activated = True
                # if launched hidden, don't show
                if "--hidden" not in sys.argv and "-h" not in sys.argv and "--daemon" not in sys.argv:
                    self.window.present()
                self.hold()
            else:
                # subsequent activate without args => toggle
                self.toggle_window()

        def do_command_line(self, cmdline):
            opts = cmdline.get_options_dict()
            # --show / --toggle => show/toggle
            if opts.contains("toggle") or "--toggle" in sys.argv or "--show" in sys.argv or opts.contains("show"):
                if self.window and self._first_activated:
                    # --show always show, --toggle toggles
                    if "--show" in sys.argv or opts.contains("show"):
                        if not self.window.is_visible():
                            self.window.present()
                    else:
                        self.toggle_window()
                else:
                    self.do_activate()
                    if self.window and not self.window.is_visible():
                        self.window.present()
                return 0
            if opts.contains("hidden"):
                self.do_activate()
                if self.window and self.window.is_visible():
                    self.window.set_visible(False)
                return 0
            # settings via command line (gapplication)
            if "--settings" in sys.argv:
                self.do_activate()
                if self.window:
                    self.window.present()
                    from gi.repository import GLib
                    GLib.timeout_add(300, lambda: (self._open_settings_from_app(), False)[1])
                return 0
            self.do_activate()
            return 0

        def _open_settings_from_app(self):
            try:
                from .ui.settings import show_settings
                show_settings(self.window, self.history)
            except Exception as e:
                print(f"settings open failed: {e}")

        def toggle_window(self):
            if not self.window:
                self.do_activate()
                return
            try:
                GLib.idle_add(lambda: (self.window.toggle_visible(), False)[1])
            except Exception:
                self.window.toggle_visible()

    def main_gtk(args=None):
        _print_banner()
        cfg = get_config()
        print(f"  Theme: {cfg.theme}  Max: {cfg.max_items}  DB: {HistoryManager().db_path}")
        print(f"  Shortcut: Super+V (Win+V)  —  اجرای دیمن و پنجره\n")
        if _HAS_ADW:
            Adw.init()
        app = ClipboardApp()
        def sig_handler(*_):
            app.quit()
        signal.signal(signal.SIGINT, sig_handler)
        signal.signal(signal.SIGTERM, sig_handler)
        return app.run(sys.argv)

# ─── TK fallback ───
def main_tk():
    _print_banner()
    from .ui.window import ClipboardWindow
    history = HistoryManager()
    try:
        history.seed_demo_if_empty()
    except Exception:
        pass
    daemon = ClipboardDaemon(history=history)
    daemon.start(use_gtk=False)
    print("  حالت Tkinter fallback — GTK4 در دسترس نیست.")
    print("  برای تجربه کامل: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1")
    print(f"  DB: {history.db_path}\n  پنجره را ببندید برای خروج.\n")
    w = ClipboardWindow(history=history)
    w._ensure()
    w._refresh()
    w._root.mainloop()
    daemon.stop()

def main():
    parser = argparse.ArgumentParser(description="Ubuntu Clipboard — Win+V", add_help=False)
    parser.add_argument("--hidden", action="store_true", help="شروع مخفی (فقط دیمن)")
    parser.add_argument("--daemon", action="store_true", help="فقط دیمن بدون UI")
    parser.add_argument("--toggle", action="store_true", help="toggle window via D-Bus (for shortcut)")
    parser.add_argument("--show", action="store_true", help="show window")
    parser.add_argument("--settings", action="store_true", help="باز کردن تنظیمات")
    parser.add_argument("--status", action="store_true", help="نمایش وضعیت و دیباگ")
    parser.add_argument("--debug", action="store_true", help="حالت دیباگ با لاگ")
    parser.add_argument("-h","--help", action="store_true")
    args, _ = parser.parse_known_args()

    if args.status:
        _print_status()
        return

    if args.settings:
        _open_settings_standalone()
        return

    if args.help:
        print("Ubuntu Clipboard — Win+V  v1.0.0\n")
        print("  ubuntu-clipboard              اجرای عادی (پنجره باز می‌شود)")
        print("  ubuntu-clipboard --hidden     اجرای مخفی در پس‌زمینه (برای Autostart + آیکون تسک‌بار)")
        print("  ubuntu-clipboard --toggle     تغییر نمایش پنجره (برای میانبر Win+V)")
        print("  ubuntu-clipboard --settings   باز کردن تنظیمات")
        print("  ubuntu-clipboard --status     نمایش وضعیت، تعداد آیتم‌ها و لاگ")
        print("  ubuntu-clipboard --debug      اجرا با لاگ کامل در ترمینال")
        print("  ubuntu-clipboard-daemon       فقط دیمن بدون UI")
        print("")
        print("  نکات عیب‌یابی:")
        print("    • بعد از نصب یک بار لاگ‌اوت/لاگین کنید تا autostart فعال شود")
        print("    • آیکون کلیپ‌بورد در Top Bar (کنار ساعت/وای‌فای) را بزنید برای باز کردن")
        print("    • اگر Win+V کار نکرد: ./scripts/setup-shortcut.sh را دوباره بزنید")
        print("")
        return

    if args.daemon:
        from .daemon import main as daemon_main
        sys.argv = [sys.argv[0]]
        daemon_main()
        return

    # --toggle with GTK: rely on GApplication single-instance
    # If no GTK, just toggle Tk window
    if _HAS_GTK:
        # GApplication will handle single-instance; just run app
        sys.exit(main_gtk())
    else:
        # Tk fallback: if --toggle and no window, show
        main_tk()

def _print_status():
    """نمایش وضعیت برای عیب‌یابی — حتی بدون GUI کار می‌کند"""
    _print_banner()
    from pathlib import Path
    cfg = get_config()
    hm = HistoryManager()
    print("── وضعیت کلیپ‌بورد ──")
    print(f"  DB: {hm.db_path}  (exists={hm.db_path.exists()})")
    try:
        print(f"  تعداد آیتم‌ها: {hm.count()}  (سنجاق: {len(hm.list_pinned())})")
        items = hm.list(limit=3)
        for i, it in enumerate(items, 1):
            print(f"    {i}. [{it.type}] {it.preview[:60]}  — {it.time_ago} pinned={it.pinned}")
        if not items:
            print("    (خالی — یک چیزی کپی کنید یا hm.seed_demo_if_empty() بزنید)")
    except Exception as e:
        print(f"  خطا در خواندن DB: {e}")

    print(f"\n  Config: theme={cfg.theme} max={cfg.max_items} size={cfg.window_width}x{cfg.window_height}")
    print(f"  Session: {os.environ.get('XDG_SESSION_TYPE','?')}  WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY','-')}  DISPLAY={os.environ.get('DISPLAY','-')}")
    # clipboard tools
    import shutil
    for cmd in ["wl-paste","wl-copy","xclip","xsel","xdotool","wtype","ydotool"]:
        print(f"    {cmd:12} {'✓' if shutil.which(cmd) else '✗'}")
    # autostart
    autostart = Path.home()/".config/autostart/ubuntu-clipboard.desktop"
    print(f"\n  Autostart: {autostart}  exists={autostart.exists()}")
    # shortcut
    try:
        import subprocess
        out = subprocess.run(["gsettings","get","org.gnome.settings-daemon.plugins.media-keys","custom-keybindings"], capture_output=True, text=True, timeout=2)
        print(f"  Shortcut bindings: {out.stdout.strip()[:200]}")
        # try to read ours
        out2 = subprocess.run(["gsettings","get","org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/ubuntu-clipboard/","binding"], capture_output=True, text=True, timeout=2)
        if out2.stdout.strip():
            print(f"    ubuntu-clipboard binding: {out2.stdout.strip()}")
        out3 = subprocess.run(["gsettings","get","org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/ubuntu-clipboard/","command"], capture_output=True, text=True, timeout=2)
        if out3.stdout.strip():
            print(f"    command: {out3.stdout.strip()}")
    except Exception as e:
        print(f"  gsettings read failed: {e}")
    # running process
    try:
        import subprocess
        ps = subprocess.run(["pgrep","-a","ubuntu-clipboard"], capture_output=True, text=True, timeout=2)
        print(f"\n  Processes:\n    {ps.stdout.strip() or '(هیچ)'}")
    except Exception:
        pass
    # log
    log = Path("/tmp/ubuntu-clipboard.log")
    if log.exists():
        print(f"\n  Log tail ({log}):")
        try:
            print("  " + "\n  ".join(log.read_text(encoding='utf-8', errors='ignore').strip().splitlines()[-20:]))
        except Exception:
            pass
    print("\n  برای اجرای دستی با لاگ:  ubuntu-clipboard --debug")
    print("  برای تنظیمات:            ubuntu-clipboard --settings")
    print("  برای باز کردن:            ubuntu-clipboard --toggle  یا کلیک روی آیکون Top Bar")

def _open_settings_standalone():
    """باز کردن صفحه تنظیمات به صورت مستقل — بدون نیاز به پنجره اصلی"""
    _print_banner()
    # try GTK first
    if _HAS_GTK:
        try:
            import gi
            gi.require_version("Gtk","4.0")
            from gi.repository import Gtk, Gio, GLib
            # need a minimal app to host dialog
            app_id = "com.ubuntu.clipboard.settings"
            app = Gtk.Application(application_id=app_id, flags=Gio.ApplicationFlags.FLAGS_NONE)
            def on_activate(a):
                # dummy window as parent
                win = Gtk.ApplicationWindow(application=a)
                win.set_title("تنظیمات کلیپ‌بورد")
                win.set_default_size(460, 460)
                # we use Adw if available for nicer header?
                # Instead just show settings dialog
                win.present()
                from .history import HistoryManager
                from .ui.settings import show_settings
                hm = HistoryManager()
                # show settings dialog with win as parent — do idle to ensure win is mapped
                GLib.idle_add(lambda: (show_settings(win, hm), False)[1])
            app.connect("activate", on_activate)
            app.run(None)
            return
        except Exception as e:
            print(f"GTK settings failed: {e}, fallback to Tk")
    # Tk fallback
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        from .history import HistoryManager
        from .ui.settings import show_settings
        hm = HistoryManager()
        root.deiconify()
        root.title("تنظیمات کلیپ‌بورد")
        root.geometry("460x420")
        show_settings(None, hm)
        root.mainloop()
    except Exception as e:
        print(f"settings fallback failed: {e}")
        cfg = get_config()
        from .config import CONFIG_PATH
        print(f"Edit manually: {CONFIG_PATH}")
        print(cfg)

if __name__ == "__main__":
    main()
