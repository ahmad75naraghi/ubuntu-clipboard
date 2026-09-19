"""Environment capabilities: which clipboard/paste helpers are installed.

The application itself talks to the clipboard through GTK's
:class:`Gdk.Clipboard` — asynchronous, no external process and, because the
process stays resident, it keeps ownership of the selection. What is left here
is the *probing* half: session detection and tool discovery, used by
``--status`` (and by :mod:`ubuntu_clipboard.paste` to pick the right helper).

The v1 code shelled out to ``wl-paste``/``xclip`` on every poll. Writers such as
``wl-copy`` fork a child that keeps the inherited pipes open, so
``subprocess.run(capture_output=True)`` blocked until its timeout on *every*
copy; nothing in version 2.0 spawns those processes.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass

#: Helper binaries that can read the selection (in order of preference).
READER_TOOLS = ("wl-paste", "xclip", "xsel")
#: Helper binaries that can write/own the selection.
WRITER_TOOLS = ("wl-copy", "xclip", "xsel")
#: Helper binaries that can synthesise a paste.
PASTE_TOOLS = ("xdotool", "wtype", "ydotool")

Which = Callable[[str], str | None]


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


def probe(which: Which = shutil.which, env: dict[str, str] | None = None) -> Capabilities:
    """Detect the session type and the installed clipboard helpers."""
    return Capabilities(session=detect_session(env), tools=available_tools(which))
