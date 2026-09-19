#!/usr/bin/env python3
"""Static GTK/GDK/libadwaita API check that runs headless.

The GUI cannot be imported in CI (there is no display and PyGObject is not on
PyPI in a usable form), so this script verifies the *API surface* instead:

* every ``Gtk.Thing`` / ``Gdk.Thing`` / ``Adw.Thing`` name exists in the
  PyGObject stubs, and
* every attribute used on an object created with ``Gtk.ClassName(...)`` or
  ``Adw.ClassName.new(...)`` exists on that class (inherited members included).

It catches typos such as ``Gtk.AlertDialog`` on an older GTK or
``Gdk.ToplevelState.ACTIVE`` (the real flag is ``FOCUSED``) before a user does.

Usage::

    pip install --no-deps PyGObject-stubs
    python3 scripts/check_gtk_api.py [--verbose]

Exits with 1 when a problem is found, 2 when the stubs are missing.
"""

from __future__ import annotations

import argparse
import ast
import os
import pathlib
import re
import sys

MODULES = ("Gtk", "Gdk", "Gio", "GLib", "Adw", "Pango", "GObject", "GdkPixbuf")
PACKAGE = "ubuntu_clipboard"


def find_stub_root() -> pathlib.Path | None:
    """Locate the ``gi-stubs/repository`` directory shipped by PyGObject-stubs."""
    override = os.environ.get("GTK_STUBS_DIR")
    if override:
        path = pathlib.Path(override)
        return path if path.is_dir() else None
    for entry in sys.path:
        if not entry:
            continue
        candidate = pathlib.Path(entry) / "gi-stubs" / "repository"
        if candidate.is_dir():
            return candidate
    return None


class Stubs:
    """Top level names and class members, extracted from the ``.pyi`` files."""

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        #: module -> name -> member names (``None`` for functions/constants)
        self.names: dict[str, dict[str, set[str] | None]] = {}
        #: module -> class -> base class names
        self.bases: dict[str, dict[str, list[str]]] = {}
        self._cache: dict[tuple[str, str, str], bool] = {}
        for module in MODULES:
            path = root / f"{module}.pyi"
            if path.is_file():
                self._load(module, path)

    def _load(self, module: str, path: pathlib.Path) -> None:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: dict[str, set[str] | None] = {}
        bases: dict[str, list[str]] = {}
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                members: set[str] = set()
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        members.add(item.name)
                    elif isinstance(item, ast.Assign):
                        members.update(t.id for t in item.targets if isinstance(t, ast.Name))
                    elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        members.add(item.target.id)
                names[node.name] = members
                bases[node.name] = [ast.unparse(base) for base in node.bases]
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names[node.name] = None
            elif isinstance(node, ast.Assign):
                names.update({t.id: None for t in node.targets if isinstance(t, ast.Name)})
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names[node.target.id] = None
        self.names[module] = names
        self.bases[module] = bases

    def is_class(self, module: str, name: str) -> bool:
        return name in self.bases.get(module, {})

    def has_member(
        self,
        module: str,
        cls: str,
        attribute: str,
        seen: frozenset[tuple[str, str]] = frozenset(),
    ) -> bool:
        """MRO aware membership test (``AttributeError`` candidates return False)."""
        key = (module, cls, attribute)
        if not seen and key in self._cache:
            return self._cache[key]
        if (module, cls) in seen:
            return False
        if not self.is_class(module, cls):
            # Unknown GTK class (newer stub or a type we do not model) -> do not
            # report; base classes from other libraries (enum.IntFlag, ...) grant
            # nothing we know about.
            return module in MODULES
        members = self.names[module][cls]
        result = bool(members and attribute in members)
        if not result:
            for base in self.bases[module][cls]:
                base_module, _, base_name = base.rpartition(".")
                if not base_module:  # a bare name refers to the same module
                    base_module, base_name = module, base
                if self.has_member(base_module, base_name, attribute, seen | {(module, cls)}):
                    result = True
                    break
        if not seen:
            self._cache[key] = result
        return result


class Visitor(ast.NodeVisitor):
    """Collects ``variable -> (module, class)`` and checks attribute accesses."""

    def __init__(self, stubs: Stubs, filename: pathlib.Path) -> None:
        self.stubs = stubs
        self.filename = filename
        self.problems: list[str] = []
        self.types: dict[str, tuple[str, str]] = {}

    # -- scope handling ----------------------------------------------------
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - ast API
        saved = dict(self.types)
        self.generic_visit(node)
        self.types = saved

    def visit_AsyncFunctionDef(self, node) -> None:  # noqa: N802 - ast API
        self.visit_FunctionDef(node)

    # -- assignments -------------------------------------------------------
    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802 - ast API
        inferred = self._infer(node.value)
        if inferred:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.types[target.id] = inferred
                elif (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    self.types[f"self.{target.attr}"] = inferred
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802 - ast API
        inferred = self._infer(node.value) if node.value is not None else None
        if inferred and isinstance(node.target, ast.Name):
            self.types[node.target.id] = inferred
        self.generic_visit(node)

    @staticmethod
    def _infer(value: ast.expr | None) -> tuple[str, str] | None:
        """``Gtk.Box(...)`` / ``Gtk.Picture.new_for_paintable(...)`` -> (module, class)."""
        if not isinstance(value, ast.Call):
            return None
        func = value.func
        if isinstance(func, ast.Attribute):
            owner = func.value
            if isinstance(owner, ast.Name) and owner.id in MODULES:
                return (owner.id, func.attr)
            if (
                isinstance(owner, ast.Attribute)
                and isinstance(owner.value, ast.Name)
                and owner.value.id in MODULES  # Class.new(...) style constructors
            ):
                return (owner.value.id, owner.attr)
        return None

    # -- attribute access --------------------------------------------------
    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802 - ast API
        key = None
        if isinstance(node.value, ast.Name):
            key = node.value.id
        elif (
            isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
        ):
            key = f"self.{node.value.attr}"
        if key and key in self.types:
            module, cls = self.types[key]
            if not self.stubs.has_member(module, cls, node.attr):
                self.problems.append(
                    f"{self.filename}:{node.lineno}: {key} ({module}.{cls}) has no attribute {node.attr!r}"
                )
        self.generic_visit(node)


def check_names(stubs: Stubs, files: list[pathlib.Path]) -> list[str]:
    """Every ``Module.Name`` / ``Module.Class.member`` reference must exist."""
    pattern = re.compile(
        r"\b(" + "|".join(MODULES) + r")\.([A-Za-z_][A-Za-z0-9_]*)(?:\.([A-Za-z_][A-Za-z0-9_]*))?"
    )
    problems: list[str] = []
    for filename in files:
        for lineno, line in enumerate(filename.read_text(encoding="utf-8").splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            for match in pattern.finditer(line):
                module, first, second = match.group(1), match.group(2), match.group(3)
                table = stubs.names.get(module)
                if table is None:
                    continue
                if first not in table:
                    problems.append(f"{filename}:{lineno}: {module}.{first} does not exist")
                    continue
                if second and table[first] is not None and not stubs.has_member(module, first, second):
                    problems.append(f"{filename}:{lineno}: {module}.{first}.{second} does not exist")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verbose", action="store_true", help="show what was checked")
    parser.add_argument("paths", nargs="*", default=[PACKAGE], help="files or directories to check")
    args = parser.parse_args(argv)

    root = find_stub_root()
    if root is None:
        print("PyGObject stubs not found — run: pip install --no-deps PyGObject-stubs", file=sys.stderr)
        return 2
    stubs = Stubs(root)

    files: list[pathlib.Path] = []
    for raw in args.paths:
        path = pathlib.Path(raw)
        files.extend(sorted(path.rglob("*.py")) if path.is_dir() else [path])

    problems = check_names(stubs, files)
    for filename in files:
        visitor = Visitor(stubs, filename)
        visitor.visit(ast.parse(filename.read_text(encoding="utf-8")))
        problems.extend(visitor.problems)

    if args.verbose:
        print(f"stubs: {root}")
        print(f"files: {len(files)}")
    if problems:
        print(f"{len(problems)} problem(s) found:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"GTK API check passed for {len(files)} file(s)")
    return 0


if __name__ == "__main__":  # pragma: no cover - developer tool
    raise SystemExit(main())
