#!/usr/bin/env python3
"""Static GTK/GDK/libadwaita API check that runs headless.

The GUI cannot be imported in CI (there is no display and PyGObject is not on
PyPI in a usable form), so this script verifies the *API surface* instead:

* every ``Gtk.Thing`` / ``Gdk.Thing`` / ``Adw.Thing`` name exists in the
  PyGObject stubs,
* every attribute used on an object created with ``Gtk.ClassName(...)`` or
  ``Adw.ClassName.new(...)`` exists on that class (inherited members included),
* every namespace imported from ``gi.repository`` is pinned with
  ``gi.require_version`` (otherwise PyGObject warns and may load GTK 3), and
* every ``self.attribute`` used inside a class derived from a GTK/GDK/Adw class
  exists on that class or its bases.

It catches typos such as ``Gtk.AlertDialog`` on an older GTK,
``Gdk.ToplevelState.ACTIVE`` (the real flag is ``FOCUSED``) or
``self.set_application_name(...)`` (``GApplication`` has no such method — the
GLib function ``GLib.set_application_name`` is the real one) before a user does.

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

#: Namespaces that only ever have one version, so ``gi.require_version`` is not
#: needed (PyGObject does not warn for them either).
SINGLE_VERSION = frozenset({"GLib", "GObject", "Gio"})

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

    def __init__(self, stubs: Stubs, filename: pathlib.Path, project: ProjectModel | None = None) -> None:
        self.stubs = stubs
        self.filename = filename
        self.problems: list[str] = []
        self.types: dict[str, tuple[str, str]] = {}
        self.project = project or ProjectModel(ast.Module(body=[], type_ignores=[]))
        self.class_stack: list[str] = []

    # -- scope handling ----------------------------------------------------
    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802 - ast API
        self.class_stack.append(node.name)
        saved = dict(self.types)
        self.generic_visit(node)
        self.types = saved
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - ast API
        saved = dict(self.types)
        self.generic_visit(node)
        self.types = saved

    def visit_AsyncFunctionDef(self, node) -> None:  # noqa: N802 - ast API
        self.visit_FunctionDef(node)

    def _check_self_attribute(self, node: ast.Attribute) -> None:
        """``self.thing`` inside a project class must exist on it or its GTK bases."""
        if not self.class_stack or node.attr.startswith("__"):
            return
        cls = self.class_stack[-1]
        if node.attr in self.project.project_members(cls):
            return
        stub_bases = self.project.stub_bases(cls)
        if not stub_bases:
            return
        if any(self.stubs.has_member(module, name, node.attr) for module, name in stub_bases):
            return
        described = ", ".join(f"{module}.{name}" for module, name in stub_bases)
        self.problems.append(
            f"{self.filename}:{node.lineno}: self.{node.attr} is not defined by {cls} nor by {described}"
        )

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
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            self._check_self_attribute(node)
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


def check_require_version(files: list[pathlib.Path]) -> list[str]:
    """Every ``from gi.repository import X`` needs a matching ``require_version``.

    Without it PyGObject prints ``PyGIWarning`` and loads whichever version of
    the namespace happens to be installed first (GTK 3 instead of GTK 4).
    """
    problems: list[str] = []
    for filename in files:
        tree = ast.parse(filename.read_text(encoding="utf-8"))
        pinned: set[str] = set()
        imports: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "require_version"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "gi"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    pinned.add(node.args[0].value)
            elif isinstance(node, ast.ImportFrom) and node.module == "gi.repository":
                imports.extend((node.lineno, alias.name) for alias in node.names)
        for lineno, namespace in sorted(imports):
            if namespace in SINGLE_VERSION or namespace in pinned:
                continue
            problems.append(
                f'{filename}:{lineno}: {namespace} is imported without gi.require_version("{namespace}", ...)'
            )
    return problems


class ProjectModel:
    """Classes defined by the project, their bases and their members."""

    def __init__(self, tree: ast.Module) -> None:
        #: class name -> base expressions (as written)
        self.bases: dict[str, list[ast.expr]] = {}
        #: class name -> member names (methods, class attributes, ``self.x = ...``)
        self.members: dict[str, set[str]] = {}
        #: module level ``name = expression`` aliases (``A if cond else B``)
        self.aliases: dict[str, ast.expr] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                self.bases[node.name] = list(node.bases)
                members = self.members.setdefault(node.name, set())
                for item in ast.walk(node):
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        members.add(item.name)
                    elif isinstance(item, ast.Assign):
                        for target in item.targets:
                            if isinstance(target, ast.Name):
                                members.add(target.id)
                            elif (
                                isinstance(target, ast.Attribute)
                                and isinstance(target.value, ast.Name)
                                and target.value.id == "self"
                            ):
                                members.add(target.attr)
                    elif isinstance(item, ast.AnnAssign) and isinstance(
                        item.target, (ast.Name, ast.Attribute)
                    ):
                        target = item.target
                        if isinstance(target, ast.Name):
                            members.add(target.id)
                        elif isinstance(target.value, ast.Name) and target.value.id == "self":
                            members.add(target.attr)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.aliases[target.id] = node.value

    def bases_of(self, name: str, seen: frozenset[str] = frozenset()) -> list[ast.expr]:
        """Base expressions of a project class, aliases resolved."""
        if name in seen:
            return []
        result: list[ast.expr] = []
        for base in self.bases.get(name, []):
            if isinstance(base, ast.Name) and base.id in self.aliases:
                alias = self.aliases[base.id]
                alternatives = [alias.body, alias.orelse] if isinstance(alias, ast.IfExp) else [alias]
                for alternative in alternatives:
                    if isinstance(alternative, ast.Name) and alternative.id in self.bases:
                        result.extend(self.bases_of(alternative.id, seen | {name}))
                    else:
                        result.append(alternative)
            elif isinstance(base, ast.Name) and base.id in self.bases:
                result.extend(self.bases_of(base.id, seen | {name}))
            else:
                result.append(base)
        return result

    def project_members(self, name: str, seen: frozenset[str] = frozenset()) -> set[str]:
        """Members defined by a project class and its project base classes."""
        if name in seen:
            return set()
        found = set(self.members.get(name, set()))
        for base in self.bases.get(name, []):
            if isinstance(base, ast.Name) and base.id in self.bases:
                found |= self.project_members(base.id, seen | {name})
        return found

    def stub_bases(self, name: str) -> list[tuple[str, str]]:
        """(module, class) pairs from the stubs that this project class derives from."""
        found: list[tuple[str, str]] = []
        for base in self.bases_of(name):
            if (
                isinstance(base, ast.Attribute)
                and isinstance(base.value, ast.Name)
                and base.value.id in MODULES
            ):
                found.append((base.value.id, base.attr))
        return found


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

    problems = check_require_version(files)
    problems.extend(check_names(stubs, files))
    for filename in files:
        tree = ast.parse(filename.read_text(encoding="utf-8"))
        visitor = Visitor(stubs, filename, ProjectModel(tree))
        visitor.visit(tree)
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
