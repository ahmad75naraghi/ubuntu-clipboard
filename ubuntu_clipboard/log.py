"""
log.py — لاگ دقیق برای عیب‌یابی چشمک
می‌نویسد به:
  - /tmp/ubuntu-clipboard.log (موقت، هر اجرا overwrite)
  - ~/.cache/ubuntu-clipboard/debug.log (دائمی، append با timestamp)
  - stdout (برای --debug)
"""

from __future__ import annotations
import os
import sys
import time
from pathlib import Path
from datetime import datetime

CACHE_LOG = Path.home() / ".cache" / "ubuntu-clipboard" / "debug.log"
TMP_LOG = Path("/tmp/ubuntu-clipboard.log")
# also legacy
CONFIG_LOG = Path.home() / ".config" / "ubuntu-clipboard" / "debug.log"

def _ensure_dirs():
    try:
        CACHE_LOG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_LOG.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def log(msg: str, level: str = "INFO"):
    """نوشتن لاگ با timestamp به همه جا"""
    _ensure_dirs()
    line = f"[{_ts()}] [{level}] [PID:{os.getpid()}] {msg}"
    # stdout always
    try:
        print(line, flush=True)
    except Exception:
        pass
    # tmp log (overwrite per run? we append)
    for p in [TMP_LOG, CACHE_LOG, CONFIG_LOG]:
        try:
            with open(p, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

def log_toggle(action: str, detail: str = ""):
    log(f"TOGGLE {action} {detail}".strip(), "TOGGLE")

def log_window(action: str, detail: str = ""):
    log(f"WINDOW {action} {detail}".strip(), "WINDOW")

def log_daemon(action: str, detail: str = ""):
    log(f"DAEMON {action} {detail}".strip(), "DAEMON")

def log_tray(action: str, detail: str = ""):
    log(f"TRAY {action} {detail}".strip(), "TRAY")

def log_error(msg: str):
    log(msg, "ERROR")

def clear_logs():
    for p in [TMP_LOG, CACHE_LOG]:
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass
    log("=== LOG CLEARED ===")

def tail_logs(n: int = 100) -> str:
    try:
        if CACHE_LOG.exists():
            lines = CACHE_LOG.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
            return "\n".join(lines[-n:])
    except Exception as e:
        return f"read log failed: {e}"
    return "(no log)"
