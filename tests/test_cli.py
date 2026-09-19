"""Command line interface behaviour, including the headless paths."""

from __future__ import annotations

import sys

import pytest
from ubuntu_clipboard import __version__, cli
from ubuntu_clipboard.config import Config
from ubuntu_clipboard.storage import HistoryStore


class BlockGtk:
    """Meta path hook that makes ``import gi`` fail."""

    def find_spec(self, name, path=None, target=None):
        if name == "gi" or name.startswith("gi."):
            raise ImportError("gi is blocked for this test")
        return


def test_parser_defaults_to_toggle():
    args = cli.build_parser().parse_args([])
    assert cli._command_from_args(args) == "toggle"


@pytest.mark.parametrize(
    ("flag", "command"),
    [
        ("--toggle", "toggle"),
        ("--show", "show"),
        ("--hide", "hide"),
        ("--settings", "settings"),
        ("--quit", "quit"),
        ("--background", "background"),
        ("--daemon", "background"),
        ("--hidden", "background"),
    ],
)
def test_every_app_command_is_recognised(flag, command):
    args = cli.build_parser().parse_args([flag])
    assert cli._command_from_args(args) == command


def test_unknown_option_exits():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--nonsense"])


def test_version(capsys):
    assert cli.main(["--version"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Ubuntu Clipboard" in out
    assert __version__ in out


def test_status_works_without_gtk(monkeypatch, capsys):
    monkeypatch.delitem(sys.modules, "gi", raising=False)
    monkeypatch.setattr(sys, "meta_path", [BlockGtk(), *sys.meta_path])
    assert cli.main(["--status"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "database" in out
    assert "items" in out
    assert "shortcut" in out


def test_list_prints_history(capsys):
    store = HistoryStore(config=Config())
    store.add_text("first entry")
    store.add_text("https://example.com")
    store.close()
    assert cli.main(["--list", "5"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "first entry" in out
    assert "https://example.com" in out


def test_list_is_empty_safe(capsys):
    assert cli.main(["--list"]) == cli.EXIT_OK
    assert capsys.readouterr().out.strip() != ""


def test_clear_keeps_pinned_by_default(capsys):
    store = HistoryStore(config=Config())
    pinned = store.add_text("keep")
    store.toggle_pin(pinned.id)
    store.add_text("remove")
    store.close()

    assert cli.main(["--clear"]) == cli.EXIT_OK
    assert "(1)" in capsys.readouterr().out  # exactly one item was removed

    store = HistoryStore(config=Config())
    assert store.count() == 1
    store.close()

    assert cli.main(["--clear", "--all"]) == cli.EXIT_OK
    store = HistoryStore(config=Config())
    assert store.count() == 0
    store.close()


def test_logs_and_clear_logs(capsys):
    assert cli.main(["--logs"]) == cli.EXIT_OK
    assert capsys.readouterr().out.strip() != ""
    assert cli.main(["--clear-logs"]) == cli.EXIT_OK
    assert capsys.readouterr().out.strip() != ""


def test_collect_logs_writes_a_report(capsys):
    assert cli.main(["--collect-logs"]) == cli.EXIT_OK
    target = capsys.readouterr().out.strip()
    from pathlib import Path

    report = Path(target)
    assert report.is_file()
    content = report.read_text(encoding="utf-8")
    assert "diagnostics" in content


def test_install_and_uninstall(monkeypatch, capsys):
    from ubuntu_clipboard.shortcut import Report as ShortcutReport

    monkeypatch.setattr(
        "ubuntu_clipboard.install.install_shortcut",
        lambda **_kwargs: ShortcutReport(ok=True),
    )
    monkeypatch.setattr(
        "ubuntu_clipboard.install.uninstall_shortcut",
        lambda **_kwargs: ShortcutReport(ok=True),
    )
    assert cli.main(["--install"]) == cli.EXIT_OK
    assert "desktop entry" in capsys.readouterr().out
    assert cli.main(["--uninstall"]) == cli.EXIT_OK
    assert capsys.readouterr().out.strip() != ""


def test_shortcut_without_gsettings_fails(monkeypatch, capsys):
    monkeypatch.setattr("ubuntu_clipboard.shortcut.shutil.which", lambda _name: None)
    assert cli.main(["--install-shortcut"]) == cli.EXIT_FAILURE
    assert "gsettings" in capsys.readouterr().err


def test_app_command_is_forwarded(monkeypatch):
    captured = {}

    def fake_run_app(command, debug=False):
        captured["command"] = command
        captured["debug"] = debug
        return 0

    monkeypatch.setattr(cli, "run_app", fake_run_app)
    assert cli.main(["--toggle"]) == 0
    assert captured["command"] == "toggle"
    assert cli.main(["--settings"]) == 0
    assert captured["command"] == "settings"


def test_app_command_needs_gtk(monkeypatch, capsys):
    import ubuntu_clipboard.app as app_module

    monkeypatch.setattr(app_module, "gtk_available", lambda: False)
    assert cli.run_app("toggle") == cli.EXIT_FAILURE
    assert "GTK" in capsys.readouterr().err


def test_deprecated_tray_flags_are_still_accepted():
    for flag in ("--with-tray", "--no-tray"):
        args = cli.build_parser().parse_args([flag])
        assert args.tray is True


def test_main_daemon_maps_to_background(monkeypatch):
    captured = {}

    def fake_run_app(command, debug=False):
        captured["command"] = command
        return 0

    monkeypatch.setattr(cli, "run_app", fake_run_app)
    assert cli.main_daemon([]) == 0
    assert captured["command"] == "background"


def test_main_daemon_forwards_other_options(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ubuntu-clipboard-daemon", "--version"])
    assert cli.main_daemon() == cli.EXIT_OK
    assert "Ubuntu Clipboard" in capsys.readouterr().out


def test_main_daemon_keeps_explicit_commands(monkeypatch):
    captured = {}

    def fake_run_app(command, debug=False):
        captured["command"] = command
        return 0

    monkeypatch.setattr(cli, "run_app", fake_run_app)
    assert cli.main_daemon(["--toggle"]) == 0
    assert captured["command"] == "toggle"


def test_is_running_returns_none_without_gtk(monkeypatch):
    monkeypatch.delitem(sys.modules, "gi", raising=False)
    monkeypatch.setattr(sys, "meta_path", [BlockGtk(), *sys.meta_path])
    assert cli.is_running() is None


def test_conflicting_action_groups_are_rejected(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--list", "--status"])
    assert excinfo.value.code == 2
    assert "different groups" in capsys.readouterr().err


def test_purge_requires_uninstall(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--purge"])
    assert "--purge" in capsys.readouterr().err


def test_clear_all_requires_clear(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--all"])
    assert "--all" in capsys.readouterr().err


def test_clear_all_is_allowed_with_clear():
    store = HistoryStore(config=Config())
    store.add_text("x")
    store.close()
    assert cli.main(["--clear", "--all"]) == cli.EXIT_OK


def test_negative_log_count_is_clamped(capsys):
    assert cli.main(["--logs", "-5"]) == cli.EXIT_OK
    assert capsys.readouterr().out.strip() != ""


def test_status_reports_clipboard_capabilities(capsys):
    assert cli.main(["--status"]) == cli.EXIT_OK
    assert "clipboard tools" in capsys.readouterr().out


def test_status_says_when_the_shortcut_is_missing(monkeypatch, capsys):
    monkeypatch.setattr("ubuntu_clipboard.shortcut.gsettings_available", lambda: True)
    monkeypatch.setattr(
        "ubuntu_clipboard.shortcut.status",
        lambda **_kwargs: {"registered_paths": [], "ours_listed": False, "binding": None, "command": None},
    )
    assert cli.main(["--status"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "--install-shortcut" in out


def test_unexpected_errors_are_reported_not_raised(monkeypatch, capsys):
    def explode(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "run_app", explode)
    assert cli.main(["--toggle"]) == cli.EXIT_FAILURE
    captured = capsys.readouterr()
    assert "boom" in captured.err
    assert "ubuntu-clipboard --logs" in captured.err


def test_take_binding_needs_an_install_command():
    with pytest.raises(SystemExit) as info:
        cli.main(["--take-binding"])
    assert info.value.code == 2


def test_take_binding_is_forwarded_to_the_shortcut_installer(monkeypatch):
    from ubuntu_clipboard.shortcut import Report as ShortcutReport

    seen = {}

    def fake_install(*, binding=None, launch_command=None, take_binding=False):
        seen["take_binding"] = take_binding
        return ShortcutReport(ok=True, binding=binding or "<Super>v")

    monkeypatch.setattr("ubuntu_clipboard.shortcut.install", fake_install)
    assert cli.main(["--install-shortcut", "--take-binding"]) == cli.EXIT_OK
    assert seen["take_binding"] is True


def test_binding_needs_an_install_command():
    with pytest.raises(SystemExit) as info:
        cli.main(["--binding", "<Super><Alt>v"])
    assert info.value.code == 2


def test_binding_is_saved_and_used(monkeypatch, capsys):
    seen = {}

    def fake_install(*, binding=None, launch_command=None, take_binding=False):
        from ubuntu_clipboard.shortcut import Report as ShortcutReport

        seen["binding"] = binding
        return ShortcutReport(ok=True, binding=binding or "<Super>v")

    monkeypatch.setattr("ubuntu_clipboard.shortcut.install", fake_install)
    assert cli.main(["--install-shortcut", "--binding", "<Super><Alt>v"]) == cli.EXIT_OK
    assert seen["binding"] == "<Super><Alt>v"
    assert Config.load().shortcut == "<Super><Alt>v"  # persisted for the next install


def test_diagnose_prints_a_checklist(monkeypatch, capsys):
    monkeypatch.setattr("ubuntu_clipboard.cli.is_running", lambda: True)
    monkeypatch.setattr("ubuntu_clipboard.shortcut.gsettings_available", lambda: False)
    assert cli.main(["--diagnose"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Win+V diagnosis" in out
    assert "gsettings" in out
