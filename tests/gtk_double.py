"""A tiny, dependency free stand-in for GTK/GDK/Gio/GLib/Adw/Pango.

The GUI cannot be imported in CI (no display, and PyGObject is not installable
from PyPI without the GTK development headers), but the *logic* inside
``ubuntu_clipboard/ui`` is worth testing. This module installs permissive
doubles for the GObject modules so the real UI code can be imported and driven
from tests.

It is intentionally small: only the behaviour the application relies on is
implemented (widget trees, CSS classes, signals, text buffers, drop downs); any
other ``set_*``/``get_*`` call is accepted and ignored. The GTK API *surface*
itself is verified separately by ``scripts/check_gtk_api.py`` against the
PyGObject stubs.
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MODULES = ("gi", "gi.repository")


class _SignalManager:
    """Object.connect()/emit() bookkeeping."""

    def __init__(self) -> None:
        object.__setattr__(self, "_handlers", {})

    def connect(self, name: str, callback, *data) -> int:
        """GTK passes the object first, then user data as extra arguments."""
        self._handlers.setdefault(name, []).append((callback, data))
        return len(self._handlers[name])

    def emit(self, name: str, *args) -> None:
        # ``notify::foo`` handlers receive the object and its GParamSpec.
        extra = (None,) if name.startswith("notify::") else ()
        for callback, data in list(self._handlers.get(name, [])):
            callback(self, *args, *extra, *data)

    def emit_raw(self, name: str, *args) -> None:
        """Emit without prepending the object (for ``notify::`` style signals)."""
        for callback, data in list(self._handlers.get(name, [])):
            callback(*args, *data)

    def disconnect(self, handler_id: int) -> None:  # pragma: no cover - rare
        for callbacks in self._handlers.values():
            if 0 < handler_id <= len(callbacks):
                callbacks.pop(handler_id - 1)
                return


class FakeObject(_SignalManager):
    """Base object: unknown ``set_*``/``get_*``/``add_*`` calls are accepted."""

    def __init__(self, **kwargs) -> None:
        super().__init__()
        for key, value in kwargs.items():
            setattr(self, f"_{key}", value)

    def __getattr__(self, name: str):
        if name.startswith(("set_", "add_", "remove_", "set", "queue_", "grab_", "unset", "load_")):
            return lambda *args, **kwargs: None
        if name.startswith("get_"):
            return lambda *args, **kwargs: None
        raise AttributeError(name)

    # convenience for tests
    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<{type(self).__name__} {getattr(self, '_css_classes', [])}>"


class FakeWidget(FakeObject):
    """A widget that keeps a real child list and CSS classes."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, "_children", [])
        object.__setattr__(self, "_classes", [])
        object.__setattr__(self, "_parent", None)
        object.__setattr__(self, "_controllers", [])
        object.__setattr__(self, "_text", getattr(self, "_text", ""))

    # -- tree ---------------------------------------------------------------
    def append(self, child) -> None:
        self._children.append(child)
        if isinstance(child, FakeWidget):
            object.__setattr__(child, "_parent", self)

    def add(self, child) -> None:
        """``Adw.PreferencesPage.add`` / ``Adw.PreferencesGroup.add``."""
        self.append(child)

    def remove(self, child) -> None:
        if child in self._children:
            self._children.remove(child)

    def set_child(self, child) -> None:
        self._children = [child]

    def get_child(self):
        return self._children[0] if self._children else None

    def get_first_child(self):
        return self._children[0] if self._children else None

    def get_next_sibling(self):
        parent = self._parent
        if parent is None:
            return None
        index = parent._children.index(self)
        return parent._children[index + 1] if index + 1 < len(parent._children) else None

    def add_controller(self, controller) -> None:
        self._controllers.append(controller)

    # -- css ----------------------------------------------------------------
    def add_css_class(self, name: str) -> None:
        if name not in self._classes:
            self._classes.append(name)

    def remove_css_class(self, name: str) -> None:
        if name in self._classes:
            self._classes.remove(name)

    def has_css_class(self, name: str) -> bool:
        return name in self._classes


class FakeFormats(FakeObject):
    """``Gdk.ContentFormats`` with an explicit set of available types."""

    def __init__(self, texture=None, files=(), mimes=()) -> None:
        super().__init__()
        object.__setattr__(self, "_texture", texture)
        object.__setattr__(self, "_files", tuple(files))
        object.__setattr__(self, "_mimes", set(mimes))

    def contain_gtype(self, gtype) -> bool:
        from gi.repository import Gdk

        if gtype is Gdk.Texture:
            return self._texture is not None
        if gtype is Gdk.FileList:
            return bool(self._files)
        return False

    def contain_mime_type(self, mime: str) -> bool:
        return mime in self._mimes


class FakeFile(FakeObject):
    def __init__(self, uri: str) -> None:
        super().__init__()
        object.__setattr__(self, "_uri", uri)

    def get_uri(self) -> str:
        return self._uri


class FakeFileListValue(FakeObject):
    def __init__(self, files) -> None:
        super().__init__()
        object.__setattr__(self, "_files", [FakeFile(uri) for uri in files])

    def get_files(self):
        return self._files

    def get_value(self):
        return self


class FakeClipboard(FakeWidget):
    """``Gdk.Clipboard`` that answers reads from its configured payload."""

    def __init__(self, text=None, texture=None, files=(), mimes=()) -> None:
        super().__init__()
        object.__setattr__(self, "_text", text)
        object.__setattr__(self, "_texture", texture)
        object.__setattr__(self, "_files", tuple(files))
        object.__setattr__(self, "_mimes", set(mimes))
        object.__setattr__(self, "content", None)

    def get_formats(self):
        return FakeFormats(texture=self._texture, files=self._files, mimes=self._mimes)

    def set_content(self, provider) -> None:
        object.__setattr__(self, "content", provider)

    def read_text_async(self, cancellable, callback) -> None:
        callback(self, None)

    def read_text_finish(self, result):
        return self._text

    def read_texture_async(self, cancellable, callback) -> None:
        callback(self, None)

    def read_texture_finish(self, result):
        return self._texture

    def read_value_async(self, gtype, priority, cancellable, callback) -> None:
        callback(self, None)

    def read_value_finish(self, result):
        return FakeFileListValue(self._files)


class FakePicture(FakeWidget):
    def __init__(self, paintable=None, **kwargs) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, "paintable", paintable)

    @classmethod
    def new_for_paintable(cls, paintable):
        return cls(paintable)


class FakeWindow(FakeWidget):
    def __init__(self, application=None, **kwargs) -> None:
        super().__init__(application=application, **kwargs)
        object.__setattr__(self, "_visible", False)
        object.__setattr__(self, "_active", False)
        object.__setattr__(self, "_surface", FakeObject())

    def present(self) -> None:
        object.__setattr__(self, "_visible", True)
        self.emit("map")

    def hide(self) -> None:
        object.__setattr__(self, "_visible", False)
        self.emit("unmap")

    def close(self) -> None:
        self.emit("close-request")

    def get_visible(self) -> bool:
        return self._visible

    def is_active(self) -> bool:
        return self._active

    def get_surface(self):
        return self._surface

    def set_active(self, active: bool) -> None:
        """Test helper: pretend the window manager changed the focus."""
        object.__setattr__(self, "_active", active)
        self.emit("notify::is-active")


class FakeLabel(FakeWidget):
    def __init__(self, label: str = "", **kwargs) -> None:
        super().__init__(label=label, **kwargs)
        object.__setattr__(self, "_label", label)

    def set_label(self, text: str) -> None:
        object.__setattr__(self, "_label", text)

    def get_label(self) -> str:
        return self._label


class FakeButton(FakeWidget):
    def __init__(self, label: str | None = None, **kwargs) -> None:
        super().__init__(label=label, **kwargs)
        object.__setattr__(self, "_label", label)

    def get_label(self):
        return self._label

    def click(self) -> None:
        self.emit("clicked")


class FakeEntry(FakeWidget):
    def __init__(self, text: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, "_text", text)
        object.__setattr__(self, "_text", text)

    def get_text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        object.__setattr__(self, "_text", text)
        self.emit("search-changed")


class FakeSearchEntry(FakeEntry):
    def has_focus(self) -> bool:
        return True


class FakeAdjustment(FakeObject):
    def __init__(self, value: float = 0.0, **kwargs) -> None:
        super().__init__(value=value, **kwargs)
        object.__setattr__(self, "_value", value)

    def get_value(self) -> float:
        return self._value

    def set_value(self, value: float) -> None:
        object.__setattr__(self, "_value", value)

    def get_page_size(self) -> float:
        return 200.0


class FakeScrolledWindow(FakeWidget):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, "_adjustment", FakeAdjustment())

    def get_vadjustment(self):
        return self._adjustment


class FakeSpinButton(FakeWidget):
    def __init__(self, adjustment=None, value: float | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, "_value", value if value is not None else 0.0)

    def get_value(self) -> float:
        return self._value

    def set_value(self, value: float) -> None:
        object.__setattr__(self, "_value", value)
        self.emit("value-changed")


class FakeSwitch(FakeWidget):
    def __init__(self, active: bool = False, **kwargs) -> None:
        super().__init__(active=active, **kwargs)
        object.__setattr__(self, "_active", active)

    def get_active(self) -> bool:
        return self._active

    def set_active(self, active: bool) -> None:
        object.__setattr__(self, "_active", active)
        self.emit("notify::active")


class FakeDropDown(FakeWidget):
    def __init__(self, options: list[str] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, "_options", list(options or []))
        object.__setattr__(self, "_selected", 0)

    @classmethod
    def new_from_strings(cls, options):
        return cls(list(options))

    def get_selected(self) -> int:
        return self._selected

    def set_selected(self, index: int) -> None:
        object.__setattr__(self, "_selected", index)
        self.emit("notify::selected")


class FakeTexture(FakeObject):
    def __init__(self, payload: bytes = b"", width: int = 4, height: int = 4) -> None:
        super().__init__()
        object.__setattr__(self, "_payload", payload)
        object.__setattr__(self, "_width", width)
        object.__setattr__(self, "_height", height)

    def get_width(self) -> int:
        return self._width

    def get_height(self) -> int:
        return self._height

    def save_to_png_bytes(self):
        return FakeBytes(self._payload)

    @classmethod
    def new_from_bytes(cls, data):
        payload = data.get_data() if hasattr(data, "get_data") else bytes(data)
        return cls(payload)


class FakeBytes(FakeObject):
    def __init__(self, payload: bytes = b"") -> None:
        super().__init__()
        object.__setattr__(self, "_payload", payload)

    @classmethod
    def new(cls, payload) -> FakeBytes:
        if isinstance(payload, FakeBytes):
            return payload
        return cls(bytes(payload))

    def get_data(self) -> bytes:
        return self._payload


class FakeAdjustmentHolder(FakeObject):  # pragma: no cover - convenience
    pass


class FakeEventControllerKey(FakeWidget):
    def set_propagation_phase(self, phase) -> None:
        object.__setattr__(self, "_phase", phase)


class FakeGestureClick(FakeWidget):
    def set_button(self, button: int) -> None:
        object.__setattr__(self, "_button", button)


class FakeMainLoop(FakeObject):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__()
        object.__setattr__(self, "_running", False)

    def run(self) -> None:
        object.__setattr__(self, "_running", True)

    def quit(self) -> None:
        object.__setattr__(self, "_running", False)


class FakeMessageDialog(FakeObject):
    """Adw.MessageDialog / Gtk.MessageDialog that auto-answers."""

    #: Set by tests to pick the response; ``None`` means "cancel".
    auto_response: str | None = "accept"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, "_responses", [])

    def add_response(self, name: str, label: str = "") -> None:
        self._responses.append(name)

    def add_button(self, label: str, response) -> None:
        self._responses.append(str(response))

    def present(self) -> None:
        response = type(self).auto_response
        if response is None:
            response = self._responses[0] if self._responses else "cancel"
        self.emit("response", response)


class FakeAlertDialog(FakeObject):
    """Gtk.AlertDialog with an asynchronous ``choose``."""

    auto_accept = True

    def __init__(self, **kwargs) -> None:
        super().__init__()
        object.__setattr__(self, "_buttons", [])

    def set_buttons(self, buttons) -> None:
        object.__setattr__(self, "_buttons", list(buttons))

    def set_cancel_button(self, index: int) -> None:
        object.__setattr__(self, "_cancel", index)

    def set_default_button(self, index: int) -> None:
        object.__setattr__(self, "_default", index)

    def choose(self, parent, cancellable, callback) -> None:
        callback(self, FakeObject())

    def choose_finish(self, result) -> int:
        return 1 if type(self).auto_accept else 0


class FakeRGBA(FakeObject):
    def __init__(self, **kwargs) -> None:
        super().__init__()
        object.__setattr__(self, "red", 1.0)
        object.__setattr__(self, "green", 0.0)
        object.__setattr__(self, "blue", 0.0)
        object.__setattr__(self, "alpha", 1.0)

    def parse(self, value: str) -> bool:
        return value.startswith("#")


class FakeSettings(FakeObject):
    def get_property(self, name: str):
        return "Adwaita-dark"

    def set_property(self, name: str, value) -> None:
        object.__setattr__(self, name, value)


class FakeStyleManager(FakeObject):
    def get_dark(self) -> bool:
        return True

    def set_color_scheme(self, scheme) -> None:
        object.__setattr__(self, "scheme", scheme)


class FakeEventKeyValues:
    """``Gtk.EventControllerKey`` signal payload helpers."""

    DEFAULT = 0


# ── module construction ────────────────────────────────────────────────────
class _ModifierType:
    CONTROL_MASK = 1 << 2
    SHIFT_MASK = 1 << 0


class _Orientation:
    VERTICAL = 0
    HORIZONTAL = 1


class _Align:
    START = 0
    CENTER = 1
    END = 2


class _EllipsizeMode:
    END = 3


class _WrapMode:
    WORD_CHAR = 2


class _PolicyType:
    NEVER = 0
    AUTOMATIC = 1


class _ContentFit:
    CONTAIN = 0


class _PropagationPhase:
    CAPTURE = 1
    BUBBLE = 2


class _TextDirection:
    LTR = 0
    RTL = 1


class _ResponseType:
    OK = -5
    CANCEL = -6


class _ButtonsType:
    NONE = 0


def _build_gtk() -> types.ModuleType:
    module = types.ModuleType("gi.repository.Gtk")
    widgets = {
        "ApplicationWindow": FakeWindow,
        "Window": FakeWindow,
        "WindowHandle": FakeWidget,
        "Box": FakeWidget,
        "Label": FakeLabel,
        "Button": FakeButton,
        "SearchEntry": FakeSearchEntry,
        "ScrolledWindow": FakeScrolledWindow,
        "Picture": FakePicture,
        "DrawingArea": FakeWidget,
        "SpinButton": FakeSpinButton,
        "Switch": FakeSwitch,
        "DropDown": FakeDropDown,
        "Adjustment": FakeAdjustment,
        "EventControllerKey": FakeEventControllerKey,
        "GestureClick": FakeGestureClick,
        "MessageDialog": FakeMessageDialog,
        "AlertDialog": FakeAlertDialog,
        "CssProvider": FakeObject,
        "StyleContext": FakeObject,
        "Settings": FakeSettings,
        "Widget": FakeObject,
        "Texture": FakeTexture,
    }
    for name, cls in widgets.items():
        setattr(module, name, cls)
    for name, value in {
        "Orientation": _Orientation,
        "Align": _Align,
        "Pango": types.SimpleNamespace(EllipsizeMode=_EllipsizeMode),
        "WrapMode": _WrapMode,
        "PolicyType": _PolicyType,
        "ContentFit": _ContentFit,
        "PropagationPhase": _PropagationPhase,
        "TextDirection": _TextDirection,
        "ResponseType": _ResponseType,
        "ButtonsType": _ButtonsType,
        "STYLE_PROVIDER_PRIORITY_APPLICATION": 600,
    }.items():
        setattr(module, name, value)
    module.Widget.set_default_direction = staticmethod(lambda *args: None)  # type: ignore[attr-defined]
    module.StyleContext.add_provider_for_display = staticmethod(lambda *args: None)  # type: ignore[attr-defined]
    return module


def _build_gdk() -> types.ModuleType:
    module = types.ModuleType("gi.repository.Gdk")
    module.ModifierType = _ModifierType
    module.RGBA = FakeRGBA
    module.Texture = FakeTexture
    module.Toplevel = FakeWidget
    module.ToplevelState = types.SimpleNamespace(FOCUSED=64)

    class _ContentProvider:
        @staticmethod
        def new_for_value(value):
            return FakeObject()

        @staticmethod
        def new_for_bytes(mime, payload):
            return FakeObject()

    class _FileList:
        @staticmethod
        def new_from_list(files):
            return FakeObject()

    class _Display:
        clipboard = None

        @classmethod
        def get_default(cls):
            return None if cls.clipboard is None else cls

        @classmethod
        def get_clipboard(cls):
            return cls.clipboard

    module.ContentProvider = _ContentProvider
    module.FileList = _FileList
    module.Display = _Display
    module.Clipboard = FakeClipboard
    module.ContentFormats = FakeFormats
    module.Keyval = types.SimpleNamespace(Escape=1, Up=2, Down=3, Return=4, Delete=5)
    key_names = {
        0xFF1B: "Escape",
        0xFF52: "Up",
        0xFF54: "Down",
        0xFF0D: "Return",
        0xFF09: "Tab",
        0xFF51: "Left",
        0xFF53: "Right",
        0xFFFF: "Delete",
    }

    def keyval_name(keyval: int) -> str:
        """Named keys, plus the printable ASCII range like the real GDK."""
        if keyval in key_names:
            return key_names[keyval]
        if 0x20 <= keyval < 0x7F:
            return chr(keyval)
        return ""

    module.keyval_name = keyval_name
    return module


def _build_glib() -> types.ModuleType:
    module = types.ModuleType("gi.repository.GLib")
    module.Bytes = FakeBytes
    # ``GApplication`` has no ``set_application_name``; the GLib global is real.
    module.set_application_name = lambda name: object.__setattr__(module, "application_name", name)
    module.Variant = lambda *args: FakeObject()
    module.VariantType = lambda *args: FakeObject()
    module.Error = Exception
    module.MainLoop = FakeMainLoop
    module.PRIORITY_DEFAULT = 0
    module.idle_add = lambda callback, *args: callback(*args)
    module.timeout_add = lambda delay, callback, *args: callback(*args)
    module.source_remove = lambda source_id: None
    module.unix_signal_add = lambda *args: 1
    return module


class FakeGioFile(FakeObject):
    def __init__(self, path: str = "") -> None:
        super().__init__()
        object.__setattr__(self, "_path", str(path))

    @classmethod
    def new_for_path(cls, path) -> FakeGioFile:
        return cls(path)

    @classmethod
    def new_for_uri(cls, uri: str) -> FakeGioFile:
        return cls(uri.removeprefix("file://"))

    def get_path(self) -> str:
        return self._path

    def get_uri(self) -> str:
        return f"file://{self._path}"

    def monitor_directory(self, flags, cancellable=None):
        return FakeMonitor()

    monitor_file = monitor_directory


class FakeNotification(FakeObject):
    def __init__(self, title: str = "") -> None:
        super().__init__()
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "body", "")
        object.__setattr__(self, "icon", None)

    @classmethod
    def new(cls, title: str) -> FakeNotification:
        return cls(title)

    def set_body(self, body: str) -> None:
        object.__setattr__(self, "body", body)

    def set_icon(self, icon) -> None:
        object.__setattr__(self, "icon", icon)


class FakeThemedIcon(FakeObject):
    @classmethod
    def new(cls, name: str) -> FakeThemedIcon:
        return cls()


class FakeMonitor(FakeObject):
    def __init__(self) -> None:
        super().__init__()
        object.__setattr__(self, "cancelled", False)

    def cancel(self) -> None:
        object.__setattr__(self, "cancelled", True)


def _build_gio() -> types.ModuleType:
    module = types.ModuleType("gi.repository.Gio")

    class _Application:
        def __init__(self, **kwargs) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

        def connect(self, *_args):
            return 1

    class _SimpleAction(FakeObject):
        def __init__(self, name: str, parameter_type=None) -> None:
            super().__init__()
            object.__setattr__(self, "name", name)
            object.__setattr__(self, "parameter_type", parameter_type)

        @classmethod
        def new(cls, name, parameter_type=None):
            return cls(name, parameter_type)

    module.Application = _Application
    module.ApplicationFlags = types.SimpleNamespace(HANDLES_COMMAND_LINE=1)
    module.SimpleAction = _SimpleAction
    module.Notification = FakeNotification
    module.ThemedIcon = FakeThemedIcon
    module.File = FakeGioFile
    module.AppInfo = FakeObject
    module.BusType = types.SimpleNamespace(SESSION=2)
    module.DBusCallFlags = types.SimpleNamespace(NONE=0)
    module.FileMonitorFlags = types.SimpleNamespace(WATCH_MOVES=1)
    module.bus_get_sync = lambda *args: FakeObject()
    return module


def _build_adw() -> types.ModuleType:
    module = types.ModuleType("gi.repository.Adw")

    class _PreferencesWindow(FakeWindow):
        pass

    class _PreferencesPage(FakeWidget):
        pass

    class _PreferencesGroup(FakeWidget):
        pass

    class _ActionRow(FakeWidget):
        def __init__(self, title="", **kwargs) -> None:
            super().__init__(title=title, **kwargs)
            object.__setattr__(self, "title", title)
            object.__setattr__(self, "suffixes", [])

        def add_suffix(self, widget) -> None:
            self.suffixes.append(widget)

        def set_subtitle(self, subtitle: str) -> None:
            object.__setattr__(self, "subtitle", subtitle)

        def set_activatable_widget(self, widget) -> None:
            object.__setattr__(self, "activatable", widget)

    module.PreferencesWindow = _PreferencesWindow
    module.PreferencesPage = _PreferencesPage
    module.PreferencesGroup = _PreferencesGroup
    module.ActionRow = _ActionRow
    module.MessageDialog = FakeMessageDialog
    module.Application = _ApplicationBase
    module.ResponseAppearance = types.SimpleNamespace(SUGGESTED=1)
    module.ColorScheme = types.SimpleNamespace(DEFAULT=0, FORCE_DARK=1, FORCE_LIGHT=2)
    module.StyleManager = types.SimpleNamespace(get_default=lambda: FakeStyleManager())
    return module


class _CommandLine:
    """Stand-in for ``Gio.ApplicationCommandLine``."""

    def __init__(self, arguments) -> None:
        object.__setattr__(self, "_arguments", list(arguments))

    def get_arguments(self) -> list[str]:
        return list(self._arguments)


class _ApplicationBase:
    def __init__(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)
        self._actions = {}

    def add_action(self, action) -> None:
        self._actions[action.name] = action

    def connect(self, *_args):
        return 1

    def set_accels_for_action(self, *_args) -> None:
        pass

    def send_notification(self, *args) -> None:
        pass

    def hold(self) -> None:
        pass

    def do_startup(self) -> None:  # GObject vfunc default implementation
        pass

    def do_shutdown(self) -> None:  # GObject vfunc default implementation
        pass

    def quit(self) -> None:
        pass

    def run(self, argv=None) -> int:
        """Emulate ``GApplication.run()``: startup, one command, shutdown."""
        arguments = list(argv or ["ubuntu-clipboard"])
        self.do_startup()
        code = 0
        try:
            handler = getattr(self, "do_command_line", None) if len(arguments) > 1 else None
            if handler is not None:
                code = handler(_CommandLine(arguments)) or 0
            else:
                activate = getattr(self, "do_activate", None)
                code = (activate() or 0) if activate is not None else 0
        finally:
            self.do_shutdown()
        return code


#: Modules that bind GTK objects at import time and must be re-imported with the
#: double. Pure modules (config, models, storage, i18n) are deliberately kept so
#: that enum identities stay stable across the import.
GTK_MODULES = ("ubuntu_clipboard.app", "ubuntu_clipboard.monitor")


def _purge_project_modules() -> None:
    for name in list(sys.modules):
        if name in GTK_MODULES or name.startswith("ubuntu_clipboard.ui"):
            del sys.modules[name]


def build_repository() -> types.ModuleType:
    """Build the ``gi.repository`` module object holding all the doubles."""
    repository = types.ModuleType("gi.repository")
    repository.Gtk = _build_gtk()
    repository.Gdk = _build_gdk()
    repository.GLib = _build_glib()
    repository.Gio = _build_gio()
    repository.Adw = _build_adw()
    repository.Pango = types.SimpleNamespace(EllipsizeMode=_EllipsizeMode, WrapMode=_WrapMode)
    return repository


#: Written by :func:`install_double_package`; it lets a *fresh* interpreter import
#: the doubles, so the real entry points can be run as subprocesses in tests.
_PACKAGE_SOURCE = '''"""Auto generated stand-in for PyGObject (see tests/gtk_double.py)."""

import sys

_REPOSITORY_ROOT = {repository_root!r}
# Append (never prepend): an installed copy of the package must win over the
# source checkout this shim lives in.
if _REPOSITORY_ROOT not in sys.path:
    sys.path.append(_REPOSITORY_ROOT)

from tests import gtk_double  # noqa: E402

repository = gtk_double.build_repository()
sys.modules["gi.repository"] = repository


def require_version(*_args, **_kwargs) -> None:
    """Accept every ``gi.require_version`` call, as a real GTK install would."""


gtk_double.seed_clipboard(repository, text={clipboard_text!r})
'''


def seed_clipboard(repository: types.ModuleType, *, text=None, texture=None, files=()) -> object:
    """Give a repository a default display whose clipboard holds this payload."""
    clipboard = FakeClipboard(text=text, texture=texture, files=files)
    repository.Gdk.Display.clipboard = clipboard
    return clipboard


def install_double_package(directory: str | os.PathLike[str], clipboard_text: str | None = None) -> Path:
    """Write a ``gi`` package into ``directory`` and return it, for ``PYTHONPATH``.

    The package is self contained: it points back at this file, so a new
    interpreter can run the whole application with the doubles instead of a
    real PyGObject installation. Pass ``clipboard_text`` to start that
    interpreter with something already on the (fake) clipboard.
    """
    package = Path(directory) / "gi"
    package.mkdir(parents=True, exist_ok=True)
    root = str(Path(__file__).resolve().parent.parent)
    source = _PACKAGE_SOURCE.format(repository_root=root, clipboard_text=clipboard_text)
    (package / "__init__.py").write_text(source, encoding="utf-8")
    return package.parent


@contextmanager
def gtk_double() -> Iterator[object]:
    """Install the doubles for the duration of the block."""
    gi_module = types.ModuleType("gi")
    gi_module.require_version = lambda *args: None
    repository = build_repository()
    gi_module.repository = repository

    saved = {name: sys.modules.get(name) for name in MODULES}
    sys.modules["gi"] = gi_module
    sys.modules["gi.repository"] = repository
    _purge_project_modules()
    modules = types.SimpleNamespace(
        gtk=repository.Gtk,
        gdk=repository.Gdk,
        glib=repository.GLib,
        gio=repository.Gio,
        adw=repository.Adw,
    )
    try:
        yield modules
    finally:
        _purge_project_modules()
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        importlib.invalidate_caches()
