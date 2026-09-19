"""User configuration and XDG directory resolution.

All paths are resolved lazily (on every call) so that tests and callers can
override ``XDG_*`` environment variables without reloading the module.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

APP_DIR_NAME = "ubuntu-clipboard"

#: Files kept next to the configuration, used to detect v1 installations.
LEGACY_DB_NAME = "history.db"

#: Number of __init__ characters kept in a preview.
PREVIEW_LENGTH = 180

THEMES = ("system", "dark", "light")
LANGUAGES = ("fa", "en", "auto")

#: Content that is never recorded when ``exclude_sensitive`` is enabled.
DEFAULT_IGNORE_PATTERNS: list[str] = [
    # 16 digit card-like numbers with optional separators
    r"^\s*(?:\d[ -]?){13,19}\s*$",
    # "password = hunter2" style lines
    r"^\s*(?:password|passwd|pwd|secret|api[_-]?key|token)\s*[:=]\s*\S+",
    # PEM private keys
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
]

#: ``int`` fields: name -> (minimum, maximum)
_INT_RANGES: dict[str, tuple[int, int]] = {
    "max_items": (10, 1000),
    "max_item_size_kb": (1, 10_240),
    "max_image_size_kb": (64, 65_536),
    "pin_limit": (1, 200),
    "window_width": (320, 1600),
    "window_height": (320, 1600),
    "image_thumb_height": (40, 320),
}


def xdg_dir(variable: str, fallback: str) -> Path:
    """Return an XDG base directory, honouring the environment when sane."""
    raw = os.environ.get(variable, "")
    if raw and os.path.isabs(raw):
        return Path(raw)
    return Path.home() / fallback


def config_dir() -> Path:
    """``~/.config/ubuntu-clipboard``"""
    return xdg_dir("XDG_CONFIG_HOME", ".config") / APP_DIR_NAME


def data_dir() -> Path:
    """``~/.local/share/ubuntu-clipboard``"""
    return xdg_dir("XDG_DATA_HOME", ".local/share") / APP_DIR_NAME


def cache_dir() -> Path:
    """``~/.cache/ubuntu-clipboard``"""
    return xdg_dir("XDG_CACHE_HOME", ".cache") / APP_DIR_NAME


def autostart_dir() -> Path:
    """``~/.config/autostart``"""
    return xdg_dir("XDG_CONFIG_HOME", ".config") / "autostart"


def applications_dir() -> Path:
    """``~/.local/share/applications``"""
    return xdg_dir("XDG_DATA_HOME", ".local/share") / "applications"


def icons_dir() -> Path:
    """``~/.local/share/icons``"""
    return xdg_dir("XDG_DATA_HOME", ".local/share") / "icons"


def config_path() -> Path:
    return config_dir() / "config.json"


def database_path() -> Path:
    return data_dir() / "history.db"


def legacy_database_path() -> Path:
    return config_dir() / LEGACY_DB_NAME


def log_path() -> Path:
    return cache_dir() / "ubuntu-clipboard.log"


def _coerce(value: Any, default: Any) -> Any:
    """Best effort conversion of a JSON value to the type of ``default``."""
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        return default
    if isinstance(default, int):
        if isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    if isinstance(default, list):
        if isinstance(value, list):
            return [str(item) for item in value if isinstance(item, (str, int, float))]
        return list(default)
    return value


@dataclass
class Config:
    """User visible settings, mirroring ``~/.config/ubuntu-clipboard/config.json``."""

    # history
    max_items: int = 80
    max_item_size_kb: int = 512
    max_image_size_kb: int = 8192
    pin_limit: int = 20
    keep_pinned_on_clear: bool = True
    # privacy
    exclude_sensitive: bool = True
    ignore_regex: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_PATTERNS))
    # appearance
    theme: str = "system"
    language: str = "fa"
    window_width: int = 420
    window_height: int = 560
    image_thumb_height: int = 96
    close_on_focus_loss: bool = True
    # GNOME gives a Wayland client no way to skip the taskbar, so the popup is
    # started on the X11 (XWayland) backend, where ``skip-taskbar`` is honoured.
    hide_from_dock: bool = True
    # integration
    auto_start: bool = True
    shortcut: str = "<Super>v"

    def __post_init__(self) -> None:
        self.normalize()

    # -- validation ---------------------------------------------------------
    def normalize(self) -> list[str]:
        """Clamp and repair invalid values. Returns a list of human messages."""
        problems: list[str] = []
        for f in fields(self):
            raw = getattr(self, f.name)
            default = _DEFAULT_CONFIG_VALUES[f.name]
            fixed = _coerce(raw, default)
            if fixed != raw:
                problems.append(f"{f.name}: {raw!r} -> {fixed!r}")
                setattr(self, f.name, fixed)
        for name, (low, high) in _INT_RANGES.items():
            value = getattr(self, name)
            clamped = max(low, min(high, value))
            if clamped != value:
                problems.append(f"{name}: {value} -> {clamped} (out of range {low}..{high})")
                setattr(self, name, clamped)
        if self.theme not in THEMES:
            problems.append(f"theme: {self.theme!r} -> 'system'")
            self.theme = "system"
        if self.language not in LANGUAGES:
            problems.append(f"language: {self.language!r} -> 'fa'")
            self.language = "fa"
        if not isinstance(self.shortcut, str) or not self.shortcut.strip():
            self.shortcut = _DEFAULT_CONFIG_VALUES["shortcut"]
        # Drop patterns that cannot compile instead of failing at capture time.
        compiled: list[str] = []
        for pattern in self.ignore_regex:
            try:
                re.compile(pattern)
            except re.error as exc:
                problems.append(f"ignore_regex: dropping {pattern!r} ({exc})")
                continue
            compiled.append(pattern)
        self.ignore_regex = compiled
        if problems:
            log.warning("config adjusted: %s", "; ".join(problems))
        return problems

    # -- persistence --------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: Path | None = None) -> Path:
        """Atomically write the configuration (never leaves a half written file)."""
        target = Path(path) if path else config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=False)
        fd, tmp_name = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        log.debug("configuration written to %s", target)
        return target

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """Read configuration, falling back to defaults on any problem."""
        target = Path(path) if path else config_path()
        if not target.is_file():
            config = cls()
            try:
                config.save(target)
            except OSError as exc:  # read-only home, container, ...
                log.warning("cannot create %s: %s", target, exc)
            return config
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.error("invalid configuration %s (%s) — using defaults", target, exc)
            _backup_broken_config(target)
            return cls()
        if not isinstance(data, dict):
            log.error("configuration %s is not an object — using defaults", target)
            _backup_broken_config(target)
            return cls()
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            log.info("ignoring unknown configuration keys: %s", ", ".join(unknown))
        values = {key: value for key, value in data.items() if key in known}
        return cls(**values)

    # -- helpers ------------------------------------------------------------
    def compiled_ignore_patterns(self) -> list[re.Pattern[str]]:
        patterns: list[re.Pattern[str]] = []
        for pattern in self.ignore_regex:
            try:
                patterns.append(re.compile(pattern, re.IGNORECASE | re.MULTILINE))
            except re.error:  # pragma: no cover - normalize() drops those
                continue
        return patterns

    def max_item_bytes(self) -> int:
        return self.max_item_size_kb * 1024

    def max_image_bytes(self) -> int:
        return self.max_image_size_kb * 1024

    def is_sensitive(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in self.compiled_ignore_patterns())

    def should_capture(self, text: str) -> bool:
        """Whether a text clipboard payload should be recorded."""
        if not text or not text.strip():
            return False
        if len(text.encode("utf-8", errors="ignore")) > self.max_item_bytes():
            return False
        if self.exclude_sensitive and self.is_sensitive(text):
            log.debug("ignoring sensitive payload (%d chars)", len(text))
            return False
        return True


def _backup_broken_config(path: Path) -> None:
    with contextlib.suppress(OSError):  # pragma: no cover - defensive
        path.replace(path.with_suffix(".json.corrupt"))


#: Snapshot of the default values, needed because ``dataclasses.fields()`` is not
#: available before the class body is executed.
_DEFAULT_CONFIG_VALUES: dict[str, Any] = {f.name: f.default for f in fields(Config)}
_DEFAULT_CONFIG_VALUES["ignore_regex"] = list(DEFAULT_IGNORE_PATTERNS)

_config: Config | None = None


def get_config(reload: bool = False) -> Config:
    """Return the process wide configuration singleton."""
    global _config
    if _config is None or reload:
        _config = Config.load()
    return _config


def set_config(config: Config) -> Config:
    """Replace the singleton (used by tests and by the settings dialog)."""
    global _config
    _config = config
    return _config


def reset_config() -> None:
    """Forget the cached configuration so the next call reloads it."""
    global _config
    _config = None
