"""Translations: both languages stay in sync and fall back gracefully."""

from __future__ import annotations

import pytest
from ubuntu_clipboard import i18n


def test_every_key_has_both_languages():
    for key, entry in i18n.TRANSLATIONS.items():
        assert "fa" in entry, f"{key} is missing Persian"
        assert "en" in entry, f"{key} is missing English"
        assert entry["fa"].strip(), f"{key} has an empty Persian string"
        assert entry["en"].strip(), f"{key} has an empty English string"


def test_language_switch():
    i18n.set_language("fa")
    assert i18n.t("app.name") == "کلیپ‌بورد"
    i18n.set_language("en")
    assert i18n.t("app.name") == "Clipboard"


def test_missing_key_returns_the_key():
    i18n.set_language("en")
    assert i18n.t("does.not.exist") == "does.not.exist"


def test_placeholders_are_formatted():
    i18n.set_language("en")
    assert i18n.t("count.items", count=3) == "3 items"
    i18n.set_language("fa")
    assert "۳" not in i18n.t("count.items", count=3)
    assert "3" in i18n.t("count.items", count=3)


def test_bad_placeholders_do_not_raise():
    assert i18n.t("count.items", unknown=1)


@pytest.mark.parametrize(
    ("locale", "expected"),
    [("fa_IR.UTF-8", "fa"), ("en_US.UTF-8", "en"), ("de_DE.UTF-8", "en"), ("C", "en")],
)
def test_auto_language_uses_the_locale(monkeypatch, locale, expected):
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("LANG", locale)
    assert i18n.resolve_language("auto") == expected


def test_explicit_setting_wins(monkeypatch):
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    assert i18n.resolve_language("fa") == "fa"
    assert i18n.resolve_language("en") == "en"


def test_rtl_detection():
    i18n.set_language("fa")
    assert i18n.is_rtl() is True
    i18n.set_language("en")
    assert i18n.is_rtl() is False


def test_get_language_is_memoised(monkeypatch):
    i18n.set_language("en")
    monkeypatch.setenv("LANG", "fa_IR.UTF-8")
    assert i18n.get_language() == "en"
