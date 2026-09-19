"""Desktop integration: desktop entry, icon, autostart, legacy cleanup."""

from __future__ import annotations

import pathlib

from ubuntu_clipboard import install
from ubuntu_clipboard.shortcut import Report as ShortcutReport

BINARY = ["/usr/bin/ubuntu-clipboard"]


def test_desktop_entry_is_valid(monkeypatch, tmp_path):
    text = install.desktop_entry_text(BINARY)
    assert "Exec=/usr/bin/ubuntu-clipboard --toggle" in text
    assert "TryExec=/usr/bin/ubuntu-clipboard" in text
    assert install.APP_ID in text
    assert "[Desktop Action" in text
    # Every entry of the Actions= list needs its own group.
    actions_line = next(line for line in text.splitlines() if line.startswith("Actions="))
    names = actions_line.removeprefix("Actions=").rstrip(";").split(";")
    for name in names:
        assert f"[Desktop Action {name}]" in text


def test_desktop_entry_quotes_paths_with_spaces():
    text = install.desktop_entry_text(["/opt/My Apps/clipboard"])
    assert "'/opt/My Apps/clipboard' --toggle" in text


def test_autostart_entry(monkeypatch):
    text = install.autostart_entry_text(BINARY)
    assert "--background" in text
    assert "X-GNOME-Autostart-enabled=true" in text
    assert "X-GNOME-Autostart-Delay=3" in text
    assert "NoDisplay=true" in text


def test_install_and_uninstall_round_trip(isolated_home, monkeypatch):
    monkeypatch.setattr(
        "ubuntu_clipboard.install.install_shortcut",
        lambda **_kwargs: ShortcutReport(ok=True, binding="<Super>v"),
    )
    monkeypatch.setattr(
        "ubuntu_clipboard.install.uninstall_shortcut",
        lambda **_kwargs: ShortcutReport(ok=True),
    )
    report = install.install_all(launch_command=BINARY)
    assert install.desktop_entry_path().is_file()
    assert install.autostart_path().is_file()
    assert install.installed_icon_path().is_file()
    assert install.autostart_enabled() is True
    assert report.warnings == []
    assert report.shortcut is not None and report.shortcut.ok

    desktop_text = install.desktop_entry_path().read_text(encoding="utf-8")
    assert "ubuntu-clipboard --toggle" in desktop_text

    report = install.uninstall_all(remove_history=True, remove_config=True)
    assert not install.desktop_entry_path().exists()
    assert not install.autostart_path().exists()
    assert not install.installed_icon_path().exists()
    assert report.actions


def test_uninstall_keeps_history_by_default(isolated_home, monkeypatch):
    from ubuntu_clipboard.config import Config, database_path
    from ubuntu_clipboard.storage import HistoryStore

    store = HistoryStore(config=Config())
    store.add_text("keep me")
    store.close()
    assert database_path().is_file()

    monkeypatch.setattr(
        "ubuntu_clipboard.install.uninstall_shortcut",
        lambda **_kwargs: ShortcutReport(ok=True),
    )
    install.uninstall_all()
    assert database_path().is_file()


def test_autostart_toggle(isolated_home):
    assert install.autostart_enabled() is False
    path = install.enable_autostart(BINARY)
    assert path == install.autostart_path()
    assert install.autostart_enabled() is True
    assert install.disable_autostart() is True
    assert install.disable_autostart() is False


def test_legacy_files_are_removed(isolated_home):
    from ubuntu_clipboard.config import applications_dir, autostart_dir

    applications_dir().mkdir(parents=True, exist_ok=True)
    autostart_dir().mkdir(parents=True, exist_ok=True)
    legacy = [
        applications_dir() / "ubuntu-clipboard.desktop",
        applications_dir() / "ubuntu-clipboard-settings.desktop",
        autostart_dir() / "ubuntu-clipboard.desktop",
        autostart_dir() / "ubuntu-clipboard-daemon.desktop",
    ]
    for path in legacy:
        path.write_text("[Desktop Entry]\n", encoding="utf-8")
    removed = install.remove_legacy_files()
    assert sorted(removed) == sorted(legacy)
    assert removed
    assert install.remove_legacy_files() == []


def test_icon_install(isolated_home):
    target = install.install_icon()
    assert target is not None
    assert target.is_file()
    assert target.read_bytes() == install.icon_source().read_bytes()


def test_refresh_caches_uses_available_tools(isolated_home):
    calls: list[list[str]] = []

    def runner(command, **_kwargs):
        calls.append(list(command))
        return

    install.refresh_caches(
        which=lambda name: (
            f"/usr/bin/{name}" if name in {"update-desktop-database", "gtk-update-icon-cache"} else None
        ),
        runner=runner,
    )
    assert any("update-desktop-database" in call[0] for call in calls)
    assert any("gtk-update-icon-cache" in call[0] for call in calls)


def test_refresh_caches_survives_missing_tools(isolated_home):
    install.refresh_caches(which=lambda _name: None, runner=lambda *_args, **_kwargs: None)


def test_environment_report(isolated_home):
    report = install.environment_report()
    for key in (
        "version",
        "python",
        "session",
        "tools",
        "auto_paste",
        "desktop_entry",
        "autostart",
        "icon",
    ):
        assert key in report
    assert report["desktop_entry_installed"] is False


def test_install_reports_a_missing_icon(isolated_home, monkeypatch):
    monkeypatch.setattr("ubuntu_clipboard.install.icon_source", lambda: pathlib.Path("/nonexistent/icon.png"))
    assert install.install_icon() is None


def test_install_surfaces_shortcut_failures(isolated_home, monkeypatch):
    from ubuntu_clipboard.shortcut import Report as ShortcutReport

    broken = ShortcutReport(ok=False)
    broken.add("gsettings rejected binding = '<Super>v' (No such schema)")

    def failing(**_kwargs):
        return broken

    monkeypatch.setattr("ubuntu_clipboard.install.install_shortcut", failing)
    report = install.install_all()
    assert any("No such schema" in warning for warning in report.warnings)
    assert any("--install-shortcut" in warning for warning in report.warnings)


def test_install_reports_a_missing_gsettings(isolated_home, monkeypatch):
    from ubuntu_clipboard.shortcut import ShortcutError

    def explode(**_kwargs):
        raise ShortcutError("gsettings not found — this is not a GNOME session")

    monkeypatch.setattr("ubuntu_clipboard.install.install_shortcut", explode)
    report = install.install_all()
    assert any("not a GNOME session" in warning for warning in report.warnings)
