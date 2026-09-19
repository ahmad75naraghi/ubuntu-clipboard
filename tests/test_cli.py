"""Command line interface behaviour, including the headless paths."""

from __future__ import annotations

import os
import socket
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


def test_diagnose_names_the_rival_shortcut_and_process(monkeypatch, capsys):
    other = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/"
    monkeypatch.setattr("ubuntu_clipboard.shortcut.gsettings_available", lambda: True)
    monkeypatch.setattr(
        "ubuntu_clipboard.shortcut.status",
        lambda: {
            "registered_paths": [other, cli.KEY_PATH],
            "ours_listed": True,
            "name": "'Ubuntu Clipboard'",
            "command": "'/usr/bin/ubuntu-clipboard --toggle'",
            "binding": "'<Super>v'",
        },
    )
    monkeypatch.setattr(
        "ubuntu_clipboard.shortcut.get_value", lambda *_a, **_k: f"['{other}', '{cli.KEY_PATH}']"
    )
    monkeypatch.setattr(
        "ubuntu_clipboard.shortcut.foreign_bindings", lambda *_a, **_k: [(other, "/usr/bin/diodon")]
    )
    monkeypatch.setattr("ubuntu_clipboard.shortcut.daemon_log", lambda *_a, **_k: ["registered <Super>v"])
    monkeypatch.setattr(
        "ubuntu_clipboard.cli._process_running", lambda pattern: pattern in {"gsd-media-keys", "diodon"}
    )
    assert cli.main(["--diagnose"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert f"{other} (/usr/bin/diodon)" in out
    assert "in the list" in out and "(ours)" in out
    assert "another clipboard manager is running: diodon" in out
    assert "registered <Super>v" in out


def test_setup_paste_prints_the_plan_without_running_it(monkeypatch, capsys):
    monkeypatch.setattr("ubuntu_clipboard.paste.setup_steps", lambda *a, **k: [])
    assert cli.main(["--setup-paste"]) == cli.EXIT_OK
    assert "nothing to do" in capsys.readouterr().out


def test_setup_paste_lists_commands_and_waits_for_confirmation(monkeypatch, capsys):
    from ubuntu_clipboard.paste import Step

    steps = [Step("install ydotool", ("apt-get", "install", "-y", "ydotool"), sudo=True)]
    monkeypatch.setattr("ubuntu_clipboard.paste.setup_steps", lambda *a, **k: steps)
    monkeypatch.setattr("ubuntu_clipboard.paste.run_setup", lambda *a, **k: pytest.fail("must not run"))
    monkeypatch.setattr("ubuntu_clipboard.cli.sys.stdin.isatty", lambda: False)
    assert cli.main(["--setup-paste"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "sudo apt-get install -y ydotool" in out
    assert "--yes" in out


def test_setup_paste_runs_everything_with_yes(monkeypatch, capsys):
    monkeypatch.setattr(
        "ubuntu_clipboard.paste.run_setup",
        lambda *a, **k: [("install ydotool", True, False), ("start ydotoold", True, False)],
    )
    monkeypatch.setattr("ubuntu_clipboard.paste.uinput_writable", lambda: True)
    assert cli.main(["--setup-paste", "--yes"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "✓ install ydotool" in out
    assert "pastes it where you are typing" in out


def test_setup_paste_fails_when_a_required_step_fails(monkeypatch, capsys):
    monkeypatch.setattr(
        "ubuntu_clipboard.paste.run_setup", lambda *a, **k: [("install ydotool", False, False)]
    )
    assert cli.main(["--setup-paste", "--yes"]) == cli.EXIT_FAILURE
    assert "some steps failed" in capsys.readouterr().out


def test_yes_needs_setup_paste():
    with pytest.raises(SystemExit) as info:
        cli.main(["--yes"])
    assert info.value.code == 2


def test_test_paste_reports_the_helper(monkeypatch, capsys):
    monkeypatch.setattr("ubuntu_clipboard.paste.usable_tools", lambda *a, **k: ["ydotool"])
    monkeypatch.setattr("ubuntu_clipboard.paste.send_paste", lambda *a, **k: (True, "ydotool"))
    monkeypatch.setattr("ubuntu_clipboard.cli._clipboard_preview", lambda: "hello")
    assert cli.main(["--test-paste", "--delay", "0"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "clipboard now           hello" in out
    assert "pressed Ctrl+V with ydotool" in out


def test_test_paste_explains_a_failure(monkeypatch, capsys):
    monkeypatch.setattr("ubuntu_clipboard.paste.usable_tools", lambda *a, **k: ["ydotool"])
    monkeypatch.setattr("ubuntu_clipboard.paste.send_paste", lambda *a, **k: (False, None))
    monkeypatch.setattr("ubuntu_clipboard.paste.paste_notes", lambda *a, **k: ["log out and back in once"])
    assert cli.main(["--test-paste", "--delay", "0"]) == cli.EXIT_FAILURE
    out = capsys.readouterr().out
    assert "no helper could send the key" in out
    assert "log out and back in once" in out


def test_test_paste_without_helpers(monkeypatch, capsys):
    monkeypatch.setattr("ubuntu_clipboard.paste.usable_tools", lambda *a, **k: [])
    monkeypatch.setattr("ubuntu_clipboard.paste.paste_notes", lambda *a, **k: ["ydotool is not installed"])
    assert cli.main(["--test-paste", "--delay", "0"]) == cli.EXIT_FAILURE
    assert "ydotool is not installed" in capsys.readouterr().out


def test_delay_needs_test_paste():
    with pytest.raises(SystemExit) as info:
        cli.main(["--delay", "3"])
    assert info.value.code == 2


def test_diagnose_prints_the_fix_for_the_input_group(monkeypatch, capsys):
    monkeypatch.setattr("ubuntu_clipboard.cli.is_running", lambda: True)
    monkeypatch.setattr("ubuntu_clipboard.shortcut.gsettings_available", lambda: False)
    monkeypatch.setattr("ubuntu_clipboard.paste.usable_tools", lambda *a, **k: [])
    monkeypatch.setattr("ubuntu_clipboard.paste.paste_tools", lambda *a, **k: ["ydotool"])
    monkeypatch.setattr("ubuntu_clipboard.paste.uinput_writable", lambda *a: False)
    monkeypatch.setattr("ubuntu_clipboard.paste.user_in_input_group", lambda *a: True)
    monkeypatch.setattr("ubuntu_clipboard.paste.ydotoold_running", lambda *a, **k: False)
    monkeypatch.setattr(
        "ubuntu_clipboard.paste.paste_notes",
        lambda *a, **k: ["log out and back in once to apply it", "right now: sudo chmod 666 /dev/uinput"],
    )
    assert cli.main(["--diagnose"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "the input group is not active in this session" in out
    assert "chmod 666 /dev/uinput" in out


def test_test_paste_can_write_a_history_item(monkeypatch, capsys):
    import ubuntu_clipboard.cli as cli_module
    from ubuntu_clipboard.storage import HistoryStore

    store = HistoryStore(config=Config())
    store.add_text("first")
    store.add_text("the one I want")
    monkeypatch.setattr(cli_module, "HistoryStore", lambda **kwargs: store)
    monkeypatch.setattr("ubuntu_clipboard.paste.usable_tools", lambda *a, **k: ["ydotool"])
    monkeypatch.setattr("ubuntu_clipboard.paste.ensure_clipboard_text", lambda *a, **k: (True, "wl-copy"))
    monkeypatch.setattr("ubuntu_clipboard.paste.clipboard_holds_text", lambda *a, **k: True)
    monkeypatch.setattr("ubuntu_clipboard.paste.send_paste", lambda *a, **k: (True, "ydotool"))
    monkeypatch.setattr("ubuntu_clipboard.cli._clipboard_preview", lambda: "the one I want")
    assert cli.main(["--test-paste", "--item", "1", "--delay", "0"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "the one I want" in out
    assert "re-published with wl-copy" in out
    assert "pressed Ctrl+V" in out


def test_test_paste_reports_an_unknown_item(monkeypatch, capsys):
    import ubuntu_clipboard.cli as cli_module
    from ubuntu_clipboard.storage import HistoryStore

    store = HistoryStore(config=Config())
    monkeypatch.setattr(cli_module, "HistoryStore", lambda **kwargs: store)
    assert cli.main(["--test-paste", "--item", "9", "--delay", "0"]) == cli.EXIT_FAILURE
    assert "no history item 9" in capsys.readouterr().out


def test_item_needs_test_paste():
    with pytest.raises(SystemExit) as info:
        cli.main(["--item", "2"])
    assert info.value.code == 2


def test_test_paste_reports_a_clipboard_that_refuses_the_item(monkeypatch, capsys):
    import ubuntu_clipboard.cli as cli_module
    from ubuntu_clipboard.storage import HistoryStore

    store = HistoryStore(config=Config())
    store.add_text("mine")
    monkeypatch.setattr(cli_module, "HistoryStore", lambda **kwargs: store)
    monkeypatch.setattr("ubuntu_clipboard.paste.ensure_clipboard_text", lambda *a, **k: (False, "wl-copy"))
    monkeypatch.setattr(
        "ubuntu_clipboard.paste.read_clipboard_text_all",
        lambda *a, **k: {"wl-paste": "mine", "xclip": "the stale one"},
    )
    monkeypatch.setattr("ubuntu_clipboard.paste.paste_notes", lambda *a, **k: [])
    assert cli.main(["--test-paste", "--item", "1", "--delay", "0"]) == cli.EXIT_FAILURE
    out = capsys.readouterr().out
    assert "the sides disagree" in out
    assert "the stale one" in out


def test_display_backend_prefers_x11_to_stay_out_of_the_dock(monkeypatch):
    monkeypatch.delenv("UBUNTU_CLIPBOARD_BACKEND", raising=False)
    monkeypatch.delenv("GDK_BACKEND", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(cli, "x11_available", lambda *a, **k: True)
    assert cli.display_backend(hide_from_dock=True) == "x11"
    assert cli.display_backend(hide_from_dock=False) == "wayland"


def test_display_backend_can_be_overridden(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("UBUNTU_CLIPBOARD_BACKEND", "wayland")
    assert cli.display_backend(hide_from_dock=True) == "wayland"
    monkeypatch.setenv("UBUNTU_CLIPBOARD_BACKEND", "x11")
    assert cli.display_backend(hide_from_dock=False) == "x11"


def test_display_backend_falls_back_when_xwayland_is_not_reachable(monkeypatch):
    """A stale or forwarded DISPLAY must not divert the window to X11."""
    monkeypatch.delenv("UBUNTU_CLIPBOARD_BACKEND", raising=False)
    monkeypatch.delenv("GDK_BACKEND", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(cli, "x11_available", lambda *a, **k: False)
    assert cli.display_backend(hide_from_dock=True) == "wayland"


def test_x11_availability_is_probed_over_the_unix_socket(tmp_path, monkeypatch):
    """There is a real listener on the socket in this test, not a fake."""
    monkeypatch.delenv("DISPLAY", raising=False)
    assert cli.x11_available({}, socket_dir=tmp_path) is False
    assert cli.x11_available({"DISPLAY": "localhost:10"}, socket_dir=tmp_path) is False
    assert cli.x11_available({"DISPLAY": ":abcdef"}, socket_dir=tmp_path) is False

    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(tmp_path / "X7"))
    listener.listen(1)
    try:
        assert cli.x11_available({"DISPLAY": ":7"}, socket_dir=tmp_path) is True
        assert cli.x11_available({"DISPLAY": ":7.0"}, socket_dir=tmp_path) is True
        assert cli.x11_available({"DISPLAY": ":8"}, socket_dir=tmp_path) is False
    finally:
        listener.close()


def test_display_backend_without_xwayland_stays_on_wayland(monkeypatch):
    monkeypatch.delenv("UBUNTU_CLIPBOARD_BACKEND", raising=False)
    monkeypatch.delenv("GDK_BACKEND", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert cli.display_backend(hide_from_dock=True) == "wayland"


def test_apply_display_backend_exports_gdk_backend(monkeypatch):
    monkeypatch.delenv("GDK_BACKEND", raising=False)
    monkeypatch.setenv("UBUNTU_CLIPBOARD_BACKEND", "x11")
    assert cli._apply_display_backend() == "x11"
    assert os.environ["GDK_BACKEND"] == "x11"


def test_status_and_diagnose_mention_the_window_backend(capsys):
    assert cli.main(["--status"]) == cli.EXIT_OK
    assert "window backend" in capsys.readouterr().out


def test_backend_label_explains_the_choice(monkeypatch):
    monkeypatch.setenv("UBUNTU_CLIPBOARD_BACKEND", "x11")
    assert "kept out of the dock" in cli.display_backend_label()
    monkeypatch.setattr(cli, "display_backend", lambda *a, **k: "wayland")
    assert cli.display_backend_label() == "wayland"


def test_diagnose_reports_the_key_of_every_layout(monkeypatch, capsys):
    """With a Persian layout, <Super>v alone is not enough — say so out loud."""
    from ubuntu_clipboard import keymap

    monkeypatch.setattr("ubuntu_clipboard.shortcut.gsettings_available", lambda: True)
    monkeypatch.setattr(
        "ubuntu_clipboard.shortcut.status",
        lambda **_kwargs: {
            "registered_paths": [cli.KEY_PATH],
            "ours_listed": True,
            "name": "'Clipboard — Win+V'",
            "command": "'/usr/bin/ubuntu-clipboard --toggle'",
            "binding": "'<Super>v'",
        },
    )
    monkeypatch.setattr("ubuntu_clipboard.shortcut.get_value", lambda *_a, **_k: f"['{cli.KEY_PATH}']")
    monkeypatch.setattr(
        "ubuntu_clipboard.keymap.configured_layouts", lambda *_a, **_k: [("us", ""), ("ir", "")]
    )
    monkeypatch.setattr("ubuntu_clipboard.keymap.default_backend", lambda: object())
    monkeypatch.setattr(
        "ubuntu_clipboard.keymap.binding_plan",
        lambda primary, **_kwargs: [
            keymap.LayoutBinding(primary),
            keymap.LayoutBinding("<Super>Arabic_ra", "Persian"),
        ],
    )
    assert cli.main(["--diagnose"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "keyboard layouts       us, ir" in out
    assert "needed for them        <Super>Arabic_ra (Persian)" in out


def test_diagnose_says_when_the_layout_cannot_be_read(monkeypatch, capsys):
    monkeypatch.setattr("ubuntu_clipboard.shortcut.gsettings_available", lambda: True)
    monkeypatch.setattr(
        "ubuntu_clipboard.shortcut.status",
        lambda **_kwargs: {"registered_paths": [], "ours_listed": False, "binding": None, "command": None},
    )
    monkeypatch.setattr(
        "ubuntu_clipboard.keymap.configured_layouts", lambda *_a, **_k: [("us", ""), ("ir", "")]
    )
    monkeypatch.setattr("ubuntu_clipboard.keymap.default_backend", lambda: None)
    monkeypatch.setattr("ubuntu_clipboard.keymap.binding_plan", lambda primary, **_k: [])
    assert cli.main(["--diagnose"]) == cli.EXIT_OK
    assert "libxkbcommon is missing" in capsys.readouterr().out


def test_status_lists_the_extra_layout_bindings(monkeypatch, capsys):
    monkeypatch.setattr("ubuntu_clipboard.shortcut.gsettings_available", lambda: True)
    monkeypatch.setattr(
        "ubuntu_clipboard.shortcut.status",
        lambda **_kwargs: {
            "registered_paths": [
                "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/ubuntu-clipboard/"
            ],
            "ours_listed": True,
            "name": "'Clipboard — Win+V'",
            "command": "'ubuntu-clipboard --toggle'",
            "binding": "'<Super>v'",
            "bindings": ["<Super>v", "<Super>Arabic_ra"],
        },
    )
    assert cli.main(["--status"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "<Super>Arabic_ra" in out
    assert "layouts" in out
