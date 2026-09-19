"""Paste simulation (``Ctrl+V``) after the user picks an item.

There is no portable way to synthesise keystrokes on Linux: ``wtype`` needs a
compositor implementing the virtual keyboard protocol (GNOME's Mutter does not),
``ydotool`` needs the uinput daemon, and ``xdotool`` only reaches X11 clients.
The helpers are therefore probed at runtime and the caller falls back to a
notification ("press Ctrl+V yourself") when none of them worked.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .clipboard import detect_session

log = logging.getLogger(__name__)

Command = list[str]
Which = Callable[[str], str | None]
Runner = Callable[..., subprocess.CompletedProcess]

#: Sleeping between clipboard read-backs; tests replace it.
SLEEP: Callable[[float], None] = time.sleep

#: Ordered by reliability on GNOME.
WAYLAND_TOOLS = ("ydotool", "wtype", "xdotool")
X11_TOOLS = ("xdotool", "ydotool", "wtype")

#: ydotool 0.1.x — the version Ubuntu 24.04 ships — takes key *names*.
YDOTOOL_NAMES: Command = ["ydotool", "key", "ctrl+v"]
#: ydotool 1.x takes raw Linux keycodes (KEY_LEFTCTRL=29, KEY_V=47).
YDOTOOL_CODES: Command = ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"]


def ydotool_uses_keycodes(which: Which = shutil.which, runner: Runner = subprocess.run) -> bool | None:
    """``True`` for ydotool >= 1.0, ``False`` for 0.1.x, ``None`` if unknown.

    The two versions are not compatible: 0.1.x (Ubuntu 24.04) reads key names
    like ``ctrl+v``, 1.x reads keycodes like ``29:1``. Sending the wrong form
    either does nothing or types garbage, so the version decides the command.
    """
    if not which("ydotool"):
        return None
    try:
        result = runner(
            ["ydotool", "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result is None:
        return None
    text = (result.stdout or b"").decode("utf-8", errors="replace")
    text += (result.stderr or b"").decode("utf-8", errors="replace")
    match = re.search(r"(\d+)\.(\d+)", text)
    if not match:
        return None
    return int(match.group(1)) >= 1


def tool_command(tool: str, keys: str = "ctrl+v") -> Command:
    """The argv used to press ``Ctrl+V`` with ``tool``."""
    if tool == "ydotool":
        return list(YDOTOOL_NAMES)
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


def paste_candidates(
    tool: str,
    which: Which = shutil.which,
    runner: Runner = subprocess.run,
) -> list[Command]:
    """Commands to try for ``tool``, most likely to work first."""
    if tool != "ydotool":
        return [tool_command(tool)]
    if ydotool_uses_keycodes(which, runner) is True:
        return [list(YDOTOOL_CODES), list(YDOTOOL_NAMES)]
    return [list(YDOTOOL_NAMES), list(YDOTOOL_CODES)]


def wtype_supported(which: Which = shutil.which, runner: Runner = subprocess.run) -> bool | None:
    """Whether the compositor implements the virtual keyboard protocol.

    wtype needs ``zwp_virtual_keyboard_manager_v1``; Mutter (GNOME) does not
    implement it, so an installed wtype that is never actually usable is the
    norm on Ubuntu. ``None`` means "could not tell".
    """
    if not which("wtype"):
        return None
    try:
        result = runner(
            ["wtype", ""],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result is None:
        return None
    if result.returncode == 0:
        return True
    text = (result.stderr or b"").decode("utf-8", errors="replace")
    if "virtual keyboard protocol" in text:
        return False
    return None


def ydotoold_running(
    which: Which = shutil.which,
    runner: Runner = subprocess.run,
    env: dict[str, str] | None = None,
) -> bool | None:
    """Whether ``ydotoold`` is up. ``None`` = cannot tell.

    Without the daemon ydotool still tries to open ``/dev/uinput`` itself,
    which only works for root or members of ``input``: a missing daemon is the
    usual reason a "working" ydotool types nothing.
    """
    if not which("ydotool"):
        return None
    environment = env if env is not None else os.environ
    if which("pgrep"):
        # Ask the process table first: a socket file outlives a crashed daemon
        # (ydotoold creates it before it opens /dev/uinput), and reporting a
        # dead daemon as running hides exactly the problem the user has.
        try:
            result = runner(
                ["pgrep", "-x", "ydotoold"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            result = None
        if result is not None:
            return result.returncode == 0
    sockets = [Path(environment.get("YDOTOOL_SOCKET", "")) if environment.get("YDOTOOL_SOCKET") else None]
    runtime = environment.get("XDG_RUNTIME_DIR")
    if runtime:
        sockets.append(Path(runtime) / ".ydotool_socket")
    sockets.append(Path("/tmp/.ydotool_socket"))
    if any(path is not None and path.exists() for path in sockets):
        return True
    return None


def send_paste(
    which: Which = shutil.which,
    runner: Runner = subprocess.run,
    env: dict[str, str] | None = None,
    timeout: float = 3.0,
) -> tuple[bool, str | None]:
    """Try every available helper and every syntax it might need."""
    for tool in paste_tools(which, env):
        for command in paste_candidates(tool, which, runner):
            try:
                result = runner(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=timeout,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                log.debug("%s failed: %s", " ".join(command), exc)
                continue
            if result.returncode == 0:
                log.debug("paste sent with %s", " ".join(command))
                return True, tool
            log.debug("%s exited with %s", " ".join(command), result.returncode)
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


def usable_tools(
    which: Which = shutil.which,
    env: dict[str, str] | None = None,
    runner: Runner = subprocess.run,
) -> list[str]:
    """Installed helpers that can actually reach the focused window."""
    session = detect_session(env)
    usable: list[str] = []
    for tool in paste_tools(which, env):
        if tool == "xdotool" and session == "wayland":
            continue  # XTEST only reaches X11/XWayland clients
        if tool == "wtype" and wtype_supported(which, runner) is False:
            continue  # Mutter does not implement the virtual keyboard protocol
        if tool == "ydotool" and ydotoold_running(which, runner, env) is False and not uinput_writable():
            continue  # no daemon and no permission to open /dev/uinput either
        usable.append(tool)
    return usable


def paste_keys_for_status(
    which: Which = shutil.which,
    env: dict[str, str] | None = None,
    runner: Runner = subprocess.run,
) -> str:
    """Human readable description for ``--status``."""
    tools = paste_tools(which, env)
    usable = usable_tools(which, env, runner)
    if usable:
        return f"yes via {usable[0]}"
    if tools:
        return f"no — {', '.join(tools)} installed but cannot reach the focused window (run --setup-paste)"
    return "no — nothing installed; run --setup-paste for one-step setup"


def paste_notes(
    which: Which = shutil.which,
    env: dict[str, str] | None = None,
    runner: Runner = subprocess.run,
) -> list[str]:
    """Why automatic pasting is off, and the shortest way to switch it on."""
    notes: list[str] = []
    if usable_tools(which, env, runner):
        return notes
    if which("ydotool") is None:
        notes.append("ydotool is not installed — run: ubuntu-clipboard --setup-paste")
        return notes
    if uinput_writable() is False:
        if user_in_input_group():
            notes.append("the input group is set, but this session predates it")
        else:
            notes.append("this user is not in the input group")
        notes.append("log out and back in once to apply it")
        notes.append("right now, without logging out: sudo chmod 666 /dev/uinput")
        notes.append("then: systemctl --user restart ydotoold")
        return notes
    if ydotoold_running(which, runner, env) is False:
        notes.append("ydotoold is not running — start it: systemctl --user enable --now ydotoold")
    return notes


def clipboard_holds_text(
    text: str,
    which: Which = shutil.which,
    env: dict[str, str] | None = None,
    runner: Runner = subprocess.run,
    timeout: float = 3.0,
) -> bool:
    """Whether the selection currently holds exactly ``text``."""
    return read_clipboard_text(which, runner, env, timeout) == text


def paste_hint(which: Which = shutil.which, env: dict[str, str] | None = None) -> str:
    """The sentence shown when automatic pasting is not possible."""
    if usable_tools(which, env):
        return "notify.copied"
    return "notify.copied_manual"


__all__: Sequence[str] = (
    "Step",
    "activate_window",
    "clipboard_holds_text",
    "ensure_clipboard_text",
    "read_clipboard_text",
    "read_clipboard_text_all",
    "read_with",
    "selection_agrees",
    "write_clipboard_text",
    "active_window",
    "can_paste",
    "paste_candidates",
    "paste_hint",
    "paste_keys_for_status",
    "paste_tools",
    "run_setup",
    "send_paste",
    "setup_steps",
    "tool_command",
    "uinput_writable",
    "usable_tools",
    "ydotoold_running",
    "ydotoold_unit_path",
    "write_ydotoold_unit",
)

# ── automatic paste setup (Ubuntu ships ydotool without a daemon unit) ─────
# ── clipboard writing & verification ──────────────────────────────────────
#: Writers that keep the selection alive after we exit (they fork a child).
CLIPBOARD_WRITERS = ("wl-copy", "xclip")
#: Readers that print the selection and exit.
CLIPBOARD_READERS = ("wl-paste", "xclip", "xsel")
#: Writing the clipboard and pressing Ctrl+V are two asynchronous systems; a
#: receiving application (Java/AWT ones especially) can read the *old* content
#: if the new selection has not been published yet. So the write is verified.
VERIFY_ATTEMPTS = 10
VERIFY_PAUSE_SECONDS = 0.05


def writer_command(tool: str) -> Command:
    """argv of a clipboard writer (the payload arrives on stdin)."""
    if tool == "wl-copy":
        return ["wl-copy", "--type", "text/plain;charset=utf-8"]
    if tool == "xclip":
        return ["xclip", "-selection", "clipboard", "-in"]
    if tool == "xsel":
        return ["xsel", "--clipboard", "--input"]
    raise ValueError(f"unknown clipboard writer: {tool}")


def reader_command(tool: str, no_newline: bool = True) -> Command:
    """argv of a clipboard reader (the payload is printed to stdout)."""
    if tool == "wl-paste":
        return ["wl-paste", "--no-newline"] if no_newline else ["wl-paste"]
    if tool == "xclip":
        return ["xclip", "-selection", "clipboard", "-o"]
    if tool == "xsel":
        return ["xsel", "--clipboard", "--output"]
    raise ValueError(f"unknown clipboard reader: {tool}")


def _session_order(tools: Sequence[str], env: dict[str, str] | None) -> list[str]:
    order = list(tools)
    if detect_session(env) == "x11":
        order.reverse()
    return order


def write_clipboard_text(
    text: str,
    which: Which = shutil.which,
    env: dict[str, str] | None = None,
    popen: Callable[..., object] | None = None,
) -> str | None:
    """Publish ``text`` on **every** clipboard side that exists.

    ``wl-copy``/``xclip`` fork a child that owns the selection and inherit our
    pipes, so they are started with ``Popen`` and deliberately never waited for
    (waiting is what made the v1 code hang); closing stdin publishes the value.

    GNOME always runs XWayland, and X11 clients — Java applications such as
    Android Studio above all — read the *X11* selection. When only the Wayland
    side is published they can hand the application the previous content, which
    is how "it pastes the last item instead of the one I chose" happens. Both
    sides are therefore written, and the first helper used is returned.
    """
    starter = popen or subprocess.Popen
    environment = env if env is not None else os.environ
    used: str | None = None
    for tool in _session_order(CLIPBOARD_WRITERS, env):
        if not which(tool):
            continue
        if tool == "xclip" and not environment.get("DISPLAY"):
            continue  # no X server (or XWayland) to talk to
        try:
            process = starter(
                writer_command(tool),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            assert process.stdin is not None  # noqa: S101 - set by stdin=PIPE
            process.stdin.write(text.encode("utf-8"))
            process.stdin.close()
        except (OSError, AttributeError, ValueError) as exc:
            log.debug("%s could not take the clipboard: %s", tool, exc)
            continue
        log.debug("clipboard written with %s (%d chars)", tool, len(text))
        used = used or tool
    return used


def read_clipboard_text(
    which: Which = shutil.which,
    runner: Runner = subprocess.run,
    env: dict[str, str] | None = None,
    timeout: float = 3.0,
) -> str | None:
    """The selection as text, or ``None`` when no helper could read it."""
    for tool in _session_order(CLIPBOARD_READERS, env):
        if which(tool):
            return read_with(tool, runner, timeout)
    return None


def read_clipboard_text_all(
    which: Which = shutil.which,
    runner: Runner = subprocess.run,
    env: dict[str, str] | None = None,
    timeout: float = 3.0,
) -> dict[str, str | None]:
    """What each installed side of the clipboard shows (``None`` = unreadable).

    ``wl-paste`` answers for the Wayland side, ``xclip``/``xsel`` for the X11
    (XWayland) side. They are supposed to agree; a disagreement is exactly the
    stale content an X11 application would paste.
    """
    environment = env if env is not None else os.environ
    state: dict[str, str | None] = {}
    for tool in _session_order(CLIPBOARD_READERS, env):
        if not which(tool) or (tool in {"xclip", "xsel"} and not environment.get("DISPLAY")):
            continue
        state[tool] = read_with(tool, runner, timeout)
    return state


def read_with(
    tool: str,
    runner: Runner = subprocess.run,
    timeout: float = 3.0,
) -> str | None:
    """Read the selection with one specific helper (``None`` when it cannot).

    ``wl-paste`` gained ``--no-newline`` in wl-clipboard 2.0, so the plain form
    (which appends one newline) is kept as a fallback.
    """
    for no_newline in (True, False) if tool == "wl-paste" else (True,):
        argv = reader_command(tool, no_newline)
        try:
            result = runner(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("%s could not read the clipboard: %s", tool, exc)
            return None
        if result is not None and result.returncode == 0:
            text = (result.stdout or b"").decode("utf-8", errors="replace")
            # ``wl-paste`` without ``--no-newline`` (old wl-clipboard) adds one.
            return text[:-1] if (not no_newline and text.endswith("\n")) else text
    return None


def selection_agrees(
    text: str,
    which: Which = shutil.which,
    runner: Runner = subprocess.run,
    env: dict[str, str] | None = None,
) -> bool:
    """Whether every readable side of the clipboard holds exactly ``text``."""
    state = read_clipboard_text_all(which, runner, env)
    answers = [value for value in state.values() if value is not None]
    return bool(answers) and all(value == text for value in answers)


def ensure_clipboard_text(
    text: str,
    which: Which = shutil.which,
    env: dict[str, str] | None = None,
    runner: Runner = subprocess.run,
    popen: Callable[..., object] | None = None,
    attempts: int = VERIFY_ATTEMPTS,
    pause: float = VERIFY_PAUSE_SECONDS,
    sleep: Callable[[float], None] | None = None,
) -> tuple[bool, str | None]:
    """Make sure the clipboard *really* holds ``text`` before a paste is sent.

    Returns ``(verified, tool)``: ``tool`` is the helper used for a re-write when
    the first (GTK) write had not been published yet.
    """
    sleeper = sleep or SLEEP
    for _ in range(max(1, attempts)):
        if selection_agrees(text, which, runner, env):
            return True, None
        sleeper(pause)
    tool = write_clipboard_text(text, which, env, popen)
    if tool is None:
        return False, None
    for _ in range(max(1, attempts)):
        if selection_agrees(text, which, runner, env):
            return True, tool
        sleeper(pause)
    return False, tool


#: Debian/Ubuntu split the daemon into its own package and ship no unit file,
#: so the user service has to be written by hand.
YDOTOOLD_UNIT = """[Unit]
Description=ydotoold - ydotool daemon
Documentation=man:ydotoold(8)

[Service]
ExecStart=/usr/bin/ydotoold
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""


@dataclass(frozen=True)
class Step:
    """One command of the setup, described for the user before it runs."""

    description: str
    command: tuple[str, ...]
    sudo: bool = False
    optional: bool = False
    relogin: bool = False


def current_user(env: dict[str, str] | None = None) -> str:
    environment = env if env is not None else os.environ
    return environment.get("USER") or environment.get("LOGNAME") or ""


def user_in_input_group(user: str | None = None) -> bool:
    """Whether ``user`` may already talk to ``/dev/uinput`` through the group."""
    name = user or current_user()
    if not name:
        return False
    try:
        import grp

        return name in grp.getgrnam("input").gr_mem
    except (KeyError, OSError):
        return False


def ydotoold_unit_path(env: dict[str, str] | None = None) -> Path:
    environment = env if env is not None else os.environ
    base = environment.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user" / "ydotoold.service"


def write_ydotoold_unit(path: Path | None = None, env: dict[str, str] | None = None) -> Path | None:
    """Create ``ydotoold.service`` for the user session. ``None`` on failure."""
    target = path or ydotoold_unit_path(env)
    try:
        if target.exists() and "ydotoold" in target.read_text(encoding="utf-8"):
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(YDOTOOLD_UNIT, encoding="utf-8")
    except OSError as exc:
        log.debug("cannot write %s: %s", target, exc)
        return None
    return target


def setup_steps(
    which: Which = shutil.which,
    env: dict[str, str] | None = None,
    user: str | None = None,
) -> list[Step]:
    """The commands that enable automatic pasting on GNOME/Wayland."""
    environment = env if env is not None else os.environ
    name = user or environment.get("USER") or environment.get("LOGNAME") or ""
    steps: list[Step] = []
    if which("ydotool") is None:
        steps.append(
            Step("install ydotool (keyboard simulation)", ("apt-get", "install", "-y", "ydotool"), sudo=True)
        )
    if which("ydotoold") is None:
        steps.append(
            Step(
                "install ydotoold (the daemon that owns /dev/uinput)",
                ("apt-get", "install", "-y", "ydotoold"),
                sudo=True,
                optional=True,
            )
        )
    if which("xdotool") is None:
        steps.append(
            Step(
                "install xdotool (reaches X11/XWayland windows such as IDEs)",
                ("apt-get", "install", "-y", "xdotool"),
                sudo=True,
                optional=True,
            )
        )
    needs_input_group = name and not uinput_writable() and not user_in_input_group(name)
    if needs_input_group and (which("usermod") or Path("/usr/sbin/usermod").exists()):
        steps.append(
            Step(
                f"allow {name} to use /dev/uinput",
                ("usermod", "-aG", "input", name),
                sudo=True,
                relogin=True,
            )
        )
    if which("systemctl") and not ydotoold_running(which):
        steps.append(Step("reload the user services", ("systemctl", "--user", "daemon-reload")))
        steps.append(
            Step(
                "start ydotoold now and at every login",
                ("systemctl", "--user", "enable", "--now", "ydotoold"),
            )
        )
    return steps


def run_setup(
    which: Which = shutil.which,
    runner: Runner = subprocess.run,
    env: dict[str, str] | None = None,
    user: str | None = None,
    unit_path: Path | None = None,
) -> list[tuple[str, bool, bool]]:
    """Run :func:`setup_steps`. Returns ``(description, ok, optional)`` per step.

    A failing optional step (a package that does not exist on this release) is
    not an error — the important ones are.
    """
    write_ydotoold_unit(unit_path, env)
    results: list[tuple[str, bool, bool]] = []
    for step in setup_steps(which, env, user):
        argv = list(step.command)
        command = ["sudo", *argv] if step.sudo else argv
        ok = False
        try:
            # The output is deliberately *not* captured: `sudo` asks for the
            # password on the terminal and apt prints progress the user can see.
            result = runner(command, timeout=600, check=False)
            ok = result is not None and result.returncode == 0
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("setup step failed: %s (%s)", " ".join(command), exc)
        results.append((step.description, ok, step.optional))
    return results


def uinput_writable(path: str = "/dev/uinput") -> bool:
    """Whether this user may write to ``/dev/uinput`` (needs the input group)."""
    return os.access(path, os.W_OK)
