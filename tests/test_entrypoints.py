"""End to end runs of the real entry points, exactly as a user starts them.

Every other test imports the package in-process. These start a *new* interpreter
with ``python -m ubuntu_clipboard`` (and with the console scripts ``pip``
creates), so the parts that only fail in a real run are covered as well: import
order, argument parsing, the ``Gtk.Application`` lifecycle (``do_startup`` →
command line → ``do_shutdown``), the files written under ``$XDG_*`` and the exit
codes.

Real GTK is replaced by the doubles in :mod:`tests.gtk_double`, which are
written to disk as a ``gi`` package and put on ``PYTHONPATH``.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from ubuntu_clipboard import __version__, install
from ubuntu_clipboard.storage import HistoryStore

from tests import gtk_double

TIMEOUT = 90
#: Console script name → callable in :mod:`ubuntu_clipboard.cli`.
SCRIPTS = {"ubuntu-clipboard": "main", "ubuntu-clipboard-daemon": "main_daemon"}
needs_no_gtk = pytest.mark.skipif(
    importlib.util.find_spec("gi") is not None,
    reason="a real PyGObject is importable, so the CLI would open a window",
)


class Cli:
    """Runs the application in a fresh interpreter with an isolated home."""

    def __init__(self, root: Path, *, with_gtk: bool = True, clipboard_text: str | None = None) -> None:
        self.root = root
        self.env = dict(os.environ)
        self.env.update(
            HOME=str(root / "home"),
            XDG_CONFIG_HOME=str(root / "config"),
            XDG_DATA_HOME=str(root / "data"),
            XDG_CACHE_HOME=str(root / "cache"),
            XDG_RUNTIME_DIR=str(root / "runtime"),
            PYTHONUNBUFFERED="1",
        )
        self.env.pop("UBUNTU_CLIPBOARD_DEBUG", None)
        for directory in ("home", "config", "data", "cache", "runtime"):
            (root / directory).mkdir(parents=True, exist_ok=True)
        entries = [str(Path(__file__).resolve().parent.parent)]
        if with_gtk:
            entries.insert(0, str(gtk_double.install_double_package(root / "pysite", clipboard_text)))
        self.env["PYTHONPATH"] = os.pathsep.join(entries)

    # ── paths ──────────────────────────────────────────────────────────────
    @property
    def log_file(self) -> Path:
        return Path(self.env["XDG_CACHE_HOME"]) / "ubuntu-clipboard" / "ubuntu-clipboard.log"

    @property
    def database(self) -> Path:
        return Path(self.env["XDG_DATA_HOME"]) / "ubuntu-clipboard" / "history.db"

    def log_text(self) -> str:
        return self.log_file.read_text(encoding="utf-8") if self.log_file.exists() else ""

    # ── runners ────────────────────────────────────────────────────────────
    def run(self, *arguments: str) -> subprocess.CompletedProcess:
        """``python -m ubuntu_clipboard`` with ``arguments``."""
        return self._spawn([sys.executable, "-m", "ubuntu_clipboard", *arguments])

    def run_script(self, name: str, *arguments: str) -> subprocess.CompletedProcess:
        """The console script ``name``, as ``pip install`` would create it."""
        script = self.root / name
        entry_point = SCRIPTS[name]
        script.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            "from ubuntu_clipboard.cli import {entry_point}\n"
            "sys.exit({entry_point}(sys.argv[1:]))\n".format(entry_point=entry_point),
            encoding="utf-8",
        )
        script.chmod(0o755)
        return self._spawn([sys.executable, str(script), *arguments])

    def _spawn(self, command: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=self.env,
            cwd=str(self.root),
            timeout=TIMEOUT,
            check=False,
        )


@pytest.fixture
def make_cli(tmp_path):
    def factory(*, with_gtk: bool = True, clipboard_text: str | None = None) -> Cli:
        return Cli(tmp_path, with_gtk=with_gtk, clipboard_text=clipboard_text)

    return factory


@pytest.fixture
def cli(make_cli) -> Cli:
    return make_cli()


# ── the command line without GTK ───────────────────────────────────────────
@needs_no_gtk
def test_version_and_diagnostics_work_without_gtk(make_cli):
    headless = make_cli(with_gtk=False)

    version = headless.run("--version")
    assert version.returncode == 0, version.stderr
    assert version.stdout.strip() == f"Ubuntu Clipboard {__version__}"

    status = headless.run("--status")
    assert status.returncode == 0, status.stderr
    assert "Ubuntu Clipboard" in status.stdout
    assert str(headless.database) in status.stdout

    listing = headless.run("--list")
    assert listing.returncode == 0, listing.stderr
    assert "تاریخچه خالی است" in listing.stdout

    assert headless.run("--logs", "5").returncode == 0


@needs_no_gtk
def test_window_commands_explain_a_missing_gtk(make_cli):
    proc = make_cli(with_gtk=False).run("--toggle")
    assert proc.returncode == 1
    assert "GTK 4 is required" in proc.stderr


def test_conflicting_options_are_rejected(cli):
    proc = cli.run("--purge")
    assert proc.returncode == 2
    assert "uninstall" in proc.stderr


# ── desktop integration, through the real entry point ──────────────────────
def test_install_status_and_purge(cli):
    installed = cli.run("--install")
    assert installed.returncode == 0, installed.stderr

    data_home = Path(cli.env["XDG_DATA_HOME"])
    desktop = data_home / "applications" / f"{install.APP_ID}.desktop"
    autostart = Path(cli.env["XDG_CONFIG_HOME"]) / "autostart" / f"{install.APP_ID}.desktop"
    icon = data_home / "icons" / "hicolor" / "512x512" / "apps" / "ubuntu-clipboard.png"
    assert desktop.is_file() and autostart.is_file()
    assert icon.is_file() and icon.stat().st_size > 0
    assert "--toggle" in desktop.read_text(encoding="utf-8")

    # Seeding proves the CLI reads the very database the application writes.
    store = HistoryStore(db_path=cli.database)
    store.add_text("written by the application")
    store.close()

    status = cli.run("--status")
    assert status.returncode == 0, status.stderr
    assert "items            1" in status.stdout
    assert "written by the application" in cli.run("--list", "5").stdout

    purged = cli.run("--uninstall", "--purge")
    assert purged.returncode == 0, purged.stderr
    assert not desktop.exists() and not autostart.exists() and not icon.exists()
    assert not cli.database.exists()
    assert not (Path(cli.env["XDG_CONFIG_HOME"]) / "ubuntu-clipboard").exists()


# ── the GUI path, through the real entry point ─────────────────────────────
def test_toggle_builds_the_window_and_shuts_down(cli):
    proc = cli.run("--debug", "--toggle")
    assert proc.returncode == 0, proc.stderr
    log = cli.log_text()
    assert "starting" in log
    assert "dispatching command 'toggle'" in log
    assert "shutting down" in log


def test_background_keeps_running_without_a_window(cli):
    proc = cli.run("--background")
    assert proc.returncode == 0, proc.stderr
    assert "running in background" in cli.log_text()


def test_settings_show_hide_and_quit(cli):
    assert cli.run("--settings").returncode == 0
    assert cli.run("--show").returncode == 0
    assert cli.run("--hide").returncode == 0
    assert cli.run("--quit").returncode == 0
    assert "shutting down" in cli.log_text()


def test_a_new_database_is_created_on_first_start(cli):
    assert not cli.database.exists()
    assert cli.run("--background").returncode == 0
    assert cli.database.is_file()


def test_clipboard_content_is_captured_at_startup(make_cli):
    cli = make_cli(clipboard_text="copied before the first start")
    assert cli.run("--background").returncode == 0

    store = HistoryStore(db_path=cli.database)
    try:
        assert [item.preview for item in store.list()] == ["copied before the first start"]
    finally:
        store.close()
    assert "copied before the first start" in cli.run("--list", "5").stdout


# ── the console scripts ────────────────────────────────────────────────────
def test_daemon_script_maps_to_the_background_command(cli):
    proc = cli.run_script("ubuntu-clipboard-daemon")
    assert proc.returncode == 0, proc.stderr
    assert "running in background" in cli.log_text()


def test_main_script_prints_the_version(cli):
    proc = cli.run_script("ubuntu-clipboard", "--version")
    assert proc.returncode == 0, proc.stderr
    assert __version__ in proc.stdout
