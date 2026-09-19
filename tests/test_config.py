"""Configuration behaviour: validation, persistence, privacy filters."""

from __future__ import annotations

import json
import logging

import pytest
from ubuntu_clipboard import config as config_module
from ubuntu_clipboard.config import (
    Config,
    autostart_dir,
    cache_dir,
    config_dir,
    config_path,
    data_dir,
    database_path,
    get_config,
    icons_dir,
    legacy_database_path,
    log_path,
    xdg_dir,
)


def test_xdg_paths_follow_the_environment(isolated_home, tmp_path):
    assert config_dir() == tmp_path / "config" / "ubuntu-clipboard"
    assert data_dir() == tmp_path / "data" / "ubuntu-clipboard"
    assert cache_dir() == tmp_path / "cache" / "ubuntu-clipboard"
    assert autostart_dir() == tmp_path / "config" / "autostart"
    assert icons_dir() == tmp_path / "data" / "icons"
    assert config_path() == config_dir() / "config.json"
    assert database_path() == data_dir() / "history.db"
    assert legacy_database_path() == config_dir() / "history.db"
    assert log_path() == cache_dir() / "ubuntu-clipboard.log"


def test_xdg_dir_ignores_relative_values(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "relative/path")
    assert xdg_dir("XDG_DATA_HOME", ".local/share").is_absolute()


def test_defaults_are_valid():
    config = Config()
    assert config.normalize() == []
    assert config.max_items == 80
    assert config.theme == "system"
    assert config.language == "fa"
    assert config.keep_pinned_on_clear is True
    assert config.hide_from_dock is True


def test_out_of_range_values_are_clamped(caplog):
    """The dataclass repairs itself while constructing and explains why."""
    with caplog.at_level(logging.WARNING, logger="ubuntu_clipboard.config"):
        config = Config(max_items=5, window_width=10_000, image_thumb_height=1)
    assert config.max_items == 10
    assert config.window_width == 1600
    assert config.image_thumb_height == 40
    assert "max_items" in caplog.text
    assert config.normalize() == []  # already repaired


def test_invalid_types_fall_back_to_defaults():
    config = Config(max_items="nonsense", keep_pinned_on_clear="maybe", theme=42, language=None)
    config.normalize()
    assert config.max_items == 80
    assert config.keep_pinned_on_clear is True
    assert config.theme == "system"
    assert config.language == "fa"


def test_booleans_and_numbers_are_coerced():
    config = Config(keep_pinned_on_clear="false", max_items="120")
    config.normalize()
    assert config.keep_pinned_on_clear is False
    assert config.max_items == 120


def test_broken_regex_is_dropped(caplog):
    with caplog.at_level(logging.WARNING, logger="ubuntu_clipboard.config"):
        config = Config(ignore_regex=["([", r"^secret$"])
    assert config.ignore_regex == [r"^secret$"]
    assert "ignore_regex" in caplog.text


def test_save_and_load_round_trip():
    config = Config(max_items=42, theme="dark", language="en")
    path = config.save()
    assert path == config_path()
    loaded = Config.load()
    assert loaded.max_items == 42
    assert loaded.theme == "dark"
    assert loaded.language == "en"
    assert loaded.to_dict() == config.to_dict()


def test_save_is_atomic_and_leaves_valid_json():
    config = Config()
    config.save()
    payload = json.loads(config_path().read_text(encoding="utf-8"))
    assert payload["max_items"] == 80
    leftovers = [item for item in config_dir().iterdir() if item.name.startswith(".config-")]
    assert leftovers == []


def test_save_never_writes_a_partial_file(monkeypatch):
    config = Config()

    def explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("os.fsync", explode)
    with pytest.raises(OSError):
        config.save()
    assert not config_path().exists()
    assert [item for item in config_dir().iterdir() if item.name.startswith(".config-")] == []


def test_missing_file_creates_defaults():
    config = Config.load()
    assert config.max_items == 80
    assert config_path().is_file()


def test_unknown_keys_are_ignored():
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps({"max_items": 30, "future_option": True}), encoding="utf-8")
    config = Config.load()
    assert config.max_items == 30
    assert not hasattr(config, "future_option")


def test_corrupt_file_is_backed_up():
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text("{not json", encoding="utf-8")
    config = Config.load()
    assert config.max_items == 80
    assert config_path().with_suffix(".json.corrupt").is_file()


def test_non_object_payload_is_rejected():
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text("[1, 2, 3]", encoding="utf-8")
    assert Config.load().max_items == 80


def test_singleton_is_cached_and_reloadable():
    first = get_config()
    assert get_config() is first
    first.max_items = 33
    first.save()
    reloaded = get_config(reload=True)
    assert reloaded is not first
    assert reloaded.max_items == 33


@pytest.mark.parametrize(
    "text",
    [
        "4111 1111 1111 1111",
        "4111111111111111",
        "password=hunter2",
        "api_key: abc123",
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
)
def test_sensitive_payloads_are_ignored(config, text):
    assert config.should_capture(text) is False


@pytest.mark.parametrize("text", ["123456", "hello world", "#ff5500", "https://example.com"])
def test_normal_payloads_are_captured(config, text):
    assert config.should_capture(text) is True


def test_sensitive_filtering_can_be_disabled():
    config = Config(exclude_sensitive=False)
    assert config.should_capture("password=hunter2") is True


def test_empty_and_oversized_text_is_rejected():
    config = Config(max_item_size_kb=1)
    assert config.should_capture("") is False
    assert config.should_capture("   \n  ") is False
    assert config.should_capture("x" * 2048) is False
    assert config.should_capture("x" * 512) is True


def test_byte_limits(config):
    assert config.max_item_bytes() == config.max_item_size_kb * 1024
    assert config.max_image_bytes() == config.max_image_size_kb * 1024


def test_legacy_database_constant_used_for_migration(isolated_home):
    assert config_module.LEGACY_DB_NAME == "history.db"


def test_hide_from_dock_survives_a_round_trip(tmp_path):
    config = Config(hide_from_dock=False)
    config.save()
    assert Config.load().hide_from_dock is False


def test_an_unknown_config_key_does_not_break_loading(tmp_path):
    """Files written by an older or newer version must still load."""
    config = Config()
    config.save()
    path = config_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["something_from_the_future"] = True
    path.write_text(json.dumps(data), encoding="utf-8")
    assert Config.load().hide_from_dock is True
