"""GTK 4 user interface.

Importing this package never fails: when PyGObject is missing,
:data:`HAS_GTK` is ``False`` and the CLI reports how to install it.
"""

from __future__ import annotations

HAS_GTK = False
HAS_ADW = False
Gtk = Gdk = Gio = GLib = Pango = None
Adw = None

try:  # pragma: no cover - depends on the environment
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Pango", "1.0")
    from gi.repository import Gdk, Gio, GLib, Gtk, Pango

    HAS_GTK = True
    try:
        gi.require_version("Adw", "1")
        from gi.repository import Adw

        HAS_ADW = True
    except (ImportError, ValueError):
        Adw = None
except (ImportError, ValueError):  # pragma: no cover - headless/test environment
    Gtk = Gdk = Gio = GLib = None

__all__ = ["HAS_ADW", "HAS_GTK", "Adw", "Gdk", "Gio", "GLib", "Gtk", "Pango"]
