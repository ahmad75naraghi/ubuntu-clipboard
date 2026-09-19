"""Environment probing: session detection and helper discovery."""

from __future__ import annotations

from ubuntu_clipboard import clipboard


def which_for(*names: str):
    return lambda name: f"/usr/bin/{name}" if name in names else None


def test_detect_session_from_the_session_type():
    assert clipboard.detect_session({"XDG_SESSION_TYPE": "wayland"}) == "wayland"
    assert clipboard.detect_session({"XDG_SESSION_TYPE": "X11"}) == "x11"
    assert clipboard.detect_session({"XDG_SESSION_TYPE": "tty"}) == "unknown"


def test_detect_session_falls_back_to_the_display_variables():
    assert clipboard.detect_session({"WAYLAND_DISPLAY": "wayland-0"}) == "wayland"
    assert clipboard.detect_session({"DISPLAY": ":0"}) == "x11"
    assert clipboard.detect_session({"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-1"}) == "wayland"
    assert clipboard.detect_session({}) == "unknown"


def test_detect_session_uses_the_real_environment(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert clipboard.detect_session() == "wayland"


def test_available_tools_lists_every_helper():
    tools = clipboard.available_tools(which=which_for("wl-copy", "wl-paste", "xdotool"))
    assert set(tools) == {"wl-paste", "xclip", "xsel", "wl-copy", "xdotool", "wtype", "ydotool"}
    assert tools["wl-copy"] is True
    assert tools["wl-paste"] is True
    assert tools["xdotool"] is True
    assert tools["xclip"] is False
    assert all(isinstance(present, bool) for present in tools.values())


def test_capabilities_without_any_tool():
    capabilities = clipboard.probe(which=which_for(), env={"DISPLAY": ":0"})
    assert capabilities.session == "x11"
    assert capabilities.can_read_text is False
    assert capabilities.can_write_text is False
    assert capabilities.can_paste is False


def test_capabilities_with_a_read_only_setup():
    capabilities = clipboard.probe(which=which_for("wl-paste"), env={"XDG_SESSION_TYPE": "wayland"})
    assert capabilities.can_read_text is True
    assert capabilities.can_write_text is False
    assert capabilities.can_paste is False


def test_capabilities_with_a_complete_setup():
    capabilities = clipboard.probe(
        which=which_for("wl-paste", "wl-copy", "ydotool"),
        env={"XDG_SESSION_TYPE": "wayland"},
    )
    assert capabilities.can_read_text is True
    assert capabilities.can_write_text is True
    assert capabilities.can_paste is True


def test_capabilities_are_immutable():
    capabilities = clipboard.probe(which=which_for())
    assert capabilities.tools["xclip"] is False
    try:
        capabilities.session = "x11"
    except Exception as exc:  # dataclasses raise FrozenInstanceError
        assert "frozen" in str(exc).lower() or "cannot assign" in str(exc).lower()
    else:  # pragma: no cover - should not happen
        raise AssertionError("Capabilities should be frozen")
