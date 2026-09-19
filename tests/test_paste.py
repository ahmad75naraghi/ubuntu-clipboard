"""Paste simulation: helper selection and command construction."""

from __future__ import annotations

import subprocess

import pytest
from ubuntu_clipboard import paste


def which_for(*names: str):
    return lambda name: f"/usr/bin/{name}" if name in names else None


class RecordingRunner:
    """Answers ``ydotool --version`` from ``version``; the rest from ``returncodes``."""

    def __init__(self, returncodes: list[int] | None = None, explode: bool = False, version="ydotool 0.1.8"):
        self.returncodes = list(returncodes or [])
        self.explode = explode
        self.version = version
        self.commands: list[list[str]] = []

    def __call__(self, command, **_kwargs) -> subprocess.CompletedProcess:
        if list(command)[1:2] == ["--version"]:
            return subprocess.CompletedProcess(list(command), 0, str(self.version).encode(), b"")
        self.commands.append(list(command))
        if self.explode:
            raise OSError("tool vanished")
        code = self.returncodes.pop(0) if self.returncodes else 1
        return subprocess.CompletedProcess(list(command), code, b"", b"")


def test_tool_commands():
    assert paste.tool_command("xdotool") == ["xdotool", "key", "--clearmodifiers", "ctrl+v"]
    assert paste.tool_command("wtype") == ["wtype", "-M", "ctrl", "-P", "v", "-m", "ctrl"]
    # Ubuntu 24.04 ships ydotool 0.1.x, which reads key names; 1.x reads codes.
    assert paste.tool_command("ydotool") == ["ydotool", "key", "ctrl+v"]
    assert paste.YDOTOOL_CODES == ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"]


def test_ydotool_syntax_follows_the_installed_version():
    def version(text: str):
        def runner(command, **_kwargs):
            return subprocess.CompletedProcess(list(command), 0, text.encode(), b"")

        return paste.ydotool_uses_keycodes(which_for("ydotool"), runner)

    assert version("ydotool v1.0.4-38-g708e96f") is True
    assert version("ydotool 0.1.8") is False
    assert version("usage: ydotool ...") is None
    assert paste.ydotool_uses_keycodes(which_for(), RecordingRunner()) is None


def test_ydotool_candidates_start_with_the_right_syntax():
    def versioned(text: str):
        def runner(command, **_kwargs):
            return subprocess.CompletedProcess(list(command), 0, text.encode(), b"")

        return paste.paste_candidates("ydotool", which_for("ydotool"), runner)

    assert versioned("ydotool 0.1.8")[0] == paste.YDOTOOL_NAMES
    assert versioned("ydotool 1.0.4")[0] == paste.YDOTOOL_CODES
    assert paste.paste_candidates("xdotool", which_for("xdotool")) == [
        ["xdotool", "key", "--clearmodifiers", "ctrl+v"]
    ]


def test_send_paste_retries_the_other_ydotool_syntax():
    runner = RecordingRunner(returncodes=[1, 0])
    success, tool = paste.send_paste(which_for("ydotool"), runner, {"XDG_SESSION_TYPE": "wayland"})
    assert (success, tool) == (True, "ydotool")
    assert runner.commands == [paste.YDOTOOL_NAMES, paste.YDOTOOL_CODES]


def test_ydotoold_state_is_reported(tmp_path):
    runner = RecordingRunner(returncodes=[1])
    assert (
        paste.ydotoold_running(which_for("ydotool", "pgrep"), runner, {"XDG_RUNTIME_DIR": str(tmp_path)})
        is False
    )
    (tmp_path / ".ydotool_socket").write_text("")
    assert paste.ydotoold_running(which_for("ydotool"), runner, {"XDG_RUNTIME_DIR": str(tmp_path)}) is True


def test_ydotool_without_daemon_or_uinput_is_not_usable(monkeypatch):
    monkeypatch.setattr(paste, "ydotoold_running", lambda *_a, **_k: False)
    monkeypatch.setattr(paste, "uinput_writable", lambda *_a: False)
    assert paste.usable_tools(which_for("ydotool"), {"XDG_SESSION_TYPE": "wayland"}) == []
    monkeypatch.setattr(paste, "uinput_writable", lambda *_a: True)
    assert paste.usable_tools(which_for("ydotool"), {"XDG_SESSION_TYPE": "wayland"}) == ["ydotool"]


def test_xdotool_counts_as_unusable_on_wayland():
    env = {"XDG_SESSION_TYPE": "wayland"}
    assert paste.usable_tools(which_for("xdotool"), env) == []
    assert paste.paste_keys_for_status(which_for("xdotool"), env).startswith("no")
    assert paste.usable_tools(which_for("xdotool"), {"XDG_SESSION_TYPE": "x11"}) == ["xdotool"]
    with pytest.raises(ValueError):
        paste.tool_command("nope")


def test_tool_order_depends_on_the_session():
    which = which_for("xdotool", "ydotool", "wtype")
    assert paste.paste_tools(which, {"XDG_SESSION_TYPE": "wayland"}) == ["ydotool", "wtype", "xdotool"]
    assert paste.paste_tools(which, {"XDG_SESSION_TYPE": "x11"}) == ["xdotool", "ydotool", "wtype"]


def test_can_paste():
    assert paste.can_paste(which_for("xdotool"), {"XDG_SESSION_TYPE": "x11"}) is True
    assert paste.can_paste(which_for(), {}) is False


def test_send_paste_uses_the_first_working_tool():
    runner = RecordingRunner(returncodes=[1, 0])
    success, tool = paste.send_paste(which_for("xdotool", "ydotool"), runner, {"XDG_SESSION_TYPE": "x11"})
    assert success is True
    assert tool == "ydotool"
    assert [command[0] for command in runner.commands] == ["xdotool", "ydotool"]


def test_send_paste_reports_failure():
    runner = RecordingRunner(returncodes=[1, 1, 1])
    success, tool = paste.send_paste(which_for("xdotool", "ydotool"), runner, {"XDG_SESSION_TYPE": "x11"})
    assert success is False
    assert tool is None
    assert [command[0] for command in runner.commands] == ["xdotool", "ydotool", "ydotool"]


def test_send_paste_survives_crashing_tools():
    runner = RecordingRunner(explode=True)
    assert paste.send_paste(which_for("xdotool"), runner, {"XDG_SESSION_TYPE": "x11"}) == (False, None)


def test_send_paste_without_tools():
    assert paste.send_paste(which_for(), RecordingRunner(), {}) == (False, None)


def test_active_window_and_activation():
    runner = RecordingRunner()
    runner.returncodes = [0]

    def runner_with_output(command, **_kwargs):
        return subprocess.CompletedProcess(list(command), 0, b"12345\n", b"")

    assert paste.active_window(which_for("xdotool"), runner_with_output) == "12345"
    assert paste.active_window(which_for(), runner) is None
    assert paste.activate_window("12345", which_for("xdotool"), runner) is True
    assert paste.activate_window("", which_for("xdotool"), runner) is False
    assert paste.activate_window("1", which_for(), runner) is False


def test_active_window_handles_failures():
    def failing(command, **_kwargs):
        raise OSError("xdotool missing")

    assert paste.active_window(which_for("xdotool"), failing) is None


def test_status_description():
    assert paste.paste_keys_for_status(which_for(), {}).startswith("no")
    assert "yes" in paste.paste_keys_for_status(which_for("xdotool"), {"XDG_SESSION_TYPE": "x11"})


def test_setup_steps_only_install_what_is_missing(monkeypatch):
    """Nothing installed: the plan installs the tools, then starts the daemon."""
    monkeypatch.setattr(paste, "ydotoold_running", lambda *_a, **_k: False)
    steps = paste.setup_steps(
        which=lambda name: f"/usr/bin/{name}" if name == "systemctl" else None, user="ana"
    )
    commands = [" ".join(step.command) for step in steps]
    assert "apt-get install -y ydotool" in commands
    assert "usermod -aG input ana" in commands
    assert commands[-2:] == ["systemctl --user daemon-reload", "systemctl --user enable --now ydotoold"]
    assert all(step.sudo for step in steps if step.command[0] in {"apt-get", "usermod"})


def test_setup_steps_are_empty_when_everything_is_there(monkeypatch):
    monkeypatch.setattr(paste, "user_in_input_group", lambda _user=None: True)
    monkeypatch.setattr(paste, "ydotoold_running", lambda *_a, **_k: True)

    def which(name):
        return f"/usr/bin/{name}"

    assert paste.setup_steps(which=which, user="ana") == []


def test_setup_steps_skip_usermod_when_uinput_is_writable(monkeypatch):
    monkeypatch.setattr(paste, "uinput_writable", lambda *_a: True)
    monkeypatch.setattr(paste, "ydotoold_running", lambda *_a, **_k: True)
    steps = paste.setup_steps(which=lambda name: f"/usr/bin/{name}", user="ana")
    assert steps == []


def test_write_ydotoold_unit(tmp_path):
    target = tmp_path / "systemd" / "user" / "ydotoold.service"
    assert paste.write_ydotoold_unit(target) == target
    assert "ExecStart=/usr/bin/ydotoold" in target.read_text(encoding="utf-8")
    # a second run is a no-op, not an error
    assert paste.write_ydotoold_unit(target) == target


def test_run_setup_reports_each_step(tmp_path, monkeypatch):
    monkeypatch.setattr(paste, "user_in_input_group", lambda _user=None: True)
    monkeypatch.setattr(paste, "ydotoold_running", lambda *_a, **_k: False)
    runner = RecordingRunner(returncodes=[0, 1])
    results = paste.run_setup(
        which=lambda name: f"/usr/bin/{name}",
        runner=runner,
        unit_path=tmp_path / "ydotoold.service",
        user="ana",
    )
    assert [ok for _description, ok, _optional in results] == [True, False]
    assert [command[:3] for command in runner.commands] == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable"],
    ]
    assert (tmp_path / "ydotoold.service").exists()


def test_wtype_is_tested_not_assumed():
    """Mutter does not implement the virtual keyboard protocol: probe it."""

    def runner(_command, **_kwargs):
        return subprocess.CompletedProcess(
            ["wtype", ""], 1, b"", b"Compositor does not support the virtual keyboard protocol"
        )

    assert paste.wtype_supported(which_for("wtype"), runner) is False

    def working(_command, **_kwargs):
        return subprocess.CompletedProcess(["wtype", ""], 0, b"", b"")

    assert paste.wtype_supported(which_for("wtype"), working) is True
    assert paste.wtype_supported(which_for(), working) is None


def test_an_unusable_wtype_is_not_offered(monkeypatch):
    monkeypatch.setattr(paste, "wtype_supported", lambda *_a, **_k: False)
    assert paste.usable_tools(which_for("wtype"), {"XDG_SESSION_TYPE": "wayland"}) == []


def test_a_stale_socket_does_not_mean_the_daemon_runs(tmp_path):
    """ydotoold creates its socket *before* it opens /dev/uinput, so it survives a crash."""
    (tmp_path / ".ydotool_socket").write_text("")

    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(list(command), 1, b"", b"")

    assert (
        paste.ydotoold_running(which_for("ydotool", "pgrep"), runner, {"XDG_RUNTIME_DIR": str(tmp_path)})
        is False
    )


def test_paste_notes_explain_the_input_group(monkeypatch):
    monkeypatch.setattr(paste, "usable_tools", lambda *_a, **_k: [])
    monkeypatch.setattr(paste, "uinput_writable", lambda *_a: False)
    monkeypatch.setattr(paste, "user_in_input_group", lambda *_a: True)
    notes = " | ".join(paste.paste_notes(which_for("ydotool")))
    assert "log out and back in" in notes
    assert "chmod 666 /dev/uinput" in notes
    monkeypatch.setattr(paste, "user_in_input_group", lambda *_a: False)
    assert "not in the input group" in " | ".join(paste.paste_notes(which_for("ydotool")))


def test_paste_notes_are_empty_when_pasting_works(monkeypatch):
    monkeypatch.setattr(paste, "usable_tools", lambda *_a, **_k: ["ydotool"])
    assert paste.paste_notes() == []
