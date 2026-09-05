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
            except Exception:
                pass

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
            # toggle flag => toggle window, otherwise activate
            if opts.contains("toggle") or "--toggle" in sys.argv:
                if self.window and self._first_activated:
                    self.toggle_window()
                else:
                    self.do_activate()
                    # if window just created and toggle requested, show it (hidden->show)
                    if self.window and not self.window.is_visible():
                        self.window.present()
                return 0
            if opts.contains("hidden"):
                # ensure hidden
                self.do_activate()
                if self.window and self.window.is_visible():
                    self.window.set_visible(False)
                return 0
            self.do_activate()
            return 0

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
    parser.add_argument("-h","--help", action="store_true")
    args, _ = parser.parse_known_args()

    if args.help:
        print("Ubuntu Clipboard — Win+V\n")
        print("  ubuntu-clipboard              اجرای عادی (پنجره باز می‌شود)")
        print("  ubuntu-clipboard --hidden     اجرای مخفی در پس‌زمینه (برای Autostart)")
        print("  ubuntu-clipboard --toggle     تغییر نمایش پنجره (برای میانبر Win+V)")
        print("  ubuntu-clipboard-daemon       فقط دیمن بدون UI")
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

if __name__ == "__main__":
    main()
