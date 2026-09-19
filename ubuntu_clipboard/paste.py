"""Paste simulation (``Ctrl+V``) after the user picks an item.

There is no portable way to synthesise keystrokes on Linux: ``wtype`` needs a
compositor implementing the virtual keyboard protocol (GNOME's Mutter does not),
``ydotool`` needs the uinput daemon, and ``xdotool`` only reaches X11 clients.
The helpers are therefore probed at runtime and the caller falls back to a
notification ("press Ctrl+V yourself") when none of them worked.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Callable, Sequence

from .clipboard import detect_session

log = logging.getLogger(__name__)

Command = list[str]
Which = Callable[[str], str | None]
Runner = Callable[..., subprocess.CompletedProcess]

#: Ordered by reliability on GNOME.
WAYLAND_TOOLS = ("ydotool", "wtype", "xdotool")
X11_TOOLS = ("xdotool", "ydotool", "wtype")

#: ``Ctrl+V`` as a ydotool key sequence (KEY_LEFTCTRL=29, KEY_V=47).
YDOTOOL_PASTE: Command = ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"]


def tool_command(tool: str, keys: str = "ctrl+v") -> Command:
    """The argv used to press ``Ctrl+V`` with ``tool``."""
    if tool == "ydotool":
        return list(YDOTOOL_PASTE)
    if tool == "wtype":
        return ["wtype", "-M", "ctrl", "-P", "v", "-m", "ctrl"]
    if tool == "xdotool":
        return ["xdotool", "key", "--clearmodifiers", keys]
    raise ValueError(f"unknown paste tool: {tool}")


def paste_tools(which: Which = shutil.which, env: dict[str, str] | None = None) -> list[str]:
    """Installed helpers, most promising first for the current session."""
    order = WAYLAND_TOOLS if detect_session(env) == "wayland" else X11_TOOLS
    return [tool for tool in order if which(tool)]


def can_paste(which: Which = shutil.which, env: dict[str, str] | None = None) -> bool:
    return bool(paste_tools(which, env))


def send_paste(
    which: Which = shutil.which,
    runner: Runner = subprocess.run,
    env: dict[str, str] | None = None,
    timeout: float = 3.0,
) -> tuple[bool, str | None]:
    """Try every available helper. Returns ``(success, tool)``."""
    for tool in paste_tools(which, env):
        command = tool_command(tool)
        try:
            result = runner(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("%s failed: %s", tool, exc)
            continue
        if result.returncode == 0:
            log.debug("paste sent with %s", tool)
            return True, tool
        log.debug("%s exited with %s", tool, result.returncode)
    log.info("no paste helper succeeded — the user has to press Ctrl+V")
    return False, None


# ── X11 focus restoration ──────────────────────────────────────────────────
def active_window(
    which: Which = shutil.which,
    runner: Runner = subprocess.run,
) -> str | None:
    """Window id of the currently active X11/XWayland window."""
    if not which("xdotool"):
        return None
    try:
        result = runner(
            ["xdotool", "getactivewindow"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout.decode("ascii", errors="ignore").strip() or None


def activate_window(
    window_id: str,
    which: Which = shutil.which,
    runner: Runner = subprocess.run,
) -> bool:
    """Give focus back to ``window_id`` (X11 only; Wayland handles this itself)."""
    if not window_id or not which("xdotool"):
        return False
    try:
        result = runner(
            ["xdotool", "windowactivate", "--sync", window_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def paste_keys_for_status(which: Which = shutil.which, env: dict[str, str] | None = None) -> str:
    """Human readable description for ``--status``."""
    tools = paste_tools(which, env)
    if not tools:
        return "unavailable (install ydotool, wtype or xdotool)"
    return f"yes via {tools[0]}"


__all__: Sequence[str] = (
    "activate_window",
    "active_window",
    "can_paste",
    "paste_keys_for_status",
    "paste_tools",
    "send_paste",
    "tool_command",
)
