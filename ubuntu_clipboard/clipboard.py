"""
clipboard.py — خواندن و نوشتن کلیپ‌بورد روی Wayland و X11
- تلاش اول: GTK4 GDK (native)
- fallback: wl-paste/wl-copy و xclip/xsel
"""

from __future__ import annotations
import subprocess
import shutil
import base64
import os
from typing import Tuple, Optional

def _run(cmd, input_data=None, timeout=1.5) -> Tuple[bool, str, str]:
    try:
        p = subprocess.run(cmd, input=input_data, capture_output=True, timeout=timeout)
        return p.returncode == 0, p.stdout.decode("utf-8", errors="ignore") if isinstance(p.stdout, bytes) else str(p.stdout), p.stderr.decode("utf-8", errors="ignore") if isinstance(p.stderr, bytes) else ""
    except Exception as e:
        return False, "", str(e)

def _has(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def detect_session() -> str:
    t = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if t in ("wayland", "x11"):
        return t
    # heuristic
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"

def read_text_fallback() -> Optional[str]:
    # Wayland first
    if _has("wl-paste"):
        ok, out, _ = _run(["wl-paste", "-n", "--type", "text/plain"])
        if ok and out:
            return out
        # try without type
        ok, out, _ = _run(["wl-paste", "-n"])
        if ok and out:
            return out
    if _has("xclip"):
        ok, out, _ = _run(["xclip", "-o", "-selection", "clipboard"])
        if ok:
            return out
    if _has("xsel"):
        ok, out, _ = _run(["xsel", "-b", "-o"])
        if ok:
            return out
    return None

def read_image_fallback() -> Optional[bytes]:
    # Try wl-paste for image/png
    for mime in ["image/png", "image/jpeg", "image/jpg"]:
        if _has("wl-paste"):
            try:
                p = subprocess.run(["wl-paste", "--type", mime], capture_output=True, timeout=1.5)
                if p.returncode == 0 and p.stdout and len(p.stdout) > 100:
                    return p.stdout
            except Exception:
                pass
        if _has("xclip"):
            try:
                p = subprocess.run(["xclip", "-o", "-selection", "clipboard", "-t", mime], capture_output=True, timeout=1.5)
                if p.returncode == 0 and p.stdout and len(p.stdout) > 100:
                    return p.stdout
            except Exception:
                pass
    return None

def write_text(text: str) -> bool:
    # Try GTK if available
    try:
        import gi
        gi.require_version("Gdk", "4.0")
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk, Gio
        disp = Gdk.Display.get_default()
        if disp:
            cb = disp.get_clipboard()
            # Gdk Clipboard set via Gdk.ContentProvider
            from gi.repository import Gdk as Gdk2
            provider = Gdk.ContentProvider.new_for_value(text)
            cb.set_content(provider)
            # also primary?
            return True
    except Exception:
        pass

    # Wayland
    if _has("wl-copy"):
        try:
            p = subprocess.run(["wl-copy"], input=text.encode("utf-8"), capture_output=True, timeout=1.5)
            if p.returncode == 0:
                return True
        except Exception:
            pass
    if _has("xclip"):
        ok, _, _ = _run(["xclip", "-i", "-selection", "clipboard"], input_data=text.encode("utf-8"))
        if ok:
            return True
    if _has("xsel"):
        ok, _, _ = _run(["xsel", "-b", "-i"], input_data=text.encode("utf-8"))
        if ok:
            return True
    return False

def write_image_b64(b64: str) -> bool:
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return False
    if _has("wl-copy"):
        for mime in ["image/png", "image/jpeg"]:
            try:
                p = subprocess.run(["wl-copy", "--type", mime], input=raw, capture_output=True, timeout=1.5)
                if p.returncode == 0:
                    return True
            except Exception:
                continue
    if _has("xclip"):
        try:
            p = subprocess.run(["xclip", "-i", "-selection", "clipboard", "-t", "image/png"], input=raw, capture_output=True, timeout=1.5)
            if p.returncode == 0:
                return True
        except Exception:
            pass
    return False
