"""Paste simulation: helper selection and command construction."""

from __future__ import annotations

import subprocess

import pytest
from ubuntu_clipboard import paste


def which_for(*names: str):
    return lambda name: f"/usr/bin/{name}" if name in names else None


class RecordingRunner:
    def __init__(self, returncodes: list[int] | None = None, explode: bool = False):
        self.returncodes = list(returncodes or [])
        self.explode = explode
        self.commands: list[list[str]] = []

    def __call__(self, command, **_kwargs) -> subprocess.CompletedProcess:
        self.commands.append(list(command))
        if self.explode:
            raise OSError("tool vanished")
        code = self.returncodes.pop(0) if self.returncodes else 1
        return subprocess.CompletedProcess(list(command), code, b"", b"")


def test_tool_commands():
    assert paste.tool_command("xdotool") == ["xdotool", "key", "--clearmodifiers", "ctrl+v"]
    assert paste.tool_command("wtype") == ["wtype", "-M", "ctrl", "-P", "v", "-m", "ctrl"]
    assert paste.tool_command("ydotool") == ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"]
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
    assert runner.commands[0][0] == "xdotool"
    assert runner.commands[1][0] == "ydotool"


def test_send_paste_reports_failure():
    runner = RecordingRunner(returncodes=[1, 1])
    success, tool = paste.send_paste(which_for("xdotool", "ydotool"), runner, {"XDG_SESSION_TYPE": "x11"})
    assert success is False
    assert tool is None
    assert len(runner.commands) == 2


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
    assert paste.paste_keys_for_status(which_for(), {}).startswith("unavailable")
    assert "xdotool" in paste.paste_keys_for_status(which_for("xdotool"), {"XDG_SESSION_TYPE": "x11"})
