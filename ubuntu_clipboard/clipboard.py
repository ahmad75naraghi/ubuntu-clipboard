"""Environment capabilities: which clipboard/paste helpers are installed.

The application itself talks to the clipboard through GTK's
:class:`Gdk.Clipboard` — asynchronous, no external process and, because the
process stays resident, it keeps ownership of the selection. What is left here
is the *probing* half: session detection and tool discovery, used by
``--status`` (and by :mod:`ubuntu_clipboard.paste` to pick the right helper).

The v1 code shelled out to ``wl-paste``/``xclip`` on every poll. Writers such as
``wl-copy`` fork a child that keeps the inherited pipes open, so
``subprocess.run(capture_output=True)`` blocked until its timeout on *every*
copy; the copy path never spawns them again. The one exception is reading the
password-manager marker, which the X11 selection cannot carry — that asks
``wl-paste --list-types`` once per copy, and only when the window runs through
XWayland.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

#: MIME type password managers use to mark a payload as "do not record this".
PASSWORD_HINT_MIME = "x-kde-passwordManagerHint"
#: Helper binaries that can read the selection (in order of preference).
READER_TOOLS = ("wl-paste", "xclip", "xsel")
#: Helper binaries that can write/own the selection.
WRITER_TOOLS = ("wl-copy", "xclip", "xsel")
#: Helper binaries that can synthesise a paste.
PASTE_TOOLS = ("xdotool", "wtype", "ydotool")

Which = Callable[[str], str | None]
Runner = Callable[..., object]


def detect_session(env: dict[str, str] | None = None) -> str:
    """``wayland``, ``x11`` or ``unknown``."""
    environment = os.environ if env is None else env
    session = environment.get("XDG_SESSION_TYPE", "").lower()
    if session in {"wayland", "x11"}:
        return session
    if environment.get("WAYLAND_DISPLAY"):
        return "wayland"
    if environment.get("DISPLAY"):
        return "x11"
    return "unknown"


def available_tools(which: Which = shutil.which) -> dict[str, bool]:
    """Which helper binaries are installed, used by ``--status``."""
    return {name: bool(which(name)) for name in (*READER_TOOLS, *WRITER_TOOLS, *PASTE_TOOLS)}


@dataclass(frozen=True)
class Capabilities:
    """Result of probing the environment, printed by ``--status``."""

    session: str
    tools: dict[str, bool]

    @property
    def can_read_text(self) -> bool:
        return any(self.tools.get(name, False) for name in READER_TOOLS)

    @property
    def can_write_text(self) -> bool:
        return any(self.tools.get(name, False) for name in WRITER_TOOLS)

    @property
    def can_paste(self) -> bool:
        return any(self.tools.get(name, False) for name in PASTE_TOOLS)


def wayland_offers_password_hint(
    which: Which = shutil.which,
    runner: Runner = subprocess.run,
    env: dict[str, str] | None = None,
) -> bool:
    """Whether the Wayland side marks the current selection as a secret.

    Needed only when the application itself reads the clipboard through XWayland
    (the ``hide_from_dock`` backend): the X11 selection carries no custom MIME
    types, so the password marker would be invisible there and a password would
    quietly end up in the history. ``wl-paste --list-types`` asks the Wayland
    side directly, and answers in a few milliseconds.
    """
    if detect_session(env) != "wayland" or not which("wl-paste"):
        return False
    try:
        result = runner(
            ["wl-paste", "--list-types"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=1.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0 or not result.stdout:
        return False
    types = result.stdout.decode("utf-8", errors="ignore").lower()
    return PASSWORD_HINT_MIME.lower() in types


def probe(which: Which = shutil.which, env: dict[str, str] | None = None) -> Capabilities:
    """Detect the session type and the installed clipboard helpers."""
    return Capabilities(session=detect_session(env), tools=available_tools(which))
