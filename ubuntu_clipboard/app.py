"""
app.py — ورودی اصلی برنامه — نسخه پایدار بدون چشمک
- Win+V فقط یک پنجره مستقل می‌سازد (NON_UNIQUE) — بدون DBus تک‌نسخه
- دیمن جدا: ubuntu-clipboard-daemon یا ubuntu-clipboard --daemon
- toggle با lock file ساده
"""

from __future__ import annotations
import sys
import os
import signal
import subprocess
from pathlib import Path
import argparse

from .history import HistoryManager
from .daemon import ClipboardDaemon
from .config import get_config
try:
    from .log import log, log_window, log_toggle, log_error
    _HAS_LOG = True
except Exception:
    _HAS_LOG = False
    def log(m,l="INFO"): print(m)
    def log_window(a,b=""): print(f"WINDOW {a} {b}")
    def log_toggle(a,b=""): print(f"TOGGLE {a} {b}")
    def log_error(m): print(f"ERROR {m}")

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
        _HAS_ADW = False
    _HAS_GTK = True
except Exception:
    _HAS_GTK = False
    _HAS_ADW = False

LOCK_DIR = Path.home() / ".cache" / "ubuntu-clipboard"
LOCK_FILE = LOCK_DIR / "window.pid"

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

def _handle_toggle_lock() -> bool:
    """
    اگر پنجره قبلی باز است، آن را ببند و True برگردان (یعنی toggle off)
    در غیر این صورت False (باید پنجره جدید باز شود)
    debounce 400ms برای جلوگیری از چشمک با نگه‌داشتن کلید
    """ 
    if _HAS_LOG:
        log(f"_handle_toggle_lock called argv={sys.argv} pid={os.getpid()}", "TOGGLE")
    # debounce: اگر همین الان toggle کردیم، نادیده بگیر
    try:
        debounce_file = LOCK_DIR / "toggle.debounce"
        import time
        if debounce_file.exists():
            try:
                last = float(debounce_file.read_text().strip())
                if time.time() - last < 0.6:
                    return True  # نادیده بگیر، فرض کن بسته شد
            except Exception:
                pass
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        if LOCK_FILE.exists():
            try:
                pid = int(LOCK_FILE.read_text().strip())
                # آیا پروسه زنده است؟
                os.kill(pid, 0)
                # زنده است — ببندش
                try:
                    os.kill(pid, signal.SIGTERM)
                    # wait a bit
                    import time
                    time.sleep(0.15)
                    try:
                        os.kill(pid, 0)
                        # still alive -> kill -9
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
                except OSError:
                    pass
                try:
                    LOCK_FILE.unlink()
                except Exception:
                    pass
                try:
                    (LOCK_DIR / "toggle.debounce").write_text(str(time.time()))
                except Exception:
                    pass
                return True
            except (ValueError, OSError):
                # lock قدیمی یا پروسه مرده — پاک کن
                try:
                    LOCK_FILE.unlink()
                except Exception:
                    pass
        return False
    except Exception:
        return False

def _write_lock():
    if _HAS_LOG:
        log_window("WRITE_LOCK", f"pid={os.getpid()} file={LOCK_FILE}")
    try:
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        LOCK_FILE.write_text(str(os.getpid()))
    except Exception:
        pass

def _clear_lock(*_):
    if _HAS_LOG:
        try:
            log_window("CLEAR_LOCK", f"pid={os.getpid()} exists={LOCK_FILE.exists()}")
        except Exception: pass
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text().strip() == str(os.getpid()):
            if _HAS_LOG: log_window("UNLINK", str(LOCK_FILE))
            LOCK_FILE.unlink()
    except Exception:
        pass

# ─── GTK App — هر Win+V یک پروسه مستقل (NON_UNIQUE) ───
if _HAS_GTK:
    class ClipboardApp(Adw.Application if _HAS_ADW else Gtk.Application):
        def __init__(self, show_settings=False):
            # NON_UNIQUE = هر اجرا مستقل، بدون DBus single-instance — هیچ NoReply و چشمکی
            super().__init__(
                application_id="com.ubuntu.clipboard.window",
                flags=Gio.ApplicationFlags.NON_UNIQUE
            )
            self.history = HistoryManager()
            self.show_settings_on_start = show_settings
            self.window = None
            # tray is disabled by default now; only if --with-tray
            self.tray = None

        def do_activate(self):
            if _HAS_LOG: log_window("DO_ACTIVATE", f"pid={os.getpid()} window_exists={self.window is not None}")
            # این فقط برای اولین activate همین پروسه صدا زده می‌شود
            if self.window is None:
                from .ui.window import ClipboardWindow
                self.window = ClipboardWindow(self, self.history)
                # برای جلوگیری از Unknown در داک
                try:
                    self.window.set_icon_name("ubuntu-clipboard")
                except Exception:
                    pass
            if _HAS_LOG: log_window("PRESENT", f"pid={os.getpid()}")
            self.window.present()
            # اگر --settings خواسته شده، بعد از present دیالوگ را باز کن
            if self.show_settings_on_start:
                GLib.timeout_add(350, lambda: (self._open_settings(), False)[1])

        def do_startup(self):
            if _HAS_ADW:
                Adw.Application.do_startup(self)
            else:
                Gtk.Application.do_startup(self)
            # seed demo
            try:
                self.history.seed_demo_if_empty()
            except Exception:
                pass
            # tray فقط اگر خواسته شده
            wants_tray = "--with-tray" in sys.argv or get_config().enable_tray
            if wants_tray and "--no-tray" not in sys.argv:
                try:
                    from .indicator import TrayIndicator
                    self.tray = TrayIndicator(self)
                    GLib.timeout_add(500, lambda: (self.tray.setup(), False)[1])
                except Exception as e:
                    print(f"tray setup failed: {e}")

        def do_shutdown(self):
            _clear_lock()
            try:
                if hasattr(self, "daemon"):
                    self.daemon.stop()
            except Exception:
                pass
            if _HAS_ADW:
                Adw.Application.do_shutdown(self)
            else:
                Gtk.Application.do_shutdown(self)

        def _open_settings(self):
            try:
                from .ui.settings import show_settings
                show_settings(self.window, self.history)
            except Exception as e:
                print(f"settings open failed: {e}")

    def main_gtk(show_settings=False):
        _print_banner()
        cfg = get_config()
        print(f"  Theme: {cfg.theme}  Max: {cfg.max_items}  DB: {HistoryManager().db_path}")
        print(f"  Shortcut: Super+V (Win+V)\n")
        if _HAS_ADW:
            Adw.init()
        # toggle logic: اگر پنجره قبلی باز است، ببند و خارج شو
        # فقط برای حالت عادی (نه --settings)
        if not show_settings and "--daemon" not in sys.argv and "--hidden" not in sys.argv:
            if _HAS_LOG: log_toggle("CHECK", f"argv={sys.argv}")
            if _handle_toggle_lock():
                if _HAS_LOG: log_toggle("KILLED_PREV", "toggle off, exiting")
                print("  (پنجره قبلی بسته شد — toggle)")
                return 0
            if _HAS_LOG: log_toggle("NO_PREV", "no previous window, will create new")
        _write_lock()
        if _HAS_LOG: log_window("CREATING", f"pid={os.getpid()} show_settings={show_settings}")
        # cleanup on exit
        import atexit
        atexit.register(_clear_lock)
        signal.signal(signal.SIGTERM, lambda *_: (_clear_lock(), sys.exit(0)))
        app = ClipboardApp(show_settings=show_settings)
        # also handle SIGINT
        def sig_handler(*_):
            _clear_lock()
            app.quit()
        signal.signal(signal.SIGINT, sig_handler)
        try:
            # Use clean argv to avoid GApplication Unknown option errors
            return app.run([sys.argv[0]])
        finally:
            _clear_lock()

# ─── TK fallback ───
def main_tk(show_settings=False):
    _print_banner()
    try:
        import tkinter
        has_tk = True
    except ImportError:
        has_tk = False

    from .ui.window import ClipboardWindow
    history = HistoryManager()
    try:
        history.seed_demo_if_empty()
    except Exception:
        pass
    daemon = ClipboardDaemon(history=history)
    # برای حالت پنجره، دیمن را اگر قبلاً daemon جدا اجرا نشده، اجرا کن
    # اما اگر daemon جدا در حال اجراست، نیازی نیست
    # بررسی: آیا daemon جدا در حال اجراست؟
    import subprocess
    try:
        r = subprocess.run(["pgrep", "-af", "ubuntu-clipboard"], capture_output=True, text=True, timeout=1)
        daemon_already = bool(r.stdout.strip())
    except Exception:
        daemon_already = False
    if not daemon_already:
        daemon.start(use_gtk=False)

    if show_settings:
        # settings standalone
        try:
            from .ui.settings import show_settings
            # need a root for dialog
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            show_settings(None, history)
            root.mainloop()
        except Exception as e:
            print(f"settings failed: {e}")
        return

    if not has_tk and not _HAS_GTK:
        print("  ✗ GUI toolkit موجود نیست")
        print("  نصب: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-tk -y")
        return

    # toggle lock for Tk as well
    if not show_settings:
        if _handle_toggle_lock():
            print("  (پنجره قبلی بسته شد)")
            return
    _write_lock()
    import atexit
    atexit.register(_clear_lock)

    if not _HAS_GTK:
        print("  حالت Tkinter fallback")
    print(f"  DB: {history.db_path}\n")

    # Tk window is already handled via ClipboardWindow fallback
    w = ClipboardWindow(history=history)
    try:
        w._ensure()
        if getattr(w, "_root", None) is not None:
            w._refresh()
            # handle close to clear lock
            orig_hide = w.hide
            def hide_and_clear():
                _clear_lock()
                orig_hide()
                try:
                    w._root.quit()
                except Exception:
                    pass
            w.hide = hide_and_clear
            w._root.protocol("WM_DELETE_WINDOW", hide_and_clear)
            w._root.mainloop()
        else:
            print("  GUI در دسترس نیست")
            import time
            while True:
                time.sleep(1)
    except Exception as e:
        print(f"خطا: {e}")
    finally:
        _clear_lock()
        daemon.stop()

def main():
    parser = argparse.ArgumentParser(description="Ubuntu Clipboard — Win+V", add_help=False)
    parser.add_argument("--hidden", action="store_true", help="شروع مخفی (فقط دیمن)")
    parser.add_argument("--daemon", action="store_true", help="فقط دیمن بدون UI")
    parser.add_argument("--toggle", action="store_true", help="نمایش/بستن پنجره (Win+V)")
    parser.add_argument("--show", action="store_true", help="نمایش پنجره")
    parser.add_argument("--settings", action="store_true", help="باز کردن تنظیمات")
    parser.add_argument("--status", action="store_true", help="نمایش وضعیت")
    parser.add_argument("--debug", action="store_true", help="حالت دیباگ")
    parser.add_argument("--no-tray", action="store_true", help="بدون آیکون تسک‌بار")
    parser.add_argument("--with-tray", action="store_true", help="با آیکون تسک‌بار")
    parser.add_argument("--log", action="store_true", help="نمایش لاگ کامل")
    parser.add_argument("--clear-log", action="store_true", help="پاک کردن لاگ")
    parser.add_argument("-h", "--help", action="store_true")
    args, _ = parser.parse_known_args()

    if args.clear_log:
        try:
            from .log import clear_logs
            clear_logs()
            print("✓ لاگ پاک شد")
        except Exception as e:
            print(f"clear log failed: {e}")
        return
    if args.log:
        try:
            from .log import tail_logs, CACHE_LOG, TMP_LOG
            print(f"=== CACHE LOG: {CACHE_LOG} ===")
            print(tail_logs(200))
            print(f"\n=== TMP LOG: {TMP_LOG} ===")
            try:
                print(open(TMP_LOG, encoding="utf-8", errors="ignore").read()[-4000:])
            except Exception as e:
                print(f"tmp log read failed: {e}")
        except Exception as e:
            print(f"log failed: {e}")
        return
    if args.status:
        _print_status()
        return

    if args.settings:
        if _HAS_GTK:
            sys.exit(main_gtk(show_settings=True))
        else:
            main_tk(show_settings=True)
        return

    if args.help:
        print("Ubuntu Clipboard — Win+V  v1.0.0\n")
        print("  ubuntu-clipboard              نمایش پنجره (Win+V)")
        print("  ubuntu-clipboard --toggle     نمایش/بستن (میانبر)")
        print("  ubuntu-clipboard --hidden     اجرای دیمن در پس‌زمینه (Autostart)")
        print("  ubuntu-clipboard --daemon     فقط دیمن")
        print("  ubuntu-clipboard --settings   تنظیمات")
        print("  ubuntu-clipboard --status     وضعیت")
        print("  ubuntu-clipboard --with-tray  با آیکون Top Bar (اختیاری)")
        print("")
        return

    # daemon modes — بدون پنجره
    if args.daemon or args.hidden:
        if _HAS_LOG: log("DAEMON mode --hidden/--daemon", "DAEMON")
        # --hidden اکنون یعنی فقط دیمن (بدون پنجره) برای جلوگیری از چشمک
        from .daemon import main as daemon_main
        # اگر --with-tray خواسته شده، دیمن + tray standalone
        if args.with_tray:
            # run daemon in thread and tray in main?
            # ساده: daemon را اجرا کن و tray را اسپاون کن
            try:
                import subprocess
                subprocess.Popen([sys.executable, "-m", "ubuntu_clipboard.tray"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            except Exception:
                pass
        # daemon blocking
        sys.argv = [sys.argv[0]]
        daemon_main()
        return

    # window modes — هر اجرا یک پروسه مستقل کوتاه‌مدت
    if _HAS_LOG: log(f"WINDOW MODE argv={sys.argv} has_gtk={_HAS_GTK}", "WINDOW")
    if _HAS_GTK:
        # برای --toggle هم همان show است — toggle با lock file هندل می‌شود
        sys.exit(main_gtk(show_settings=False))
    else:
        main_tk(show_settings=False)

def _print_status():
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
            print("    (خالی)")
    except Exception as e:
        print(f"  خطا: {e}")
    print(f"\n  Config: theme={cfg.theme} max={cfg.max_items} tray={cfg.enable_tray}")
    print(f"  Session: {os.environ.get('XDG_SESSION_TYPE','?')}  WAYLAND={os.environ.get('WAYLAND_DISPLAY','-')}  DISPLAY={os.environ.get('DISPLAY','-')}")
    import shutil
    for cmd in ["wl-paste","wl-copy","xclip","xsel","xdotool","wtype"]:
        print(f"    {cmd:12} {'✓' if shutil.which(cmd) else '✗'}")
    autostart = Path.home()/".config/autostart/ubuntu-clipboard.desktop"
    autostart_daemon = Path.home()/".config/autostart/ubuntu-clipboard-daemon.desktop"
    print(f"\n  Autostart window: {autostart} exists={autostart.exists()}")
    print(f"  Autostart daemon: {autostart_daemon} exists={autostart_daemon.exists()}")
    try:
        import subprocess
        out = subprocess.run(["gsettings","get","org.gnome.settings-daemon.plugins.media-keys","custom-keybindings"], capture_output=True, text=True, timeout=2)
        print(f"  Shortcut: {out.stdout.strip()[:200]}")
        out2 = subprocess.run(["gsettings","get","org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/ubuntu-clipboard/","command"], capture_output=True, text=True, timeout=2)
        if out2.stdout.strip():
            print(f"    command: {out2.stdout.strip()}")
    except Exception as e:
        print(f"  gsettings: {e}")
    try:
        import subprocess
        ps = subprocess.run(["pgrep","-af","ubuntu-clipboard"], capture_output=True, text=True, timeout=2)
        print(f"\n  Processes:\n    {ps.stdout.strip() or '(هیچ)'}")
    except Exception:
        pass
    lock = LOCK_FILE
    if lock.exists():
        print(f"  Window lock: {lock.read_text().strip()} (exists)")
    else:
        print(f"  Window lock: (none)")

def _open_settings_standalone():
    # handled in main() --settings
    pass

if __name__ == "__main__":
    main()
