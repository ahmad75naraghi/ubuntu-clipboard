"""Which keysym the shortcut key sends on each of the user's keyboard layouts.

GNOME matches a shortcut against the keysym of the layout that is *active*, so
``<Super>v`` stops firing the moment the user switches to Persian, Russian or
Greek: that same physical key now sends ``Arabic_ra``, nothing matches, and the
letter is typed into whatever has focus. We cannot bind a physical key —
``xkb``/GTK accelerators are keysyms — so one custom shortcut is registered per
configured layout, each naming the keysym that layout puts on the key.

The keysym is read from libxkbcommon, the very library the compositor uses to
build its keymap, so the name that is written is the keysym the compositor will
deliver. Everything here is best effort: when libxkbcommon, the ``xkb-data``
files or ``gsettings`` are missing, the caller keeps the plain Latin binding.
"""

from __future__ import annotations

import ast
import ctypes
import ctypes.util
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

#: ``xkb`` keycodes are evdev codes plus this offset (see input-event-codes.h).
XKB_KEYCODE_OFFSET = 8
#: Keycode of the physical ``V`` key — the fallback when the key cannot be found.
V_KEYCODE = 47 + XKB_KEYCODE_OFFSET
#: Keysyms at or above this value are "Unicode" keysyms (``0x01000000 + U+xxxx``).
UNICODE_KEYSYM_OFFSET = 0x01000000
#: Keycodes are 8 bit, so 255 is the last one worth asking about.
MAX_KEYCODE = 255
#: Where GNOME keeps the list of configured layouts.
SOURCE_SCHEMA = "org.gnome.desktop.input-sources"
SOURCE_KEY = "sources"
#: The keyboard data package, used for the human readable layout names.
XKB_RULES_DIR = Path("/usr/share/X11/xkb/rules")


@dataclass(frozen=True)
class LayoutBinding:
    """One accelerator, and the keyboard layout that asked for it."""

    accelerator: str
    #: Human readable layout name (``Persian``); empty for the primary binding.
    layout: str = ""
    variant: str = ""


class Backend(Protocol):
    """The two questions asked of a keymap implementation."""

    def keysym(self, layout: str, variant: str, keycode: int) -> int:
        """Keysym ``keycode`` sends on ``layout`` (0 when it cannot be answered)."""

    def name(self, sym: int) -> str:
        """The accelerator name of ``sym`` (``Arabic_ra``, ``v``, …).

        Names come from ``keysymdef.h`` — the same table GTK parses an
        accelerator with. The Persian ``ر`` is the *legacy* keysym
        ``Arabic_ra`` (0x05d1), which is what ``symbols/ir`` puts on ``v``.
        """

    def character(self, sym: int) -> str:
        """The character ``sym`` types, or an empty string."""


# ── accelerator handling ────────────────────────────────────────────────────
def split_accelerator(accelerator: str) -> tuple[str, str]:
    """``"<Super><Alt>v"`` -> ``("<Super><Alt>", "v")``.

    Everything up to the last ``<...>`` group is the modifier prefix; the rest
    is the key (a name such as ``v`` or ``Arabic_ra``, or a character).
    """
    end = accelerator.rfind("<")
    if end == -1:
        return "", accelerator
    close = accelerator.find(">", end)
    if close == -1:
        return "", accelerator
    return accelerator[: close + 1], accelerator[close + 1 :]


def parse_sources(raw: str | None) -> list[tuple[str, str]]:
    """``"[('xkb', 'us'), ('xkb', 'ir')]"`` -> ``[("us", ""), ("ir", "")]``.

    Input methods (``('ibus', …)``) are skipped: they have no fixed keysym for a
    physical key. A variant is written as ``layout(variant)``.
    """
    if not raw:
        return []
    text = raw.strip()
    if text.startswith("@a(ss)"):
        text = text[len("@a(ss)") :].strip()
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        log.debug("cannot parse the input sources %r", raw)
        return []
    if not isinstance(value, (list, tuple)):
        return []
    layouts: list[tuple[str, str]] = []
    for entry in value:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            continue
        kind, identifier = entry
        if str(kind) != "xkb":
            continue
        layout, variant = _split_source(str(identifier))
        if layout and (layout, variant) not in layouts:
            layouts.append((layout, variant))
    return layouts


def _split_source(identifier: str) -> tuple[str, str]:
    """``"us"``, ``"us(dvorak)"`` and ``"us+dvorak"`` all name a variant."""
    for separator in ("(", "+"):
        if separator in identifier:
            layout, _, variant = identifier.partition(separator)
            return layout, variant.rstrip(")")
    return identifier, ""


def configured_layouts(getter: Callable[[str, str], str | None] | None = None) -> list[tuple[str, str]]:
    """The ``(layout, variant)`` pairs GNOME is configured to switch between."""
    if getter is None:
        from .shortcut import get_value  # kept local: the two modules import in either order

        getter = lambda schema, key: get_value(schema, key)  # noqa: E731 - tiny adapter
    return parse_sources(getter(SOURCE_SCHEMA, SOURCE_KEY))


def layout_display_name(layout: str, rules_dir: Path = XKB_RULES_DIR) -> str:
    """``"ir"`` -> ``"Persian"``, using the rules file GNOME's own UI reads."""
    for filename in ("evdev.lst", "base.lst"):
        path = rules_dir / filename
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        section = ""
        for line in lines:
            if line.startswith("!"):
                section = line[2:].strip() if line.startswith("! ") else line[1:].strip()
                continue
            if section != "layout":
                continue
            parts = line.split()
            if parts and parts[0] == layout and len(parts) > 1:
                return " ".join(parts[1:])
    return layout


# ── libxkbcommon ────────────────────────────────────────────────────────────
class _RuleNames(ctypes.Structure):
    """``struct xkb_rule_names``."""

    _fields_ = [
        ("rules", ctypes.c_char_p),
        ("model", ctypes.c_char_p),
        ("layout", ctypes.c_char_p),
        ("variant", ctypes.c_char_p),
        ("options", ctypes.c_char_p),
    ]


class XkbCommon:
    """The libxkbcommon calls needed to ask "what does this key send here?"."""

    def __init__(self, library: str | None = None) -> None:
        name = library or ctypes.util.find_library("xkbcommon") or "libxkbcommon.so.0"
        self._lib = ctypes.CDLL(name)
        self._lib.xkb_context_new.restype = ctypes.c_void_p
        self._lib.xkb_context_new.argtypes = [ctypes.c_int]
        self._lib.xkb_context_unref.argtypes = [ctypes.c_void_p]
        self._lib.xkb_context_unref.restype = ctypes.c_void_p
        self._lib.xkb_keymap_new_from_names.restype = ctypes.c_void_p
        self._lib.xkb_keymap_new_from_names.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_RuleNames),
            ctypes.c_int,
        ]
        self._lib.xkb_keymap_unref.argtypes = [ctypes.c_void_p]
        self._lib.xkb_keymap_unref.restype = ctypes.c_void_p
        self._lib.xkb_state_new.restype = ctypes.c_void_p
        self._lib.xkb_state_new.argtypes = [ctypes.c_void_p]
        self._lib.xkb_state_unref.argtypes = [ctypes.c_void_p]
        self._lib.xkb_state_unref.restype = ctypes.c_void_p
        self._lib.xkb_state_key_get_one_sym.restype = ctypes.c_uint32
        self._lib.xkb_state_key_get_one_sym.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self._lib.xkb_keysym_get_name.restype = ctypes.c_int
        self._lib.xkb_keysym_get_name.argtypes = [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t]
        self._lib.xkb_keysym_to_utf8.restype = ctypes.c_int
        self._lib.xkb_keysym_to_utf8.argtypes = [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t]
        self._context = self._lib.xkb_context_new(0)
        if not self._context:  # pragma: no cover - only with a broken installation
            raise OSError("libxkbcommon refused to create a context")
        self._states: dict[tuple[str, str], int] = {}

    # ── keymaps ────────────────────────────────────────────────────────────
    def _state(self, layout: str, variant: str) -> int:
        """A cached ``xkb_state`` for ``layout``; ``0`` when it cannot be built."""
        cached = self._states.get((layout, variant))
        if cached is not None:
            return cached
        names = _RuleNames(
            rules=b"evdev",
            model=b"pc105",
            layout=layout.encode("utf-8"),
            variant=(variant or "").encode("utf-8"),
            options=None,
        )
        keymap = self._lib.xkb_keymap_new_from_names(self._context, ctypes.byref(names), 0)
        state = self._lib.xkb_state_new(keymap) if keymap else 0
        if keymap and not state:  # pragma: no cover - out of memory
            self._lib.xkb_keymap_unref(keymap)
        if not keymap:
            log.debug("libxkbcommon cannot build a keymap for layout %r", layout)
        self._states[(layout, variant)] = state or 0
        return state or 0

    def keysym(self, layout: str, variant: str, keycode: int) -> int:
        state = self._state(layout, variant)
        if not state:
            return 0
        return int(self._lib.xkb_state_key_get_one_sym(state, keycode))

    def name(self, sym: int) -> str:
        if not sym:
            return ""
        buffer = ctypes.create_string_buffer(64)
        written = self._lib.xkb_keysym_get_name(sym, buffer, ctypes.sizeof(buffer))
        return buffer.value.decode("utf-8", "replace") if written > 0 else ""

    def character(self, sym: int) -> str:
        if not sym:
            return ""
        buffer = ctypes.create_string_buffer(16)
        written = self._lib.xkb_keysym_to_utf8(sym, buffer, ctypes.sizeof(buffer))
        return buffer.value.decode("utf-8", "replace") if written > 0 else ""

    def close(self) -> None:
        for state in self._states.values():
            if state:
                self._lib.xkb_state_unref(state)
        self._states.clear()
        if self._context:
            self._lib.xkb_context_unref(self._context)
            self._context = 0


def default_backend() -> Backend | None:
    """libxkbcommon, or ``None`` when it is not usable on this machine."""
    try:
        return XkbCommon()
    except (OSError, AttributeError, TypeError, SystemError):  # pragma: no cover - no library
        log.debug("libxkbcommon is not available", exc_info=True)
        return None


# ── the answer ──────────────────────────────────────────────────────────────
def _matches(backend: Backend, sym: int, key: str) -> bool:
    """Whether ``sym`` is the key the user wrote in the accelerator."""
    if not sym or not key:
        return False
    if backend.name(sym).lower() == key.lower():
        return True
    character = backend.character(sym)
    return bool(character) and character == key


def keycode_for(
    backend: Backend,
    layouts: Sequence[tuple[str, str]],
    key: str,
    fallback: int = V_KEYCODE,
) -> int | None:
    """The physical key that carries ``key`` in one of ``layouts``.

    The binding is copied to the other layouts by *key position*, so the key has
    to be located first: ``v`` is found in the Latin layout the user configured.
    """
    for layout, variant in layouts:
        for keycode in range(XKB_KEYCODE_OFFSET, MAX_KEYCODE + 1):
            if _matches(backend, backend.keysym(layout, variant, keycode), key):
                return keycode
    if key.lower() == "v":  # the default binding, even without a Latin layout
        return fallback
    return None


def binding_plan(
    primary: str,
    *,
    layouts: Sequence[tuple[str, str]] | None = None,
    backend: Backend | None = None,
) -> list[LayoutBinding]:
    """``primary`` plus one binding per keyboard layout that sends another key.

    Each entry knows the layout it came from, which is what the settings list
    needs: one tiny shortcut per layout, so ``Win+V`` keeps working after the
    user switches to Persian (``ر``), Russian (``м``) or Greek (``ω``). Only the
    primary is returned whenever the answer cannot be computed — a missing
    binding is harmless, a wrong one is not.
    """
    modifiers, key = split_accelerator(primary)
    plan = [LayoutBinding(primary)]
    if not key:
        return plan
    if layouts is None:
        layouts = configured_layouts()
    if not layouts:
        return plan
    if backend is None:
        backend = default_backend()
    if backend is None:
        return plan
    try:
        return _plan(plan, modifiers, key, layouts, backend)
    except Exception:  # noqa: BLE001 - any surprise here only means "extra key skipped"
        log.debug("cannot resolve the keyboard layouts", exc_info=True)
        return [LayoutBinding(primary)]


def _plan(
    plan: list[LayoutBinding],
    modifiers: str,
    key: str,
    layouts: Sequence[tuple[str, str]],
    backend: Backend,
) -> list[LayoutBinding]:
    keycode = keycode_for(backend, layouts, key)
    if keycode is None:
        log.debug("cannot find the physical key for %r", key)
        return plan

    seen = {item.accelerator for item in plan}
    for layout, variant in layouts:
        sym = backend.keysym(layout, variant, keycode)
        if not sym:
            continue
        candidates = [backend.name(sym)]
        if sym >= UNICODE_KEYSYM_OFFSET:
            # A Unicode keysym is spelled ``U044C`` and friends; offer the typed
            # character as well, since the two spellings are not equally well
            # understood by every version of the shortcut parser.
            character = backend.character(sym)
            if character and not character.isascii():
                candidates.append(character)
        label = layout_display_name(layout)
        for candidate in candidates:
            if not candidate or candidate == key:
                continue
            binding = f"{modifiers}{candidate}"
            if binding in seen:
                continue
            seen.add(binding)
            plan.append(LayoutBinding(binding, label, variant))
    return plan


def shortcut_bindings(
    primary: str,
    *,
    layouts: Sequence[tuple[str, str]] | None = None,
    backend: Backend | None = None,
) -> list[str]:
    """The accelerators from :func:`binding_plan`, ``primary`` first."""
    return [item.accelerator for item in binding_plan(primary, layouts=layouts, backend=backend)]


def describe(bindings: Iterable[LayoutBinding]) -> str:
    """``"<Super>Arabic_ra (Persian)"`` for the CLI to print."""
    parts = []
    for item in bindings:
        suffix = f" ({item.layout})" if item.layout else ""
        parts.append(f"{item.accelerator}{suffix}")
    return ", ".join(parts)
