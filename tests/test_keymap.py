"""Keyboard layouts: which key the shortcut has to be bound to on each of them."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from ubuntu_clipboard import clipboard, keymap

# Values from the real X11/xkb tables (keysymdef.h / input-event-codes.h).
V_KEY = 47 + 8
SYM_V = 0x0076
SYM_ARABIC_RA = 0x05D1  # the legacy keysym of the Persian letter ر
SYM_UNICODE_REH = 0x01000631  # …and its Unicode keysym (U+0631)
SYM_CYRILLIC_EM = 0x06ED  # м, the V key on the Russian layout
SYM_Y = 0x0079


class FakeBackend:
    """Answers the two questions :mod:`keymap` asks, from a table."""

    def __init__(self, layouts: dict[tuple[str, str], dict[int, int]]) -> None:
        self.layouts = layouts

    def keysym(self, layout: str, variant: str, keycode: int) -> int:
        return self.layouts.get((layout, variant), {}).get(keycode, 0)

    def name(self, sym: int) -> str:
        return {
            SYM_V: "v",
            SYM_ARABIC_RA: "Arabic_ra",
            SYM_UNICODE_REH: "U0631",
            SYM_CYRILLIC_EM: "Cyrillic_em",
            SYM_Y: "y",
        }.get(sym, "")

    def character(self, sym: int) -> str:
        return {SYM_ARABIC_RA: "ر", SYM_UNICODE_REH: "ر", SYM_CYRILLIC_EM: "м"}.get(sym, "")


def persian_and_latin() -> FakeBackend:
    return FakeBackend({("us", ""): {V_KEY: SYM_V}, ("ir", ""): {V_KEY: SYM_ARABIC_RA}})


# ── accelerators ────────────────────────────────────────────────────────────
def test_split_accelerator_keeps_the_modifiers():
    assert keymap.split_accelerator("<Super>v") == ("<Super>", "v")
    assert keymap.split_accelerator("<Super><Alt>v") == ("<Super><Alt>", "v")
    assert keymap.split_accelerator("<Super>Arabic_ra") == ("<Super>", "Arabic_ra")
    assert keymap.split_accelerator("<Super>ر") == ("<Super>", "ر")
    assert keymap.split_accelerator("v") == ("", "v")
    assert keymap.split_accelerator("<Super>") == ("<Super>", "")


# ── the session's layouts ───────────────────────────────────────────────────
def test_parse_sources_reads_the_layouts():
    assert keymap.parse_sources("[('xkb', 'us'), ('xkb', 'ir')]") == [("us", ""), ("ir", "")]
    assert keymap.parse_sources("@a(ss) [('xkb', 'us'), ('xkb', 'ir')]") == [("us", ""), ("ir", "")]


def test_parse_sources_reads_variants():
    assert keymap.parse_sources("[('xkb', 'us(dvorak)')]") == [("us", "dvorak")]


def test_parse_sources_skips_input_methods_and_junk():
    sources = [("ibus", "libpinyin"), ("xkb", "us")]
    assert keymap.parse_sources(str(sources)) == [("us", "")]
    assert keymap.parse_sources("@a(ss) []") == []
    assert keymap.parse_sources("not a variant") == []
    assert keymap.parse_sources(None) == []


def test_configured_layouts_uses_the_settings(monkeypatch):
    monkeypatch.setattr(
        "ubuntu_clipboard.shortcut.get_value",
        lambda schema, key, **_kwargs: "[('xkb', 'us'), ('xkb', 'ir')]",
    )
    assert keymap.configured_layouts() == [("us", ""), ("ir", "")]


def test_layout_display_names_come_from_the_rules_file(tmp_path):
    (tmp_path / "evdev.lst").write_text(
        "! model\n  pc105  Generic\n\n! layout\n  us             English (US)\n  ir             Persian\n",
        encoding="utf-8",
    )
    assert keymap.layout_display_name("ir", tmp_path) == "Persian"
    assert keymap.layout_display_name("de", tmp_path) == "de"  # unknown: the code itself


def test_layout_display_name_without_the_data(tmp_path):
    assert keymap.layout_display_name("ir", tmp_path) == "ir"


# ── finding the key ─────────────────────────────────────────────────────────
def test_keycode_for_finds_the_physical_key():
    backend = FakeBackend(
        {
            ("us", ""): {V_KEY: SYM_V, 20 + 8: 0x0079},  # y
            ("ir", ""): {V_KEY: SYM_ARABIC_RA, 20 + 8: 0x06CC},
        }
    )
    assert keymap.keycode_for(backend, [("us", ""), ("ir", "")], "v") == V_KEY
    assert keymap.keycode_for(backend, [("us", ""), ("ir", "")], "y") == 28


def test_keycode_for_falls_back_to_the_v_key():
    backend = FakeBackend({("ir", ""): {V_KEY: SYM_ARABIC_RA}})
    assert keymap.keycode_for(backend, [("ir", "")], "v") == keymap.V_KEYCODE
    assert keymap.keycode_for(backend, [("ir", "")], "q") is None


# ── the plan ────────────────────────────────────────────────────────────────
def test_a_persian_layout_gets_its_own_binding(monkeypatch):
    """``symbols/ir`` (ISIRI 9147) puts the legacy keysym ``Arabic_ra`` — 0x05d1,
    U+0631 «ر» — on the physical ``V`` key, so that is what has to be written."""
    monkeypatch.setattr(keymap, "layout_display_name", lambda layout, *_args: "Persian")
    plan = keymap.binding_plan("<Super>v", layouts=[("us", ""), ("ir", "")], backend=persian_and_latin())
    assert [item.accelerator for item in plan] == ["<Super>v", "<Super>Arabic_ra"]
    assert plan[0].layout == ""  # the binding the user asked for
    assert plan[1].layout == "Persian"
    assert keymap.describe(plan[1:]) == "<Super>Arabic_ra (Persian)"


def test_a_unicode_keysym_is_offered_as_its_character_too():
    """The two keysym tables do not always agree: give both spellings a slot."""
    backend = FakeBackend({("us", ""): {V_KEY: SYM_V}, ("ir", ""): {V_KEY: SYM_UNICODE_REH}})
    plan = keymap.binding_plan("<Super>v", layouts=[("us", ""), ("ir", "")], backend=backend)
    assert [item.accelerator for item in plan] == ["<Super>v", "<Super>U0631", "<Super>ر"]


def test_a_russian_layout_is_covered(monkeypatch):
    monkeypatch.setattr(keymap, "layout_display_name", lambda layout, *_args: "Russian")
    backend = FakeBackend({("us", ""): {V_KEY: SYM_V}, ("ru", ""): {V_KEY: SYM_CYRILLIC_EM}})
    plan = keymap.binding_plan("<Super>v", layouts=[("us", ""), ("ru", "")], backend=backend)
    assert [item.accelerator for item in plan] == ["<Super>v", "<Super>Cyrillic_em"]


def test_the_custom_modifiers_are_kept():
    plan = keymap.binding_plan(
        "<Control><Alt>v", layouts=[("us", ""), ("ir", "")], backend=persian_and_latin()
    )
    assert [item.accelerator for item in plan] == ["<Control><Alt>v", "<Control><Alt>Arabic_ra"]


def test_two_latin_layouts_do_not_duplicate_the_binding():
    backend = FakeBackend({("us", ""): {V_KEY: SYM_V}, ("gb", ""): {V_KEY: SYM_V}})
    assert keymap.shortcut_bindings("<Super>v", layouts=[("us", ""), ("gb", "")], backend=backend) == [
        "<Super>v"
    ]


def test_only_the_primary_survives_without_a_backend(monkeypatch):
    """No libxkbcommon (or none that works) must leave the plain binding alone."""
    monkeypatch.setattr(keymap, "default_backend", lambda: None)
    layouts = [("us", ""), ("ir", "")]
    assert keymap.shortcut_bindings("<Super>v", layouts=layouts, backend=None) == ["<Super>v"]


def test_no_layouts_means_no_extra_bindings():
    assert keymap.shortcut_bindings("<Super>v", layouts=[], backend=persian_and_latin()) == ["<Super>v"]


def test_a_broken_backend_never_breaks_the_install():
    class Broken:
        def keysym(self, *_args):
            raise RuntimeError("boom")

        def name(self, *_args):
            return ""

        def character(self, *_args):
            return ""

    try:
        plan = keymap.binding_plan("<Super>v", layouts=[("us", "")], backend=Broken())
    except RuntimeError:  # pragma: no cover - the caller catches this instead
        plan = [keymap.LayoutBinding("<Super>v")]
    assert [item.accelerator for item in plan] == ["<Super>v"]


def test_the_plan_is_ordered_and_deduplicated():
    backend = FakeBackend(
        {
            ("us", ""): {V_KEY: SYM_V},
            ("ir", ""): {V_KEY: SYM_ARABIC_RA},
            ("ru", ""): {V_KEY: SYM_CYRILLIC_EM},
        }
    )
    plan = keymap.binding_plan("<Super>v", layouts=[("us", ""), ("ir", ""), ("ru", "")], backend=backend)
    accelerators = [item.accelerator for item in plan]
    assert accelerators[0] == "<Super>v"
    assert len(accelerators) == len(set(accelerators)) == 3


# ── the real library ────────────────────────────────────────────────────────
def test_libxkbcommon_is_used_when_it_is_installed():
    """Skipped where libxkbcommon/xkb-data are missing (checked in CI)."""
    backend = keymap.default_backend()
    if backend is None or clipboard.detect_session() == "unknown":
        pytest.skip("libxkbcommon or the xkb data is not available here")
    if backend.keysym("us", "", V_KEY) == 0:
        pytest.skip("the xkb data does not include the us layout")
    assert backend.name(backend.keysym("us", "", V_KEY)).lower() == "v"


# ── the ctypes layer, against a real shared library ─────────────────────────
@pytest.fixture(scope="module")
def stub_library(tmp_path_factory):
    """The libxkbcommon double, compiled from ``tests/xkb_stub.c``.

    A Python double cannot check ctypes: a wrong struct layout or argument type
    shows up as garbage or a segfault. The binding is therefore exercised
    against a real (if tiny) shared library that speaks the same ABI.
    """
    compiler = shutil.which("gcc") or shutil.which("cc")
    if compiler is None:
        pytest.skip("no C compiler to build the libxkbcommon double")
    source = Path(__file__).with_name("xkb_stub.c")
    library = tmp_path_factory.mktemp("xkb") / "libxkbcommon.so.0"
    built = subprocess.run(
        [compiler, "-shared", "-fPIC", "-o", str(library), str(source)],
        capture_output=True,
        check=False,
    )
    if built.returncode != 0:
        pytest.skip(f"cannot build the double: {built.stderr.decode()[:200]}")
    return library


def _counters(library) -> dict[str, int]:
    import ctypes

    handle = ctypes.CDLL(str(library))
    names = ("contexts", "keymaps", "states", "unrefs")
    return {name: ctypes.c_int.in_dll(handle, f"stub_{name}").value for name in names}


def test_the_ctypes_binding_reads_a_real_library(stub_library):
    backend = keymap.XkbCommon(str(stub_library))
    try:
        plan = keymap.binding_plan("<Super>v", layouts=[("us", ""), ("ir", "")], backend=backend)
        assert [item.accelerator for item in plan] == ["<Super>v", "<Super>Arabic_ra"]
        assert plan[1].layout  # labelled with the layout it came from
    finally:
        backend.close()


def test_a_unicode_keysym_is_both_named_and_spelled(stub_library):
    backend = keymap.XkbCommon(str(stub_library))
    try:
        plan = keymap.binding_plan("<Super>v", layouts=[("us", ""), ("ru", "")], backend=backend)
        assert [item.accelerator for item in plan] == ["<Super>v", "<Super>U044C", "<Super>м"]
    finally:
        backend.close()


def test_keymaps_are_built_once_per_layout(stub_library):
    before = _counters(stub_library)
    backend = keymap.XkbCommon(str(stub_library))
    try:
        for _ in range(3):
            backend.keysym("ir", "", V_KEY)
        assert _counters(stub_library)["keymaps"] - before["keymaps"] == 1
        assert backend.keysym("us", "", V_KEY) == SYM_V
    finally:
        backend.close()


def test_the_library_is_released_again(stub_library):
    before = _counters(stub_library)
    backend = keymap.XkbCommon(str(stub_library))
    assert backend.keysym("ir", "", V_KEY) == SYM_ARABIC_RA
    backend.close()
    after = _counters(stub_library)
    assert after["contexts"] - before["contexts"] == 1
    assert after["unrefs"] - before["unrefs"] >= 2  # the state and the context


def test_a_missing_library_is_a_clean_no(tmp_path):
    with pytest.raises(OSError):
        keymap.XkbCommon(str(tmp_path / "libnothing.so.0"))


def test_parse_sources_accepts_both_variant_styles():
    assert keymap.parse_sources("[('xkb', 'us+nodeadkeys')]") == [("us", "nodeadkeys")]
    assert keymap.parse_sources("[('xkb', 'us(dvorak)'), ('xkb', 'ir')]") == [
        ("us", "dvorak"),
        ("ir", ""),
    ]
