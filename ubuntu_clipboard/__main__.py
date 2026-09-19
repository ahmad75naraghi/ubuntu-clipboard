"""Allow ``python -m ubuntu_clipboard``."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
