"""
paste.py — شبیه‌سازی Ctrl+V بعد از انتخاب آیتم (مثل ویندوز)
پشتیبانی Wayland و X11
"""

from __future__ import annotations
import shutil
import subprocess
import time
import os

def _has(c): return shutil.which(c) is not None

def _run(cmd, timeout=1.5):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return p.returncode == 0
    except Exception:
        return False

def simulate_paste(delay: float = 0.15) -> bool:
    """
    بعد از write_text، یک Paste مجازی می‌زند تا در اپ فوکوس شده Paste شود.
    برمی‌گرداند True اگر موفق شد.
    """
    time.sleep(delay)
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    # heuristics: try wtype first on wayland, xdotool on x11, ydotool everywhere

    # Wayland: wtype (needs compositor support) یا ydotool
    if session == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
        if _has("wtype"):
            # wtype can send Ctrl+v
            if _run(["wtype", "-M", "ctrl", "-P", "v", "-m", "ctrl"]):
                return True
            # fallback single
            if _run(["wtype", "-M", "ctrl", "v"]):
                return True
        if _has("ydotool"):
            # ydotool needs daemon
            if _run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"]):  # ctrl+v
                return True
        # fallback: try xdotool even on wayland (may work on XWayland)
        if _has("xdotool"):
            if _run(["xdotool", "key", "ctrl+v"]):
                return True
    else:
        # X11
        if _has("xdotool"):
            if _run(["xdotool", "key", "--clearmodifiers", "ctrl+v"]):
                return True
        if _has("wtype"):
            if _run(["wtype", "-M", "ctrl", "-P", "v", "-m", "ctrl"]):
                return True
        if _has("ydotool"):
            if _run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"]):
                return True

    # last resort: tell user to press Ctrl+V manually — still return False
    return False

def copy_and_paste(text: str = None, image_b64: str = None) -> bool:
    from .clipboard import write_text, write_image_b64
    ok = False
    if text is not None:
        ok = write_text(text)
    elif image_b64 is not None:
        ok = write_image_b64(image_b64)
    else:
        return False
    if not ok:
        return False
    # small delay then paste
    return simulate_paste()
