"""Shared fixtures: every test runs against a throwaway XDG environment."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ubuntu_clipboard import config as config_module  # noqa: E402
from ubuntu_clipboard import i18n  # noqa: E402
from ubuntu_clipboard.config import Config  # noqa: E402
from ubuntu_clipboard.storage import HistoryStore  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Redirect configuration, data and cache directories into ``tmp_path``."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.delenv("UBUNTU_CLIPBOARD_DEBUG", raising=False)
    config_module.reset_config()
    i18n.set_language("en")
    yield
    config_module.reset_config()


@pytest.fixture(autouse=True)
def instant_sleep(monkeypatch):
    """Never wait for a daemon in tests: the wait logic is exercised directly."""
    monkeypatch.setattr("ubuntu_clipboard.shortcut.SLEEP", lambda _seconds: None)
    monkeypatch.setattr("ubuntu_clipboard.paste.SLEEP", lambda _seconds: None)


@pytest.fixture
def config() -> Config:
    return Config()


@pytest.fixture
def store(config):
    instance = HistoryStore(config=config)
    yield instance
    instance.close()


class FakeGsettings:
    """Stand-in for the ``gsettings`` binary, including its schema rules.

    The rules matter: ``org.gnome.settings-daemon.plugins.media-keys`` is not
    relocatable, so ``...media-keys:<path>`` is an error — only the child schema
    ``...media-keys.custom-keybinding:<path>`` accepts a path. A double that
    accepted both forms hid a broken shortcut install for a whole release.
    """

    #: Schemas that accept ``schema:path``.
    RELOCATABLE = frozenset(
        {
            "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding",
            "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding.extra",
        }
    )
    #: Schemas that exist at all, so a typo is caught instead of accepted.
    KNOWN = frozenset(
        {
            "org.gnome.settings-daemon.plugins.media-keys",
            "org.gnome.shell.keybindings",
            "org.gnome.desktop.input-sources",
            *RELOCATABLE,
        }
    )

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})
        self.calls: list[list[str]] = []

    @staticmethod
    def _key(target: str, key: str) -> str:
        return f"{target}|{key}"

    def _validate(self, command: list[str], target: str) -> subprocess.CompletedProcess | None:
        schema, _, path = target.partition(":")
        if schema not in self.KNOWN:
            return subprocess.CompletedProcess(command, 1, b"", f'Schema "{schema}" does not exist'.encode())
        if path and schema not in self.RELOCATABLE:
            return subprocess.CompletedProcess(
                command,
                1,
                b"",
                f'Schema "{schema}" is not relocatable (path must not be specified)'.encode(),
            )
        if not path and schema in self.RELOCATABLE:
            return subprocess.CompletedProcess(
                command, 1, b"", f'Schema "{schema}" is relocatable (path must be specified)'.encode()
            )
        return None

    def __call__(self, command, **_kwargs) -> subprocess.CompletedProcess:
        command = list(command)
        self.calls.append(command)
        if len(command) < 4 or command[0] != "gsettings":
            return subprocess.CompletedProcess(command, 2, b"", b"bad usage")
        action, target, key = command[1], command[2], command[3]
        invalid = self._validate(command, target)
        if invalid is not None:
            return invalid
        lookup = self._key(target, key)
        if action == "get":
            if lookup not in self.values:
                return subprocess.CompletedProcess(command, 1, b"", b"no such key")
            return subprocess.CompletedProcess(command, 0, self.values[lookup].encode(), b"")
        if action == "set":
            if len(command) < 5:
                return subprocess.CompletedProcess(command, 2, b"", b"missing value")
            self.values[lookup] = command[4]
            return subprocess.CompletedProcess(command, 0, b"", b"")
        if action == "reset":
            # Real gsettings drops the value back to the schema default; the
            # double forgets it, so a stale key can never look cleaned up.
            self.values.pop(lookup, None)
            return subprocess.CompletedProcess(command, 0, b"", b"")
        return subprocess.CompletedProcess(command, 2, b"", b"unknown action")


@pytest.fixture
def fake_gsettings(monkeypatch):
    """A gsettings replacement plus a patched ``shutil.which``."""

    def build(values: dict[str, str] | None = None) -> FakeGsettings:
        fake = FakeGsettings(values)
        monkeypatch.setattr(
            "ubuntu_clipboard.shortcut.shutil.which",
            lambda name: "/usr/bin/gsettings" if name == "gsettings" else None,
        )
        return fake

    return build
