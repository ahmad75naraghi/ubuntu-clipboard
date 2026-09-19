"""GNOME custom shortcut management.

The v1 installer manipulated the ``custom-keybindings`` list with ``sed`` and
kept a special case for a stray ``custom1`` entry. Here the list is parsed,
filtered and rebuilt in Python, which is both shorter and correct: any binding
that already points at this application is removed before ours is added, so
repeated installs stay idempotent.
"""

from __future__ import annotations

import ast
import logging
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
KEY = "custom-keybindings"
#: The list of bindings lives in SCHEMA, which is *not* relocatable, so
#: ``$SCHEMA:<path>`` is rejected by gsettings. ``name``, ``command`` and
#: ``binding`` belong to the relocatable child schema instead:
#: ``org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:<path>``.
CHILD_SCHEMA = f"{SCHEMA}.custom-keybinding"
BASE_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
SLOT = "ubuntu-clipboard"
KEY_PATH = f"{BASE_PATH}{SLOT}/"
DEFAULT_BINDING = "<Super>v"

SHELL_SCHEMA = "org.gnome.shell.keybindings"
SHELL_TOGGLE_KEY = "toggle-message-tray"

#: Commands that mean "this binding belongs to us".
OWNED_MARKERS = ("ubuntu-clipboard", "ubuntu_clipboard")
#: Clipboard managers that are regularly bound to the same key (diodon ships no
#: hotkey of its own, but every tutorial tells users to make a custom shortcut
#: for it, usually <Super>v).
RIVAL_CLIPBOARDS = (
    "diodon",
    "copyq",
    "clipit",
    "parcellite",
    "gpaste",
    "clipman",
    "cliphist",
    "greenclip",
    "clipboard-indicator",
)
#: ``gsettings`` is a session bus round trip; it can be slow on a loaded machine.
SET_TIMEOUT = 10.0

#: The systemd user unit that owns the custom keybindings.
MEDIA_KEYS_UNIT = "org.gnome.SettingsDaemon.MediaKeys"

Runner = Callable[..., subprocess.CompletedProcess]


class ShortcutError(RuntimeError):
    """Raised when ``gsettings`` is missing or refuses a value."""


@dataclass
class Report:
    """Outcome of an install/uninstall run, printed by the CLI."""

    ok: bool = False
    binding: str = DEFAULT_BINDING
    command: str = ""
    removed: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)
    #: The user's own shortcuts that already use our binding (we never edit them).
    clashing: list[str] = field(default_factory=list)
    #: Paths we removed because the caller asked for the binding (``--take-binding``).
    took: list[str] = field(default_factory=list)
    #: What the shortcut daemon reload reported, if it was asked to reload.
    reloaded: str | None = None
    messages: list[str] = field(default_factory=list)

    def add(self, message: str) -> None:
        self.messages.append(message)
        log.info(message)


def _run(
    command: Sequence[str],
    runner: Runner = subprocess.run,
    timeout: float = 5.0,
) -> subprocess.CompletedProcess | None:
    try:
        return runner(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("gsettings call failed: %s", exc)
        return None


def gsettings_available(which: Callable[[str], str | None] | None = None) -> bool:
    """Whether the ``gsettings`` binary can be found (resolved at call time)."""
    finder = which or shutil.which
    return bool(finder("gsettings"))


def get_value(
    schema: str,
    key: str,
    path: str | None = None,
    runner: Runner = subprocess.run,
) -> str | None:
    target = f"{schema}:{path}" if path else schema
    result = _run(["gsettings", "get", target, key], runner)
    if result is None or result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip()


def set_value_checked(
    schema: str,
    key: str,
    value: str,
    path: str | None = None,
    runner: Runner = subprocess.run,
) -> str | None:
    """Set a key; return ``None`` on success or a human readable reason."""
    target = f"{schema}:{path}" if path else schema
    try:
        result = runner(
            ["gsettings", "set", target, key, value],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=SET_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        return "gsettings is not installed"
    except subprocess.TimeoutExpired:
        return f"gsettings did not answer within {SET_TIMEOUT:g}s"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"gsettings could not be run: {exc}"
    if result is None:  # a runner that reported the failure instead of raising
        return "gsettings could not be run"
    if result.returncode == 0:
        return None
    message = result.stderr.decode("utf-8", errors="replace").strip()
    return message or f"gsettings exited with status {result.returncode}"


def set_value(
    schema: str,
    key: str,
    value: str,
    path: str | None = None,
    runner: Runner = subprocess.run,
) -> bool:
    return set_value_checked(schema, key, value, path, runner) is None


def parse_list(raw: str | None) -> list[str]:
    """Parse a GVariant ``as`` value such as ``['/a/', '/b/']``."""
    if not raw:
        return []
    text = raw.strip()
    if text in {"@as []", "[]", ""}:
        return []
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        log.warning("cannot parse keybinding list %r", raw)
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if isinstance(item, (str, int, float))]
    return []


def quote(value: str) -> str:
    """Render a Python string as a GVariant string literal."""
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def format_list(paths: Sequence[str]) -> str:
    """Render a list as a GVariant ``as`` literal."""
    if not paths:
        return "@as []"
    inner = ", ".join(f"'{path}'" for path in paths)
    return f"[{inner}]"


def binding_command(path: str, runner: Runner = subprocess.run) -> str:
    return get_value(CHILD_SCHEMA, "command", path, runner) or ""


def owns_binding(path: str, runner: Runner = subprocess.run) -> bool:
    command = binding_command(path, runner)
    return any(marker in command for marker in OWNED_MARKERS)


def resolve_launch_command(
    explicit: str | None = None,
    which: Callable[[str], str | None] | None = None,
) -> list[str]:
    """Absolute command used by the desktop entry and the shortcut."""
    if explicit:
        return [explicit]
    finder = which or shutil.which
    found = finder("ubuntu-clipboard")
    if found:
        return [found]
    local = Path.home() / ".local" / "bin" / "ubuntu-clipboard"
    if local.is_file():
        return [str(local)]
    sibling = Path(sys.executable).parent / "ubuntu-clipboard"
    if sibling.is_file():
        return [str(sibling)]
    # Last resort: run the package with the interpreter that is executing us.
    return [sys.executable, "-m", "ubuntu_clipboard"]


def conflicts(binding: str = DEFAULT_BINDING, runner: Runner = subprocess.run) -> list[str]:
    """GNOME shortcuts that would swallow ``binding`` before we see it."""
    found: list[str] = []
    raw = get_value(SHELL_SCHEMA, SHELL_TOGGLE_KEY, runner=runner)
    if raw and binding in raw:
        found.append(f"{SHELL_SCHEMA} {SHELL_TOGGLE_KEY}")
    return found


def unquote(value: str | None) -> str:
    """``"'<Super>v'"`` -> ``"<Super>v"`` (gsettings prints GVariant strings)."""
    if not value:
        return ""
    text = value.strip().lstrip("@as ")
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def foreign_bindings(
    binding: str = DEFAULT_BINDING, runner: Runner = subprocess.run
) -> list[tuple[str, str]]:
    """``(path, command)`` of the user's own shortcuts that already use ``binding``.

    The desktop environment runs whichever of the two it reaches first, so one
    of them never fires — usually the reason "Win+V does nothing".
    """
    found: list[tuple[str, str]] = []
    for path in parse_list(get_value(SCHEMA, KEY, runner=runner)):
        if path == KEY_PATH:
            continue
        if unquote(get_value(CHILD_SCHEMA, "binding", path, runner)) != binding:
            continue
        found.append((path, unquote(get_value(CHILD_SCHEMA, "command", path, runner))))
    return found


def describe_foreign(path: str, command: str) -> str:
    """Human readable description of a conflicting shortcut."""
    return f"{path} ({command})" if command else path


def rival_name(command: str) -> str | None:
    """Name of the other clipboard manager behind ``command``, if it is one."""
    lowered = command.lower()
    return next((name for name in RIVAL_CLIPBOARDS if name in lowered), None)


def custom_conflicts(binding: str = DEFAULT_BINDING, runner: Runner = subprocess.run) -> list[str]:
    """Other custom shortcuts (someone else's) already bound to ``binding``."""
    return [describe_foreign(path, command) for path, command in foreign_bindings(binding, runner)]


def media_keys_units() -> tuple[str, ...]:
    """systemd user units that own GNOME's keyboard shortcuts."""
    return ("org.gnome.SettingsDaemon.MediaKeys", "org.gnome.SettingsDaemon.Keyboard")


def refresh_media_keys(
    runner: Runner = subprocess.run,
    which: Callable[[str], str | None] | None = None,
) -> str | None:
    """Ask the shortcut daemon to reload, so the new key works immediately.

    ``gnome-settings-daemon`` watches the keybinding list, but in practice a
    freshly written binding is sometimes ignored until the plugin restarts;
    restarting it avoids a log out. Returns what happened, or ``None``.
    """
    finder = which or shutil.which
    if finder("systemctl"):
        unit = media_keys_units()[0]
        try:
            result = runner(
                ["systemctl", "--user", "restart", unit],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=SET_TIMEOUT,
                check=False,
            )
            if result is not None and result.returncode == 0:
                return f"reloaded {unit}"
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("systemctl could not restart %s: %s", unit, exc)
    if finder("pkill"):
        try:
            runner(
                ["pkill", "-f", "gsd-media-keys"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=SET_TIMEOUT,
                check=False,
            )
            return "restarted gsd-media-keys (the shell starts it again)"
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("pkill failed: %s", exc)
    return None


def remove_paths(paths: Sequence[str], runner: Runner = subprocess.run) -> list[str]:
    """Drop ``paths`` from the keybinding list; returns the ones that were there."""
    registered = parse_list(get_value(SCHEMA, KEY, runner=runner))
    keep = [path for path in registered if path not in set(paths)]
    removed = [path for path in registered if path in set(paths)]
    if removed and not set_value(SCHEMA, KEY, format_list(keep), runner=runner):
        log.warning("could not remove %s from %s", removed, KEY)
        return []
    return removed


def _disable_shell_conflict(runner: Runner) -> bool:
    return set_value(SHELL_SCHEMA, SHELL_TOGGLE_KEY, "@as []", runner=runner)


def daemon_log(lines: int = 15, runner: Runner = subprocess.run) -> list[str]:
    """Tail of the media-keys plugin log — it says whether a key was registered.

    Empty when ``journalctl`` is missing or has nothing to show.
    """
    if shutil.which("journalctl") is None:
        return []
    result = _run(["journalctl", "--user", "--no-pager", "-n", str(lines), "-u", MEDIA_KEYS_UNIT], runner)
    if result is None or result.returncode != 0:
        return []
    text = result.stdout.decode("utf-8", errors="replace")
    lines_out = [line.strip() for line in text.splitlines() if line.strip()]
    # journalctl's "no entries" placeholder is noise, not evidence.
    return [line for line in lines_out if line != "-- No entries --"]


def status(runner: Runner = subprocess.run) -> dict[str, object]:
    """Current state of our shortcut, used by ``--status``."""
    raw = get_value(SCHEMA, KEY, runner=runner)
    paths = parse_list(raw)
    return {
        "registered_paths": paths,
        "ours_listed": KEY_PATH in paths,
        "name": get_value(CHILD_SCHEMA, "name", KEY_PATH, runner),
        "command": get_value(CHILD_SCHEMA, "command", KEY_PATH, runner),
        "binding": get_value(CHILD_SCHEMA, "binding", KEY_PATH, runner),
    }


def install(
    *,
    binding: str = DEFAULT_BINDING,
    launch_command: Sequence[str] | None = None,
    runner: Runner = subprocess.run,
    unbind_conflicts: bool = True,
    take_binding: bool = False,
    reload_daemon: bool = True,
) -> Report:
    """Register ``Win+V`` for this application. Idempotent.

    ``take_binding`` also removes *other* custom shortcuts that already use
    the same key. They are the user's, so this only happens on request
    (``--take-binding``).
    """
    report = Report(binding=binding)
    binary = list(launch_command) if launch_command else resolve_launch_command()
    report.command = shlex.join([*binary, "--toggle"])
    if not gsettings_available():
        raise ShortcutError("gsettings not found — this is not a GNOME session")

    paths = parse_list(get_value(SCHEMA, KEY, runner=runner))
    # De-duplicate: drop other slots that already point at us.
    keep: list[str] = []
    for path in paths:
        if path != KEY_PATH and owns_binding(path, runner):
            report.removed.append(path)
            continue
        keep.append(path)
    if KEY_PATH not in keep:
        keep.append(KEY_PATH)
    if keep != paths:
        error = set_value_checked(SCHEMA, KEY, format_list(keep), runner=runner)
        if error:
            report.add(f"could not update {SCHEMA} {KEY}: {error}")
            return report

    values = (
        ("name", quote("Clipboard — Win+V")),
        ("command", quote(report.command)),
        ("binding", quote(binding)),
    )
    failures = [
        f"{key} = {value} ({error})"
        for key, value in values
        if (error := set_value_checked(CHILD_SCHEMA, key, value, KEY_PATH, runner))
    ]
    if failures:
        report.add("gsettings rejected " + "; ".join(failures))
        return report

    for conflict in conflicts(binding, runner):
        if unbind_conflicts and _disable_shell_conflict(runner):
            report.disabled.append(conflict)
            report.add(f"disabled conflicting GNOME shortcut: {conflict}")
        else:
            report.add(f"warning: {conflict} also uses {binding} and may win")
    if report.removed:
        report.add("removed duplicate bindings: " + ", ".join(report.removed))
    report.ok = True
    report.add(f"shortcut {binding} -> {report.command}")
    foreign = foreign_bindings(binding, runner)
    if foreign and take_binding:
        for path in remove_paths([path for path, _command in foreign], runner):
            report.took.append(path)
            report.add(f"took {binding} over from {path}")
        foreign = foreign_bindings(binding, runner)
    report.clashing = [describe_foreign(path, command) for path, command in foreign]
    for path, command in foreign:
        message = (
            f"warning: {describe_foreign(path, command)} also answers to {binding} and may "
            "win — remove that shortcut with --take-binding, or in "
            "Settings → Keyboard → Custom Shortcuts"
        )
        report.add(message)
        rival = rival_name(command)
        if rival:
            report.add(
                f"warning: {rival} is another clipboard manager — only one of them can "
                "own the key, and running two is not useful"
            )
    if reload_daemon:
        report.reloaded = refresh_media_keys(runner)
        if report.reloaded:
            report.add(report.reloaded)
        else:
            report.add(
                "warning: could not reload the shortcut daemon — if the key does nothing, "
                "log out and back in once"
            )
    return report


def uninstall(runner: Runner = subprocess.run) -> Report:
    """Remove our shortcut and any duplicate slot that points at us."""
    report = Report()
    if not gsettings_available():
        raise ShortcutError("gsettings not found — this is not a GNOME session")
    paths = parse_list(get_value(SCHEMA, KEY, runner=runner))
    keep = []
    for path in paths:
        if path == KEY_PATH or owns_binding(path, runner):
            report.removed.append(path)
            continue
        keep.append(path)
    if keep != paths and not set_value(SCHEMA, KEY, format_list(keep), runner=runner):
        report.add("could not update the keybinding list")
        return report
    # Give GNOME's notification tray shortcut back, but only when it is empty —
    # we must not overwrite a binding the user chose themselves.
    current = get_value(SHELL_SCHEMA, SHELL_TOGGLE_KEY, runner=runner)
    if current and current.strip() in {"@as []", "[]"}:
        set_value(SHELL_SCHEMA, SHELL_TOGGLE_KEY, "['<Super>v']", runner=runner)
        report.add(f"restored {SHELL_SCHEMA} {SHELL_TOGGLE_KEY} to <Super>v")
    report.ok = True
    report.add("shortcut removed")
    return report
