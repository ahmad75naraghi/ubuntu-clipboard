"""Command line interface.

Everything that does not need a display (``--status``, ``--list``, ``--clear``,
``--install-shortcut``, …) is handled locally, so the CLI stays usable over SSH
and in headless CI. Only the window/daemon commands are forwarded to the GTK
application — which also means ``--toggle`` reaches the *running* instance and
returns in milliseconds.
"""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

from . import APP_ID, APP_NAME, PROJECT_URL, __version__
from .config import Config, config_path, database_path, get_config
from .i18n import set_language, t
from .log import clear as clear_logs
from .log import log_path, setup_logging, tail
from .shortcut import KEY_PATH
from .storage import HistoryStore

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILURE = 1

#: Commands that need a running GTK application.
APP_COMMANDS = ("toggle", "show", "hide", "settings", "quit", "background")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ubuntu-clipboard",
        description=f"{APP_NAME} — Windows 11 style clipboard history for Ubuntu (Win+V)",
        epilog=f"Report issues at {PROJECT_URL}",
    )
    parser.add_argument("--version", action="store_true", help="show the version and exit")

    window = parser.add_argument_group("window")
    window.add_argument("--toggle", action="store_true", help="show or hide the clipboard window")
    window.add_argument("--show", action="store_true", help="show the clipboard window")
    window.add_argument("--hide", action="store_true", help="hide the clipboard window")
    window.add_argument("--settings", action="store_true", help="open the preferences window")
    window.add_argument("--quit", action="store_true", help="stop the running instance")
    window.add_argument(
        "--background",
        "--daemon",
        "--hidden",
        dest="background",
        action="store_true",
        help="run in the background without a window (used at login)",
    )

    data = parser.add_argument_group("history")
    data.add_argument("--list", nargs="?", const=20, type=int, metavar="N", help="print the last N items")
    data.add_argument("--clear", action="store_true", help="delete the history")
    data.add_argument(
        "--all",
        dest="clear_all",
        action="store_true",
        help="with --clear: also delete pinned items",
    )

    setup = parser.add_argument_group("integration")
    setup.add_argument(
        "--install", action="store_true", help="install desktop entry, icon, autostart, shortcut"
    )
    setup.add_argument("--uninstall", action="store_true", help="remove the user level integration")
    setup.add_argument(
        "--purge",
        action="store_true",
        help="with --uninstall: also delete the history database and the configuration",
    )
    setup.add_argument("--install-shortcut", action="store_true", help="register the Win+V keybinding")
    setup.add_argument("--remove-shortcut", action="store_true", help="remove the Win+V keybinding")
    setup.add_argument(
        "--take-binding",
        action="store_true",
        help="with --install/--install-shortcut: also drop other shortcuts using the same key",
    )
    setup.add_argument(
        "--binding",
        metavar="KEYS",
        help="with --install/--install-shortcut: use another key, e.g. '<Super><Alt>v'",
    )
    setup.add_argument(
        "--setup-paste",
        dest="setup_paste",
        action="store_true",
        help="set up automatic pasting (installs ydotool and starts its daemon)",
    )
    setup.add_argument(
        "--yes",
        action="store_true",
        help="with --setup-paste: do not ask before running the commands",
    )

    diagnostics = parser.add_argument_group("diagnostics")
    diagnostics.add_argument("--status", action="store_true", help="print environment and installation state")
    diagnostics.add_argument(
        "--logs", nargs="?", const=200, type=int, metavar="N", help="print the last N log lines"
    )
    diagnostics.add_argument("--clear-logs", action="store_true", help="delete the log files")
    diagnostics.add_argument(
        "--collect-logs", action="store_true", help="write a diagnostic report and print its path"
    )
    diagnostics.add_argument(
        "--diagnose",
        action="store_true",
        help="check why Win+V does not open the window and print what to do",
    )
    diagnostics.add_argument(
        "--test-paste",
        dest="test_paste",
        action="store_true",
        help="press Ctrl+V in a few seconds so you can see whether pasting works",
    )
    diagnostics.add_argument(
        "--item",
        type=int,
        metavar="N",
        help="with --test-paste: put the N-th history item (as printed by --list) on the clipboard first",
    )
    diagnostics.add_argument(
        "--delay",
        type=float,
        default=5.0,
        metavar="SECONDS",
        help="with --test-paste: how long to wait before pressing Ctrl+V (default 5)",
    )
    diagnostics.add_argument("--debug", action="store_true", help="verbose logging on stderr")

    parser.add_argument(
        "--with-tray",
        "--no-tray",
        dest="tray",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def _command_from_args(args: argparse.Namespace) -> str:
    for name in APP_COMMANDS:
        if getattr(args, name, False):
            return name
    return "toggle"


#: Action groups: at most one may be selected per invocation.
ACTION_GROUPS: dict[str, tuple[str, ...]] = {
    "window": APP_COMMANDS,
    "history": ("list", "clear"),
    "integration": ("install", "uninstall", "install_shortcut", "remove_shortcut", "setup_paste"),
    "diagnostics": ("status", "logs", "clear_logs", "collect_logs", "diagnose", "test_paste"),
}


def _selected_groups(args: argparse.Namespace) -> list[str]:
    selected = []
    for name, attributes in ACTION_GROUPS.items():
        if any(getattr(args, attribute, None) not in (False, None) for attribute in attributes):
            selected.append(name)
    return selected


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Reject contradictory combinations before anything is executed."""
    groups = _selected_groups(args)
    if len(groups) > 1:
        parser.error("options from different groups were given: " + ", ".join(groups))
    if getattr(args, "clear_all", False) and not getattr(args, "clear", False):
        parser.error("--all only makes sense together with --clear")
    if getattr(args, "purge", False) and not getattr(args, "uninstall", False):
        parser.error("--purge only makes sense together with --uninstall")
    if getattr(args, "take_binding", False) and not (
        getattr(args, "install", False) or getattr(args, "install_shortcut", False)
    ):
        parser.error("--take-binding only makes sense with --install or --install-shortcut")
    if getattr(args, "binding", None) and not (
        getattr(args, "install", False) or getattr(args, "install_shortcut", False)
    ):
        parser.error("--binding only makes sense with --install or --install-shortcut")
    if getattr(args, "yes", False) and not getattr(args, "setup_paste", False):
        parser.error("--yes only makes sense with --setup-paste")
    if getattr(args, "delay", 5.0) != 5.0 and not getattr(args, "test_paste", False):
        parser.error("--delay only makes sense with --test-paste")
    if getattr(args, "item", None) is not None and not getattr(args, "test_paste", False):
        parser.error("--item only makes sense with --test-paste")
    others = ("install", "uninstall", "install_shortcut", "remove_shortcut")
    if getattr(args, "setup_paste", False) and any(getattr(args, name, False) for name in others):
        parser.error("--setup-paste cannot be combined with the other integration options")


# ── local commands ─────────────────────────────────────────────────────────
def cmd_list(store: HistoryStore, limit: int) -> int:
    items = store.list(limit=max(1, limit))
    if not items:
        print(t("empty.title"))
        return EXIT_OK
    print(f"{t('cli.items_header')} ({len(items)})")
    for index, item in enumerate(items, start=1):
        pinned = "📌 " if item.pinned else "   "
        print(f"{index:>3}. {pinned}[{item.type.value:<5}] {item.preview[:100]}  ·  {item.relative_time()}")
    return EXIT_OK


def is_running() -> bool | None:
    """Whether another instance owns the application's D-Bus name.

    ``None`` means "cannot tell" (no Gio or no session bus).
    """
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
    except (ImportError, ValueError):
        return None
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            GLib.Variant("(s)", (APP_ID,)),
            GLib.VariantType("(b)"),
            Gio.DBusCallFlags.NONE,
            2000,
            None,
        )
    except Exception:  # pragma: no cover - no session bus
        return None
    return bool(reply.unpack()[0])


def cmd_status(store: HistoryStore) -> int:
    config = get_config()
    from .install import environment_report
    from .paste import paste_keys_for_status
    from .shortcut import gsettings_available
    from .shortcut import status as shortcut_status

    stats = store.stats()
    report = environment_report()
    print(f"{APP_NAME} {__version__}")
    print(f"  python           {report['python']} ({report['executable']})")
    print(f"  session          {report['session']}")
    print(f"  database         {stats['database']}")
    print(f"  items            {stats['items']} (pinned: {stats['pinned']}, images: {stats['images']})")
    print(f"  database size    {stats['database_bytes'] / 1024:.1f} KiB")
    print(f"  configuration    {config_path()}")
    print(f"  theme / language {config.theme} / {config.language}")
    print(
        f"  desktop entry    {report['desktop_entry']} ({'ok' if report['desktop_entry_installed'] else 'missing'})"
    )
    print(f"  icon             {'installed' if report['icon_installed'] else 'missing'}")
    print(f"  autostart        {'enabled' if report['autostart_enabled'] else 'disabled'}")
    instance_state = {True: "running", False: "stopped", None: "unknown"}[is_running()]
    print(f"  instance         {instance_state}")
    print(f"  auto paste       {paste_keys_for_status()}")
    print(f"  window backend   {display_backend_label()}")
    tools = report["tools"]
    print(
        "  tools            "
        + ", ".join(f"{name}={'yes' if present else 'no'}" for name, present in tools.items())
    )
    print(
        f"  clipboard tools  {'readable' if report['can_read_text'] else 'missing'} / "
        f"{'writable' if report['can_write_text'] else 'missing'}"
    )
    if gsettings_available():
        shortcut_state = shortcut_status()
        binding = shortcut_state.get("binding") or "-"
        command = shortcut_state.get("command") or "-"
        if shortcut_state.get("ours_listed"):
            print(f"  shortcut         {binding} -> {command}")
        else:
            print("  shortcut         not registered — ubuntu-clipboard --install-shortcut")
    else:
        print("  shortcut         gsettings unavailable")
    return EXIT_OK


def cmd_collect_logs(store: HistoryStore) -> int:
    from .config import cache_dir

    target = cache_dir() / "diagnostics.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    import contextlib
    import io

    sections = [f"# {APP_NAME} {__version__} diagnostics", ""]
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        cmd_status(store)
    sections.append(buffer.getvalue())
    sections.append("\n## log tail\n")
    sections.append(tail(300))
    sections.append("\n## environment\n")
    for key in sorted(os.environ):
        if key.startswith(("XDG_", "WAYLAND_", "DISPLAY", "DBUS_", "LANG", "LC_")):
            sections.append(f"{key}={os.environ[key]}")
    target.write_text("\n".join(sections), encoding="utf-8")
    print(target)
    return EXIT_OK


def cmd_install(args: argparse.Namespace, config: Config) -> int:
    from .install import install_all

    report = install_all(
        enable_startup=config.auto_start,
        shortcut_binding=config.shortcut,
        install_keybinding=True,
        take_binding=args.take_binding,
    )
    for action in report.actions:
        print(f"  ✓ {action}")
    for warning in report.warnings:
        print(f"  ! {warning}", file=sys.stderr)
    return EXIT_OK


def cmd_uninstall(args: argparse.Namespace) -> int:
    from .install import uninstall_all

    report = uninstall_all(remove_config=args.purge, remove_history=args.purge)
    for action in report.actions:
        print(f"  ✓ {action}")
    for warning in report.warnings:
        print(f"  ! {warning}", file=sys.stderr)
    return EXIT_OK


def cmds_shortcut(install: bool, take_binding: bool = False, binding: str | None = None) -> int:
    from .shortcut import DEFAULT_BINDING, ShortcutError
    from .shortcut import install as shortcut_install
    from .shortcut import uninstall as shortcut_uninstall

    try:
        report = (
            shortcut_install(binding=binding or DEFAULT_BINDING, take_binding=take_binding)
            if install
            else shortcut_uninstall()
        )
    except ShortcutError as exc:
        print(f"  ! {exc}", file=sys.stderr)
        return EXIT_FAILURE
    for message in report.messages:
        warning = not report.ok or message.startswith("warning:")
        stream = sys.stderr if warning else sys.stdout
        print(f"  {'!' if warning else '✓'} {message}", file=stream)
    if not report.ok:
        print(
            "  ! gsettings refused the keybinding — set it up by hand instead:"
            "\n    Settings → Keyboard → Custom Shortcuts"
            f"\n    name: Clipboard, command: {report.command}, shortcut: {report.binding}",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    return EXIT_OK


def _process_running(pattern: str) -> bool | None:
    """Whether a process matching ``pattern`` is running (``None`` = cannot tell)."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", pattern], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
        )
    except OSError:  # pragma: no cover - no pgrep
        return None
    return result.returncode == 0


def _registered_paths() -> list[str]:
    """Paths currently listed in GNOME's custom keybinding list."""
    from .shortcut import KEY, SCHEMA, get_value, parse_list

    return parse_list(get_value(SCHEMA, KEY))


def _entry_details(path: str) -> dict[str, str]:
    """name/binding/command of one custom shortcut (empty strings on failure)."""
    from .shortcut import CHILD_SCHEMA, get_value

    return {key: (get_value(CHILD_SCHEMA, key, path) or "").strip("'") for key in ("binding", "command")}


def _first_word(command: str) -> str:
    """The program a keybinding command starts, or '' when unparsable."""
    if not command:
        return ""
    try:
        parts = shlex.split(command)
    except ValueError:
        return ""
    return parts[0] if parts else ""


def cmd_setup_paste(assume_yes: bool = False) -> int:
    """Install and configure automatic pasting (GNOME/Wayland needs ydotool)."""
    from .paste import paste_keys_for_status, run_setup, setup_steps, uinput_writable, ydotoold_unit_path

    print(f"{APP_NAME} {__version__} — automatic paste setup")
    print(f"  current state    {paste_keys_for_status()}")
    steps = setup_steps()
    if not steps:
        print("  nothing to do — pasting already works.")
        return EXIT_OK

    print("\n  these commands will run:")
    for step in steps:
        prefix = "sudo " if step.sudo else ""
        note = "  (optional)" if step.optional else ""
        relocate = "  (needs a log out once)" if step.relogin else ""
        print(f"    {prefix}{' '.join(step.command)}{note}{relocate}")
        print(f"      {step.description}")
    print(f"    write {ydotoold_unit_path()}")

    if not assume_yes:
        if not sys.stdin.isatty():
            print("\n  nothing was changed — run the same command with --yes to execute it.")
            return EXIT_OK
        try:
            answer = input("\n  run them now? [y/N] ").strip().lower()
        except EOFError:  # pragma: no cover - closed stdin
            answer = ""
        if answer not in {"y", "yes"}:
            print("  nothing was changed.")
            return EXIT_OK

    results = run_setup()
    print("")
    for description, ok, optional in results:
        mark = "✓" if ok else ("·" if optional else "✗")
        print(f"  {mark} {description}")
    print(f"\n  state            {paste_keys_for_status()}")
    failed = [description for description, ok, optional in results if not ok and not optional]
    if failed:
        print("  some steps failed — run them by hand from the list above.")
        return EXIT_FAILURE
    if not uinput_writable():
        print("  /dev/uinput is not writable for this user yet: log out and back in once,")
        print("  then Win+V copies an item and pastes it straight into the focused window.")
    else:
        print("  done — Win+V now copies an item and pastes it where you are typing.")
    return EXIT_OK


def _clipboard_preview(limit: int = 60) -> str:
    """What is on the clipboard right now, so a paste test is not a surprise."""
    import subprocess

    for tool in (["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"]):
        try:
            result = subprocess.run(
                tool, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3, check=False
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0 and result.stdout:
            text = result.stdout.decode("utf-8", errors="replace").strip().replace("\n", " ⏎ ")
            return text[:limit] + ("…" if len(text) > limit else "")
    return ""


def _test_paste_item(store: HistoryStore, number: int) -> tuple[str | None, str]:
    """Resolve ``--item N`` (list position first, then item id) to its text."""
    items = store.list(limit=500)
    item = items[number - 1] if 1 <= number <= len(items) else store.get(number)
    if item is None:
        return None, f"no history item {number} — see: {APP_NAME.lower()} --list"
    text = store.get_text(item.id)
    if not text:
        return None, f"item #{item.id} is a {item.type.value} item; the paste test needs text"
    return text, f"item #{item.id} ({item.type.value}, {len(text)} chars)"


def cmd_test_paste(delay: float = 5.0, item_number: int | None = None) -> int:
    """Press Ctrl+V after a countdown — the honest way to check pasting."""
    from .paste import (
        ensure_clipboard_text,
        paste_notes,
        read_clipboard_text_all,
        send_paste,
        usable_tools,
    )

    print(f"{APP_NAME} {__version__} — paste test")
    tools = usable_tools()
    print(f"  helpers that can paste  {', '.join(tools) or 'none'}")

    if item_number is not None:
        from .config import get_config

        store = HistoryStore(config=get_config())
        try:
            text, label = _test_paste_item(store, item_number)
        finally:
            store.close()
        if text is None:
            print(f"  {label}")
            return EXIT_FAILURE
        print(f"  putting on clipboard    {label}")
        print(f"  preview                 {text.strip()[:60].replace(chr(10), ' ⏎ ')}")
        verified, tool = ensure_clipboard_text(text)
        if verified:
            print(f"  clipboard verified      held{f' (re-published with {tool})' if tool else ''}")
        else:
            print("  clipboard verified      NOT held — the sides disagree:")
            for name, value in read_clipboard_text_all().items():
                print(f"    {name:<20} {(value or '(unreadable)').strip()[:40]!r}")
            for note in paste_notes():
                print(f"  {note}")
            return EXIT_FAILURE

    preview = _clipboard_preview()
    print(f"  clipboard now           {preview or '(empty or unreadable)'}")
    if not tools:
        print("\n  no helper can press the key for you — press Ctrl+V yourself.")
        for note in paste_notes():
            print(f"  {note}")
        return EXIT_FAILURE

    seconds = max(0.0, delay)
    print(f"\n  put the cursor in a text field — pressing Ctrl+V in {seconds:g} seconds…")
    while seconds > 0:
        print(f"    {seconds:g}", flush=True)
        time.sleep(1.0 if seconds >= 1 else seconds)
        seconds -= 1.0
    ok, tool = send_paste()
    if ok:
        print(f"\n  ✓ pressed Ctrl+V with {tool}")
        print("    if the text appeared where the cursor was, automatic pasting works.")
        return EXIT_OK
    print("\n  ✗ no helper could send the key — nothing was pasted.")
    for note in paste_notes():
        print(f"    {note}")
    return EXIT_FAILURE


def cmd_diagnose(config: Config) -> int:
    """Answer "why does Win+V not open the window?" with a checklist."""
    from .app import gtk_available
    from .shortcut import (
        DEFAULT_BINDING,
        RIVAL_CLIPBOARDS,
        conflicts,
        daemon_log,
        foreign_bindings,
        gsettings_available,
    )
    from .shortcut import status as shortcut_status

    print(f"{APP_NAME} {__version__} — Win+V diagnosis")
    problems: list[str] = []
    gsettings_missing = False

    # 1. is the application itself healthy?
    print("\n1. the application")
    gtk = gtk_available()
    print(f"   GTK 4 available        {'yes' if gtk else 'NO — install python3-gi gir1.2-gtk-4.0'}")
    if not gtk:
        problems.append("GTK 4 is missing, so no window can be opened")
    state = is_running()
    if state is None:
        print("   running instance       unknown (no session bus)")
    else:
        print(f"   running instance       {'yes' if state else 'no — start it with --background'}")
    if state is False:
        problems.append("the background service is not running")
    print(f"   database               {database_path()}")
    print(f"   configuration          {config_path()}")

    # 2. is the keybinding registered?
    print("\n2. the keybinding (org.gnome.settings-daemon.plugins.media-keys)")
    if not gsettings_available():
        gsettings_missing = True
        print("   gsettings              not found — this is not a GNOME session")
        problems.append("gsettings is unavailable, so no shortcut can be registered")
        wanted = config.shortcut or DEFAULT_BINDING
    else:
        state_shortcut = shortcut_status()
        wanted = config.shortcut or DEFAULT_BINDING
        binding = state_shortcut.get("binding")
        command = state_shortcut.get("command")
        listed = bool(state_shortcut.get("ours_listed"))
        print(f"   registered             {'yes' if listed else 'NO — run --install-shortcut'}")
        for entry in _registered_paths():
            print(f"   in the list            {entry}{' (ours)' if entry == KEY_PATH else ''}")
        print(f"   binding                {binding or '-'}")
        print(f"   command                {command or '-'}")
        if not listed:
            problems.append("our shortcut is not in the GNOME keybinding list")
        if binding and binding.strip("'") != wanted:
            print(f"   note                   the configured key is {wanted!r}")
        program = _first_word(command.strip("'"))
        if program and not Path(program).exists():
            print(f"   command exists         NO — {program} is gone")
            problems.append("the shortcut runs a program that no longer exists")

        # 3. what else is registered, and does it still work?
        print("\n3. every custom shortcut")
        for path in _registered_paths():
            slot = path.rstrip("/").rsplit("/", 1)[-1]
            entry = _entry_details(path)
            print(f"   {slot:<22} {entry['binding'] or '-'}  {entry['command'] or '-'}")
            program = _first_word(entry["command"])
            if program and not Path(program).exists():
                print("                          ↳ that program is gone — this shortcut cannot work")
                if path != KEY_PATH:
                    problems.append(
                        f"the shortcut {slot} ({program}) no longer works — remove it or take the key over"
                    )
        clashing = foreign_bindings(wanted)
        for path, other in clashing:
            print(f"   same key as ours       {path} ({other or 'no command'})")
        if clashing:
            problems.append(
                "another custom shortcut uses the same key — "
                "remove it with --take-binding or in Settings → Keyboard"
            )
        shell = (conflicts(wanted) or [None])[0]
        print(f"   GNOME shell            {shell or 'free'}")

        # 4. will the daemon even react?
        print("\n4. the shortcut daemon")
        running_rivals = [name for name in RIVAL_CLIPBOARDS if _process_running(name)]
        if running_rivals:
            print(
                f"   other clipboard        {chr(44).join(running_rivals)} — quit it, two managers fight over the key"
            )
            problems.append("another clipboard manager is running: " + chr(44).join(running_rivals))
        media_keys = _process_running("gsd-media-keys")
        print(
            "   gsd-media-keys         "
            + {True: "running", False: "NOT running — restart it or log out", None: "unknown"}[media_keys]
        )
        if media_keys is False:
            problems.append("gnome-settings-daemon's media-keys plugin is not running")
        for line in daemon_log(8):
            print(f"   log                    {line}")

    # 5. can a chosen item actually reach the focused window?
    section = "3" if gsettings_missing else "5"
    from .clipboard import detect_session
    from .paste import (
        paste_keys_for_status,
        paste_notes,
        paste_tools,
        read_clipboard_text_all,
        uinput_writable,
        usable_tools,
        user_in_input_group,
        ydotoold_running,
    )

    session = detect_session()
    print(f"\n{section}. pasting into the focused window")
    print(f"   session                {session}")
    print(f"   window backend         {display_backend_label()}")
    print(f"   helpers installed      {', '.join(paste_tools()) or 'none'}")
    print(f"   helpers that can paste {', '.join(usable_tools()) or 'none'}")
    if "ydotool" in paste_tools():
        print(f"   ydotoold running       {'yes' if ydotoold_running() else 'no'}")
        writable = uinput_writable()
        in_group = user_in_input_group()
        detail = (
            "yes"
            if writable
            else ("no — the input group is not active in this session" if in_group else "no")
        )
        print(f"   /dev/uinput writable   {detail}")
        print(f"   input group            {'member' if in_group else 'not a member'}")
    print(f"   verdict                {paste_keys_for_status()}")
    sides = read_clipboard_text_all()
    for tool, value in sides.items():
        shown = (value or "").strip().splitlines()[0][:40] if value else "(unreadable)"
        print(f"   {tool:<22} {shown or '(empty)'}")
    if not usable_tools():
        problems.append(
            "automatic pasting is not available: the clipboard gets the item, but you must "
            "press Ctrl+V — run --setup-paste to fix that"
        )
        for note in paste_notes():
            print(f"   fix                    {note}")

    print("\nsummary")
    if not problems:
        print("   everything checks out — press Win+V. If nothing appears, the key is")
        print("   being handled elsewhere: log out and back in once, then try again.")
    else:
        for index, problem in enumerate(problems, start=1):
            print(f"   {index}. {problem}")
        print("\n   fastest fixes:")
        print("     ubuntu-clipboard --install-shortcut --take-binding")
        print("     pgrep -a gsd-media-keys   # dead? the tool restarts it on the next install")
        print("     systemctl --user reset-failed org.gnome.SettingsDaemon.MediaKeys")
        print("     ubuntu-clipboard --install-shortcut --binding '<Super><Alt>v'")
        print("     ubuntu-clipboard --setup-paste    # automatic paste into the focused window")
    print("\nfull report for a bug report: ubuntu-clipboard --collect-logs")
    return EXIT_OK


#: Set to ``wayland`` or ``x11`` to override the backend decision.
BACKEND_ENV = "UBUNTU_CLIPBOARD_BACKEND"


#: Where X servers keep their local sockets (XWayland included).
X11_SOCKET_DIR = Path("/tmp/.X11-unix")


def x11_available(
    env: dict[str, str] | None = None,
    socket_dir: Path = X11_SOCKET_DIR,
) -> bool:
    """Whether ``DISPLAY`` points at a *live* local X server.

    Only a local display can be XWayland: forwarding ``DISPLAY`` (SSH, a nested
    session) must not divert the application to a backend it cannot open.
    """
    environment = os.environ if env is None else env
    display = environment.get("DISPLAY", "")
    if not display.startswith(":"):
        return False
    number = display[1:].split(".")[0]
    if not number.isdigit():
        return False
    path = str(socket_dir / f"X{number}")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.25)
        for address in (path, "\0" + path):  # filesystem, then abstract socket
            try:
                probe.connect(address)
            except OSError:
                continue
            return True
    return False


def display_backend(hide_from_dock: bool | None = None) -> str:
    """The display backend the windowed application will use.

    GNOME exposes no way for a Wayland client to skip the taskbar, so a Wayland
    window always shows up in the dock while it is open. XWayland honours
    ``_NET_WM_STATE_SKIP_TASKBAR``, so a popup that should stay out of the dock
    is started on the X11 backend — still inside the Wayland session, just
    through XWayland, which is what the ``hide_from_dock`` setting controls.
    """
    from .clipboard import detect_session

    requested = os.environ.get(BACKEND_ENV, "auto").strip().lower()
    if requested in {"x11", "wayland"}:
        return requested
    if os.environ.get("GDK_BACKEND"):
        return os.environ["GDK_BACKEND"]
    if hide_from_dock is None:
        hide_from_dock = get_config().hide_from_dock
    if hide_from_dock and detect_session() == "wayland" and x11_available():
        return "x11"
    return detect_session()


def display_backend_label() -> str:
    """``display_backend`` plus the reason, as printed by ``--status``/``--diagnose``."""
    backend = display_backend()
    return (
        f"{backend} (kept out of the dock)" if backend == "x11" and get_config().hide_from_dock else backend
    )


def _apply_display_backend() -> str:
    """Export ``GDK_BACKEND`` *before* GTK initialises (it cannot be changed later)."""
    backend = display_backend()
    if backend in {"x11", "wayland"}:
        os.environ["GDK_BACKEND"] = backend
    return backend


def run_app(command: str, debug: bool = False) -> int:
    """Hand the command to the (single) GTK application instance."""
    backend = _apply_display_backend()
    from .app import ClipboardApplication, gtk_available

    if not gtk_available():
        print(f"{APP_NAME}: GTK 4 is required for this command.", file=sys.stderr)
        print("  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1", file=sys.stderr)
        return EXIT_FAILURE
    log.debug("display backend: %s", backend)
    config = get_config()
    set_language(config.language)
    try:
        application = ClipboardApplication(
            config=config,
            command=command,
            debug=debug,
            start_hidden=command in {"background", "none"},
        )
        return application.run([sys.argv[0], f"--{command}"])
    except Exception as exc:  # pragma: no cover - display/bus problems
        return _explain_failure(exc, debug)


def _explain_failure(exc: Exception, debug: bool = False) -> int:
    """Log an unexpected failure and tell the user where the details are."""
    log.error("unexpected error: %s", exc)
    log.debug("traceback:", exc_info=exc)
    print(f"{APP_NAME}: {exc}", file=sys.stderr)
    if debug:
        traceback.print_exc()
    else:
        print(f"  full traceback in {log_path()} — run: ubuntu-clipboard --logs", file=sys.stderr)
    return EXIT_FAILURE


# ── entry point ────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    """Entry point of ``ubuntu-clipboard``; never raises."""
    try:
        return _main(argv)
    except KeyboardInterrupt:  # pragma: no cover - user pressed Ctrl+C
        return EXIT_FAILURE
    except Exception as exc:  # pragma: no cover - last resort
        arguments = sys.argv[1:] if argv is None else list(argv)
        setup_logging(console="--debug" in arguments)
        return _explain_failure(exc, debug="--debug" in arguments)


def _main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)
    setup_logging(debug=args.debug, console=args.debug)

    if args.tray:
        log.warning("--with-tray/--no-tray are deprecated: the tray was removed in 2.0.0")

    if args.version:
        print(f"{APP_NAME} {__version__}")
        return EXIT_OK

    config = get_config()
    set_language(config.language)

    if args.binding:
        config.shortcut = args.binding
        config.save()
    if args.install_shortcut or args.remove_shortcut:
        return cmds_shortcut(
            install=args.install_shortcut,
            take_binding=args.take_binding,
            binding=args.binding or config.shortcut,
        )
    if args.setup_paste:
        return cmd_setup_paste(assume_yes=args.yes)
    if args.install:
        return cmd_install(args, config)
    if args.uninstall:
        return cmd_uninstall(args)

    if args.clear_logs:
        clear_logs()
        print("logs cleared")
        return EXIT_OK
    if args.logs is not None:
        print(tail(max(1, args.logs)))
        return EXIT_OK

    if args.diagnose:
        return cmd_diagnose(config)
    if args.test_paste:
        return cmd_test_paste(args.delay, args.item)

    if args.status or args.collect_logs or args.list is not None or args.clear:
        store = HistoryStore(config=config)
        try:
            if args.status:
                return cmd_status(store)
            if args.collect_logs:
                return cmd_collect_logs(store)
            if args.list is not None:
                return cmd_list(store, args.list)
            removed = store.clear(keep_pinned=not args.clear_all)
            print(f"{t('notify.history_cleared')} ({removed})")
            return EXIT_OK
        finally:
            store.close()

    return run_app(_command_from_args(args), debug=args.debug)


def main_daemon(argv: list[str] | None = None) -> int:
    """Entry point of ``ubuntu-clipboard-daemon`` (kept for backwards compatibility).

    ``--background`` is prepended only when the caller did not ask for something
    else, so ``ubuntu-clipboard-daemon --version`` still prints the version.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not any(argument.startswith("--") and argument != "--debug" for argument in arguments):
        arguments.insert(0, "--background")
    return main(arguments)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
