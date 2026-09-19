"""Minimal, dependency free translation layer (Persian first, English fallback).

``gettext`` is overkill for a two language application that is not shipped with
compiled ``.mo`` files, but the interface is the same: call :func:`t` with a key.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "fa"
RTL_LANGUAGES = frozenset({"fa", "ar", "he", "ur"})

TRANSLATIONS: dict[str, dict[str, str]] = {
    # ── application ────────────────────────────────────────────────────────
    "app.name": {"fa": "کلیپ‌بورد", "en": "Clipboard"},
    "app.subtitle": {"fa": "تاریخچه کلیپ‌بورد • Win+V", "en": "Clipboard history • Win+V"},
    # ── search & actions ───────────────────────────────────────────────────
    "search.placeholder": {"fa": "جستجو در تاریخچه…", "en": "Search history…"},
    "action.clear": {"fa": "پاک کردن همه", "en": "Clear all"},
    "action.clear_history": {"fa": "پاک کردن تاریخچه", "en": "Clear history"},
    "action.settings": {"fa": "تنظیمات", "en": "Preferences"},
    "action.close": {"fa": "بستن", "en": "Close"},
    "action.pin": {"fa": "سنجاق کردن", "en": "Pin"},
    "action.unpin": {"fa": "برداشتن سنجاق", "en": "Unpin"},
    "action.delete": {"fa": "حذف", "en": "Delete"},
    "action.about": {"fa": "درباره", "en": "About"},
    # ── list ───────────────────────────────────────────────────────────────
    "section.pinned": {"fa": "سنجاق‌شده", "en": "Pinned"},
    "section.recent": {"fa": "اخیر", "en": "Recent"},
    "count.items": {"fa": "{count} آیتم", "en": "{count} items"},
    "empty.title": {"fa": "تاریخچه خالی است", "en": "No clipboard history yet"},
    "empty.hint": {
        "fa": "هر چیزی را کپی کنید تا اینجا ظاهر شود",
        "en": "Copy something with Ctrl+C and it shows up here",
    },
    "empty.tip": {"fa": "Ctrl+C  →  Win+V  →  انتخاب", "en": "Ctrl+C  →  Win+V  →  select"},
    "footer.hints": {
        "fa": "↵ جای‌گذاری    ↑↓ حرکت    Del حذف    Ctrl+P سنجاق    Esc بستن",
        "en": "↵ paste    ↑↓ navigate    Del delete    Ctrl+P pin    Esc close",
    },
    "preview.image": {"fa": "تصویر", "en": "Image"},
    "preview.image_hint": {
        "fa": "برای جای‌گذاری کلیک کنید",
        "en": "Click to paste",
    },
    "file.more": {"fa": "و {count} مورد دیگر", "en": "+{count} more"},
    # ── content types ──────────────────────────────────────────────────────
    "type.text": {"fa": "متن", "en": "TEXT"},
    "type.code": {"fa": "کد", "en": "CODE"},
    "type.link": {"fa": "لینک", "en": "LINK"},
    "type.color": {"fa": "رنگ", "en": "COLOR"},
    "type.image": {"fa": "تصویر", "en": "IMAGE"},
    "type.file": {"fa": "فایل", "en": "FILE"},
    # ── relative time ──────────────────────────────────────────────────────
    "time.now": {"fa": "اکنون", "en": "just now"},
    "time.minutes": {"fa": "{count} دقیقه پیش", "en": "{count} min ago"},
    "time.hours": {"fa": "{count} ساعت پیش", "en": "{count} h ago"},
    "time.days": {"fa": "{count} روز پیش", "en": "{count} d ago"},
    # ── dialogs ────────────────────────────────────────────────────────────
    "dialog.clear.title": {"fa": "پاک کردن همه؟", "en": "Clear all history?"},
    "dialog.clear.body": {
        "fa": "آیتم‌های سنجاق‌شده نگه داشته می‌شوند. ادامه می‌دهید؟",
        "en": "Pinned items are kept. Do you want to continue?",
    },
    "dialog.clear.body_all": {
        "fa": "این کار همه آیتم‌ها را حذف می‌کند. ادامه می‌دهید؟",
        "en": "Every item will be removed. Do you want to continue?",
    },
    "button.cancel": {"fa": "انصراف", "en": "Cancel"},
    "button.ok": {"fa": "تأیید", "en": "OK"},
    # ── notifications ──────────────────────────────────────────────────────
    "notify.copied": {
        "fa": "در کلیپ‌بورد کپی شد — با Ctrl+V جای‌گذاری کنید",
        "en": "Copied to the clipboard — press Ctrl+V to paste",
    },
    "notify.copied_manual": {
        "fa": "در کلیپ‌بورد کپی شد — جای‌گذاری خودکار فعال نیست، با Ctrl+V بچسبانید "
        "(برای فعال‌سازی: ubuntu-clipboard --setup-paste)",
        "en": "Copied to the clipboard — automatic pasting is off, press Ctrl+V "
        "(enable it with: ubuntu-clipboard --setup-paste)",
    },
    "notify.history_cleared": {"fa": "تاریخچه پاک شد", "en": "Clipboard history cleared"},
    "notify.shortcut_installed": {"fa": "میانبر Win+V ثبت شد", "en": "Win+V shortcut installed"},
    # ── preferences ────────────────────────────────────────────────────────
    "settings.title": {"fa": "تنظیمات کلیپ‌بورد", "en": "Clipboard preferences"},
    "settings.group.history": {"fa": "تاریخچه", "en": "History"},
    "settings.group.privacy": {"fa": "حریم خصوصی", "en": "Privacy"},
    "settings.group.appearance": {"fa": "ظاهر", "en": "Appearance"},
    "settings.group.system": {"fa": "سیستم", "en": "System"},
    "settings.group.about": {"fa": "درباره", "en": "About"},
    "settings.max_items": {"fa": "حداکثر تعداد آیتم‌ها", "en": "Maximum history size"},
    "settings.max_items.subtitle": {
        "fa": "آیتم‌های قدیمی‌تر خودکار حذف می‌شوند",
        "en": "Older items are removed automatically",
    },
    "settings.pin_limit": {"fa": "حداکثر آیتم‌های سنجاق‌شده", "en": "Maximum pinned items"},
    "settings.keep_pinned": {
        "fa": "نگه‌داشتن سنجاق‌شده‌ها هنگام پاک کردن همه",
        "en": "Keep pinned items when clearing",
    },
    "settings.exclude_sensitive": {
        "fa": "ثبت نکردن رمز، کلید و شماره کارت",
        "en": "Do not record passwords, keys and card numbers",
    },
    "settings.exclude_sensitive.subtitle": {
        "fa": "همچنین محتوایی که مدیران رمز به‌عنوان حساس علامت می‌زنند",
        "en": "Also skips clipboard content marked as secret by password managers",
    },
    "settings.max_item_size": {
        "fa": "حداکثر حجم آیتم متنی (کیلوبایت)",
        "en": "Maximum text item size (KB)",
    },
    "settings.max_image_size": {"fa": "حداکثر حجم تصویر (کیلوبایت)", "en": "Maximum image size (KB)"},
    "settings.theme": {"fa": "تم", "en": "Theme"},
    "settings.theme.system": {"fa": "هماهنگ با سیستم", "en": "Follow system"},
    "settings.theme.dark": {"fa": "تاریک", "en": "Dark"},
    "settings.theme.light": {"fa": "روشن", "en": "Light"},
    "settings.language": {"fa": "زبان", "en": "Language"},
    "settings.language.auto": {"fa": "خودکار (بر اساس سیستم)", "en": "Automatic (system locale)"},
    "settings.window_size": {"fa": "اندازه پنجره (عرض × ارتفاع)", "en": "Window size (width × height)"},
    "settings.thumbnail_height": {"fa": "ارتفاع تصویر بندانگشتی", "en": "Thumbnail height"},
    "settings.close_on_focus_loss": {
        "fa": "بستن پنجره وقتی تمرکز از دست می‌رود",
        "en": "Close the window when it loses focus",
    },
    "settings.auto_start": {"fa": "اجرا هنگام ورود به سیستم", "en": "Start when I log in"},
    "settings.shortcut": {"fa": "میانبر باز کردن", "en": "Open shortcut"},
    "settings.shortcut.subtitle": {
        "fa": "میانبر سیستمی گنوم که این پنجره را باز می‌کند",
        "en": "GNOME keybinding that opens this window",
    },
    "settings.shortcut.install": {"fa": "ثبت میانبر", "en": "Install shortcut"},
    "settings.shortcut.installed": {"fa": "ثبت شده", "en": "Installed"},
    "settings.shortcut.failed": {
        "fa": "ثبت میانبر ناموفق بود — از مسیر Settings → Keyboard امتحان کنید",
        "en": "Could not install the shortcut — use Settings → Keyboard instead",
    },
    "settings.config_file": {"fa": "فایل تنظیمات", "en": "Configuration file"},
    "settings.application": {"fa": "برنامه", "en": "Application"},
    "settings.reset": {"fa": "بازگشت به پیش‌فرض", "en": "Reset to defaults"},
    # ── CLI ────────────────────────────────────────────────────────────────
    "cli.items_header": {"fa": "تاریخچه کلیپ‌بورد", "en": "Clipboard history"},
}


def resolve_language(setting: str = "auto") -> str:
    """Turn the ``language`` setting into a concrete language code."""
    if setting in TRANSLATIONS["app.name"]:
        return setting
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(variable, "")
        if not value:
            continue
        code = value.split(".")[0].split("_")[0].lower()
        if code:
            return code if code in TRANSLATIONS["app.name"] else "en"
    return DEFAULT_LANGUAGE


_language: str | None = None


def set_language(setting: str = "auto") -> str:
    """Set the active language (``auto`` resolves the environment)."""
    global _language
    _language = resolve_language(setting)
    return _language


def get_language() -> str:
    if _language is None:
        return set_language("auto")
    return _language


def is_rtl() -> bool:
    return get_language() in RTL_LANGUAGES


def t(key: str, **kwargs: object) -> str:
    """Translate ``key`` and format it with ``kwargs``."""
    entry = TRANSLATIONS.get(key)
    if entry is None:
        log.debug("missing translation for %r", key)
        return key
    text = entry.get(get_language()) or entry.get(DEFAULT_LANGUAGE) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):  # pragma: no cover - developer error
            log.warning("cannot format translation %r with %r", key, kwargs)
            return text
    return text
