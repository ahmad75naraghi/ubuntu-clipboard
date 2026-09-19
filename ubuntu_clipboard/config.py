"""
config.py — مدیریت تنظیمات
~/.config/ubuntu-clipboard/config.json
"""

from __future__ import annotations
import json
import os
import re
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List

CONFIG_DIR = Path.home() / ".config" / "ubuntu-clipboard"
CONFIG_PATH = CONFIG_DIR / "config.json"
DB_PATH = CONFIG_DIR / "history.db"

DEFAULT_IGNORE_PATTERNS = [
    r"^\d{4}-\d{4}-\d{4}-\d{4}$",  # credit card like
    r"^(?:password|passwd|pwd)\s*[:=]",
]

@dataclass
class Config:
    max_items: int = 80
    max_item_size_kb: int = 512
    pin_limit: int = 20
    auto_start: bool = True
    keep_pinned_on_clear: bool = True
    theme: str = "dark"  # dark | light | system
    window_width: int = 420
    window_height: int = 560
    shortcut: str = "<Super>v"  # Win+V
    exclude_apps: List[str] = field(default_factory=lambda: ["KeePassXC", "Bitwarden", "1Password"])
    ignore_regex: List[str] = field(default_factory=lambda: DEFAULT_IGNORE_PATTERNS.copy())
    exclude_sensitive: bool = True
    image_max_preview: int = 512
    enable_sound: bool = False
    language: str = "fa"
    enable_tray: bool = False  # پیش‌فرض خاموش تا چشمک نزند — با --with-tray روشن می‌شود

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_PATH.exists():
            c = cls()
            c.save()
            return c
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            # migrate: only known fields
            known = {k for k in cls.__dataclass_fields__}
            filtered = {k: v for k, v in data.items() if k in known}
            return cls(**filtered)
        except Exception:
            return cls()

    def should_ignore(self, text: str) -> bool:
        if not text or len(text) > self.max_item_size_kb * 1024:
            return True
        if self.exclude_sensitive:
            # ignore OTP / very short secrets that look like password
            if re.match(r"^\s*\d{4,8}\s*$", text):  # pure OTP
                # keep OTP? Windows keeps it. We'll keep but mark sensitive.
                pass
            for pat in self.ignore_regex:
                try:
                    if re.search(pat, text, re.IGNORECASE | re.MULTILINE):
                        return True
                except re.error:
                    continue
        return False

# singleton
_config: Config | None = None

def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config.load()
    return _config
