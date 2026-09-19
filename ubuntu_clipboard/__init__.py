"""Ubuntu Clipboard — a Windows 11 style clipboard history for Ubuntu (``Win+V``).

The package is intentionally split so that everything except :mod:`ubuntu_clipboard.ui`
and :mod:`ubuntu_clipboard.app` is importable without GTK/PyGObject.
"""

from __future__ import annotations

__version__ = "2.1.3"
__author__ = "ubuntu-clipboard contributors"
__license__ = "MIT"

APP_ID = "io.github.ahmad75naraghi.UbuntuClipboard"
APP_NAME = "Ubuntu Clipboard"
APP_ICON = "ubuntu-clipboard"
PROJECT_URL = "https://github.com/ahmad75naraghi/ubuntu-clipboard"

__all__ = [
    "APP_ICON",
    "APP_ID",
    "APP_NAME",
    "PROJECT_URL",
    "__version__",
]
