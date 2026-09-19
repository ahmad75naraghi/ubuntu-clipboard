"""The GTK API checker has to actually catch the mistakes it exists for.

Two real crashes shipped in 2.0.0 that the checker missed:

* ``Gdk`` imported without ``gi.require_version`` (PyGObject warns and may load
  GTK 3), and
* ``self.set_application_name(...)`` — ``Gio.Application`` has no such method.

The rules that now cover them are tested here, so they cannot silently rot.
The stub based parts are skipped when PyGObject-stubs is not installed; the
``require_version`` rule needs no stubs at all and always runs.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "ubuntu_clipboard"
CHECKER = REPO_ROOT / "scripts" / "check_gtk_api.py"

SINGLE_VERSION = {"GLib", "GObject", "Gio"}


def run_checker(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER), *map(str, paths or [PACKAGE])],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )


def test_every_gtk_import_is_version_pinned():
    """``from gi.repository import Gdk`` without a pin is what printed PyGIWarning."""
    unpinned: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        pinned = set()
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                func = node.func
                if func.attr == "require_version" and node.args:
                    pinned.add(getattr(node.args[0], "value", None))
            elif isinstance(node, ast.ImportFrom) and node.module == "gi.repository":
                imports.extend(alias.name for alias in node.names)
        unpinned.extend(
            f"{path.relative_to(REPO_ROOT)}: {namespace}"
            for namespace in imports
            if namespace not in SINGLE_VERSION and namespace not in pinned
        )
    assert unpinned == [], "gi.repository imports without gi.require_version:\n" + "\n".join(unpinned)


def test_checker_passes_on_the_package():
    result = run_checker()
    if result.returncode == 2:  # pragma: no cover - stubs not installed
        pytest.skip("PyGObject stubs are not installed")
    assert result.returncode == 0, result.stdout + result.stderr


# ── the rules must have teeth ──────────────────────────────────────────────
BUGGY_SOURCE = """
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

HAS_ADW = False
_ApplicationBase = Gtk.Application


class ClipboardApplication(_ApplicationBase):
    def __init__(self) -> None:
        super().__init__()
        self.config = None
        self.set_application_name("Clipboard")
        self.set_application_id("io.github.example.App")


def make() -> Gtk.Box:
    return Gtk.Box()
"""


@pytest.fixture
def buggy_file(tmp_path):
    path = tmp_path / "buggy_app.py"
    path.write_text(BUGGY_SOURCE, encoding="utf-8")
    return path


def test_checker_flags_an_unpinned_namespace(buggy_file):
    result = run_checker(buggy_file)
    if result.returncode == 2:  # pragma: no cover - stubs not installed
        pytest.skip("PyGObject stubs are not installed")
    assert result.returncode == 1
    assert 'Gdk is imported without gi.require_version("Gdk"' in result.stdout


def test_checker_flags_a_method_the_gtk_class_does_not_have(buggy_file):
    result = run_checker(buggy_file)
    if result.returncode == 2:  # pragma: no cover - stubs not installed
        pytest.skip("PyGObject stubs are not installed")
    assert result.returncode == 1
    assert "self.set_application_name is not defined by ClipboardApplication" in result.stdout
    # ... and a legal call in the same class must not be reported
    assert "set_application_id" not in result.stdout
