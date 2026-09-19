"""Install/uninstall the user level integration.

Everything that v1 did with ``sed`` and ``cp`` in ``install.sh`` (desktop entry,
icon, autostart, keybinding) lives here as testable Python: the shell script now
only installs distribution packages and the Python package itself.
"""

from __future__ import annotations

import contextlib
import logging
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

from . import APP_ICON, APP_ID, APP_NAME, PROJECT_URL, __version__
from .config import (
    applications_dir,
    autostart_dir,
    cache_dir,
    config_dir,
    data_dir,
    database_path,
    icons_dir,
)
from .shortcut import DEFAULT_BINDING, ShortcutError, resolve_launch_command
from .shortcut import Report as ShortcutReport
from .shortcut import install as install_shortcut
from .shortcut import uninstall as uninstall_shortcut

log = logging.getLogger(__name__)

DESKTOP_FILENAME = f"{APP_ID}.desktop"
ICON_DIR_NAME = "hicolor/512x512/apps"

#: Files created by v1 that must disappear on upgrade/uninstall.
LEGACY_FILES = (
    "applications/ubuntu-clipboard.desktop",
    "applications/ubuntu-clipboard-settings.desktop",
    "autostart/ubuntu-clipboard.desktop",
    "autostart/ubuntu-clipboard-daemon.desktop",
)


@dataclass
class Report:
    """Result of an install/uninstall run."""

    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    shortcut: ShortcutReport | None = None

    def did(self, message: str) -> None:
        self.actions.append(message)
        log.info(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        log.warning(message)


#: Where the icon lives inside the package (a real hicolor layout, so GTK can
#: also be pointed at it directly when the application is run from a checkout).
ICON_RELATIVE_PATH = f"assets/hicolor/512x512/apps/{APP_ICON}.png"


def assets_dir() -> Path:
    """Directory of the bundled assets (``.../ubuntu_clipboard/assets``)."""
    return Path(str(files("ubuntu_clipboard").joinpath("assets")))


def icon_source() -> Path:
    """Path of the bundled application icon."""
    return assets_dir() / ICON_RELATIVE_PATH.removeprefix("assets/")


def desktop_entry_path() -> Path:
    return applications_dir() / DESKTOP_FILENAME


def autostart_path() -> Path:
    return autostart_dir() / DESKTOP_FILENAME


def installed_icon_path() -> Path:
    return icons_dir() / ICON_DIR_NAME / f"{APP_ICON}.png"


def _exec_line(command: Sequence[str], *extra: str) -> str:
    return shlex.join([*command, *extra])


def desktop_entry_text(launch_command: Sequence[str], icon: str = APP_ICON) -> str:
    """Contents of ``~/.local/share/applications/<app id>.desktop``."""
    program = launch_command[0] if launch_command else "ubuntu-clipboard"
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Version=1.1",
            f"Name={APP_NAME}",
            "Name[fa]=کلیپ‌بورد",
            "GenericName=Clipboard history",
            "GenericName[fa]=تاریخچه کلیپ‌بورد",
            "Comment=Windows 11 style clipboard history (Win+V)",
            "Comment[fa]=تاریخچه کلیپ‌بورد شبیه ویندوز ۱۱ (Win+V)",
            f"Exec={_exec_line(launch_command, '--toggle')}",
            f"TryExec={program}",
            f"Icon={icon}",
            "Terminal=false",
            "Categories=Utility;GTK;",
            "Keywords=clipboard;paste;history;win+v;",
            "StartupNotify=true",
            f"StartupWMClass={APP_ID}",
            "X-GNOME-UsesNotifications=true",
            "Actions=Settings;ClearHistory;Quit;",
            "",
            "[Desktop Action Settings]",
            "Name=Preferences",
            "Name[fa]=تنظیمات",
            f"Exec={_exec_line(launch_command, '--settings')}",
            "",
            "[Desktop Action ClearHistory]",
            "Name=Clear history",
            "Name[fa]=پاک کردن تاریخچه",
            f"Exec={_exec_line(launch_command, '--clear')}",
            "",
            "[Desktop Action Quit]",
            "Name=Quit",
            "Name[fa]=خروج",
            f"Exec={_exec_line(launch_command, '--quit')}",
            "",
        ]
    )


def autostart_entry_text(launch_command: Sequence[str], delay: int = 3) -> str:
    """Contents of ``~/.config/autostart/<app id>.desktop``."""
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            f"Name={APP_NAME} (background)",
            "Name[fa]=کلیپ‌بورد (پس‌زمینه)",
            "Comment=Keep the clipboard history running",
            "Comment[fa]=اجرای تاریخچه کلیپ‌بورد در پس‌زمینه",
            f"Exec={_exec_line(launch_command, '--background')}",
            f"Icon={APP_ICON}",
            "Terminal=false",
            "NoDisplay=true",
            "X-GNOME-Autostart-enabled=true",
            f"X-GNOME-Autostart-Delay={delay}",
            "",
        ]
    )


def _write(path: Path, content: str, mode: int = 0o644) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def install_icon() -> Path | None:
    """Copy the bundled icon into the user's hicolor theme."""
    source = icon_source()
    if not source.is_file():
        log.warning("bundled icon missing at %s", source)
        return None
    target = installed_icon_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return target


def install_desktop_entry(launch_command: Sequence[str] | None = None) -> Path:
    command = list(launch_command) if launch_command else resolve_launch_command()
    return _write(desktop_entry_path(), desktop_entry_text(command))


def enable_autostart(launch_command: Sequence[str] | None = None) -> Path:
    command = list(launch_command) if launch_command else resolve_launch_command()
    return _write(autostart_path(), autostart_entry_text(command))


def disable_autostart() -> bool:
    path = autostart_path()
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except OSError as exc:  # pragma: no cover - defensive
        log.warning("cannot remove %s: %s", path, exc)
        return False


def autostart_enabled() -> bool:
    return autostart_path().is_file()


def remove_legacy_files() -> list[Path]:
    """Delete the desktop entries created by v1."""
    removed: list[Path] = []
    roots = {"applications": applications_dir(), "autostart": autostart_dir()}
    for relative in LEGACY_FILES:
        folder, _, name = relative.partition("/")
        path = roots[folder] / name
        if path.is_file():
            try:
                path.unlink()
                removed.append(path)
            except OSError:  # pragma: no cover - defensive
                log.debug("cannot remove legacy file %s", path, exc_info=True)
    return removed


def refresh_caches(which=shutil.which, runner=subprocess.run) -> None:
    """Best effort ``update-desktop-database`` / ``gtk-update-icon-cache``."""
    commands = []
    if which("update-desktop-database"):
        commands.append(["update-desktop-database", str(applications_dir())])
    if which("gtk4-update-icon-cache"):
        commands.append(["gtk4-update-icon-cache", "-q", "-t", str(icons_dir() / "hicolor")])
    elif which("gtk-update-icon-cache"):
        commands.append(["gtk-update-icon-cache", "-q", "-t", str(icons_dir() / "hicolor")])
    for command in commands:
        try:
            runner(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=False)
        except (OSError, subprocess.SubprocessError):  # pragma: no cover - cosmetic
            log.debug("cache refresh %s failed", command[0], exc_info=True)


def install_all(
    *,
    launch_command: Sequence[str] | None = None,
    enable_startup: bool = True,
    shortcut_binding: str = DEFAULT_BINDING,
    install_keybinding: bool = True,
    take_binding: bool = False,
) -> Report:
    """Install every user level integration piece."""
    report = Report()
    command = list(launch_command) if launch_command else resolve_launch_command()
    report.did("launcher command: " + shlex.join(command))

    icon = install_icon()
    if icon:
        report.did(f"icon installed: {icon}")
    else:
        report.warn("application icon could not be installed")

    entry = install_desktop_entry(command)
    report.did(f"desktop entry: {entry}")

    if enable_startup:
        autostart = enable_autostart(command)
        report.did(f"autostart: {autostart}")

    for path in remove_legacy_files():
        report.did(f"removed legacy file: {path}")

    refresh_caches()

    if install_keybinding:
        try:
            result = install_shortcut(
                binding=shortcut_binding, launch_command=command, take_binding=take_binding
            )
            report.shortcut = result
            for message in result.messages:
                if result.ok and not message.startswith("warning:"):
                    report.did(message)
                else:
                    report.warn(message)
            if not result.ok:
                report.warn(
                    "shortcut installation did not complete — run 'ubuntu-clipboard "
                    "--install-shortcut' for the exact reason, or add it by hand in "
                    "Settings → Keyboard → Custom Shortcuts"
                )
        except ShortcutError as exc:
            report.warn(str(exc))
    return report


def uninstall_all(*, remove_config: bool = False, remove_history: bool = False) -> Report:
    """Undo :func:`install_all`. The Python package itself is left alone."""
    report = Report()
    try:
        result = uninstall_shortcut()
        report.shortcut = result
        if result.ok:
            report.did("shortcut removed")
        else:
            report.warn("shortcut could not be removed")
    except ShortcutError as exc:
        report.warn(str(exc))

    if disable_autostart():
        report.did(f"autostart removed: {autostart_path()}")

    for path in (desktop_entry_path(), installed_icon_path()):
        if path.is_file():
            try:
                path.unlink()
                report.did(f"removed {path}")
            except OSError as exc:  # pragma: no cover - defensive
                report.warn(f"cannot remove {path}: {exc}")

    for path in remove_legacy_files():
        report.did(f"removed legacy file: {path}")

    refresh_caches()

    if remove_history:
        # Remove the database files only: the data directory also holds the
        # virtualenv used by scripts/install.sh, which must survive a purge.
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{database_path()}{suffix}")
            if path.exists():
                with contextlib.suppress(OSError):
                    path.unlink()
                    report.did(f"removed {path}")
    if remove_config and config_dir().exists():
        shutil.rmtree(config_dir(), ignore_errors=True)
        report.did(f"removed configuration: {config_dir()}")
    if not remove_history and not remove_config:
        report.did(f"history kept in {data_dir()}")
    shutil.rmtree(cache_dir(), ignore_errors=True)
    return report


def environment_report() -> dict[str, object]:
    """Facts used by ``--status`` and ``--collect-logs``."""
    from .clipboard import probe
    from .paste import can_paste

    capabilities = probe()
    return {
        "version": __version__,
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "session": capabilities.session,
        "tools": capabilities.tools,
        "can_read_text": capabilities.can_read_text,
        "can_write_text": capabilities.can_write_text,
        "auto_paste": can_paste(),
        "desktop_entry": str(desktop_entry_path()),
        "desktop_entry_installed": desktop_entry_path().is_file(),
        "icon": str(installed_icon_path()),
        "icon_installed": installed_icon_path().is_file(),
        "autostart": str(autostart_path()),
        "autostart_enabled": autostart_enabled(),
        "project": PROJECT_URL,
    }
