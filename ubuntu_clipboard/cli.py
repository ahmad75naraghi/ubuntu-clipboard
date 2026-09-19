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
import sys

from . import APP_ID, APP_NAME, PROJECT_URL, __version__
from .config import Config, config_path, get_config
from .i18n import set_language, t
from .log import clear as clear_logs
from .log import setup_logging, tail
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

    diagnostics = parser.add_argument_group("diagnostics")
    diagnostics.add_argument("--status", action="store_true", help="print environment and installation state")
    diagnostics.add_argument(
        "--logs", nargs="?", const=200, type=int, metavar="N", help="print the last N log lines"
    )
    diagnostics.add_argument("--clear-logs", action="store_true", help="delete the log files")
    diagnostics.add_argument(
        "--collect-logs", action="store_true", help="write a diagnostic report and print its path"
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
    "integration": ("install", "uninstall", "install_shortcut", "remove_shortcut"),
    "diagnostics": ("status", "logs", "clear_logs", "collect_logs"),
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
        print(f"  shortcut         {binding} -> {command}")
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


def cmd_install(_args: argparse.Namespace, config: Config) -> int:
    from .install import install_all

    report = install_all(
        enable_startup=config.auto_start,
        shortcut_binding=config.shortcut,
        install_keybinding=True,
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


def cmds_shortcut(install: bool) -> int:
    from .shortcut import ShortcutError
    from .shortcut import install as shortcut_install
    from .shortcut import uninstall as shortcut_uninstall

    try:
        report = shortcut_install() if install else shortcut_uninstall()
    except ShortcutError as exc:
        print(f"  ! {exc}", file=sys.stderr)
        return EXIT_FAILURE
    for message in report.messages:
        print(f"  ✓ {message}")
    return EXIT_OK


def run_app(command: str, debug: bool = False) -> int:
    """Hand the command to the (single) GTK application instance."""
    from .app import ClipboardApplication, gtk_available

    if not gtk_available():
        print(f"{APP_NAME}: GTK 4 is required for this command.", file=sys.stderr)
        print("  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1", file=sys.stderr)
        return EXIT_FAILURE
    config = get_config()
    set_language(config.language)
    application = ClipboardApplication(
        config=config,
        command=command,
        debug=debug,
        start_hidden=command in {"background", "none"},
    )
    try:
        return application.run([sys.argv[0], f"--{command}"])
    except Exception as exc:  # pragma: no cover - display/bus problems
        log.error("cannot start the application: %s", exc)
        print(f"{APP_NAME}: {exc}", file=sys.stderr)
        return EXIT_FAILURE


# ── entry point ────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
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

    if args.install_shortcut or args.remove_shortcut:
        return cmds_shortcut(install=args.install_shortcut)
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
