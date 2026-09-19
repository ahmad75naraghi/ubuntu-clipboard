"""Logging setup.

A single rotating file under ``$XDG_CACHE_HOME/ubuntu-clipboard`` plus optional
console output; the old implementation appended to three unbounded files.
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import os
import sys
from pathlib import Path

from .config import log_path

_MAX_BYTES = 512 * 1024
_BACKUPS = 2
_FORMAT = "%(asctime)s %(levelname)-7s [%(process)d] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(debug: bool = False, console: bool = False) -> Path:
    """Configure the root logger once. Returns the log file path."""
    global _configured
    target = log_path()
    if _configured:
        logging.getLogger().setLevel(logging.DEBUG if debug else logging.INFO)
        return target
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            target, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
        root.addHandler(file_handler)
    except OSError:  # read-only or missing home directory
        target = Path(os.devnull)

    if console or os.environ.get("UBUNTU_CLIPBOARD_DEBUG"):
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
        root.addHandler(stream)

    # Third party chatter we never want in our logs.
    logging.getLogger("PIL").setLevel(logging.WARNING)
    _configured = True
    return target


def tail(lines: int = 200) -> str:
    """Return the last ``lines`` lines of the log file."""
    path = log_path()
    if not path.is_file():
        return "(no log file yet)"
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"(cannot read {path}: {exc})"
    if lines <= 0:
        return "\n".join(content)
    return "\n".join(content[-lines:])


def clear() -> None:
    """Truncate the log file (keeps rotations intact)."""
    path = log_path()
    for candidate in (path, *(path.with_name(f"{path.name}.{i}") for i in range(1, _BACKUPS + 1))):
        with contextlib.suppress(OSError):  # pragma: no cover - defensive
            candidate.unlink(missing_ok=True)
