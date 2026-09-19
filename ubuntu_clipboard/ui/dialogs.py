"""Modal dialogs.

GTK 4 has no ``dialog.run()`` anymore, so a small nested main loop is used to
keep the calling code synchronous. ``Adw.MessageDialog`` is preferred (all
supported Ubuntu releases ship libadwaita), ``Gtk.AlertDialog`` covers GTK
4.10+ without libadwaita and ``Gtk.MessageDialog`` the rest.
"""

from __future__ import annotations

import contextlib
import logging

from ..i18n import t
from . import HAS_ADW, Adw, GLib, Gtk

log = logging.getLogger(__name__)


def confirm(
    parent, title: str, body: str, ok_label: str | None = None, cancel_label: str | None = None
) -> bool:
    """Show a modal confirmation, returning ``True`` when accepted."""
    ok_label = ok_label or t("button.ok")
    cancel_label = cancel_label or t("button.cancel")
    loop = GLib.MainLoop()
    outcome = {"accepted": False}

    try:
        if HAS_ADW:
            dialog = Adw.MessageDialog(transient_for=parent, modal=True, heading=title, body=body)
            dialog.add_response("cancel", cancel_label)
            dialog.add_response("accept", ok_label)
            appearance = getattr(Adw, "ResponseAppearance", None)
            if appearance is not None:
                dialog.set_response_appearance("accept", appearance.SUGGESTED)
            dialog.set_default_response("accept")
            dialog.set_close_response("cancel")

            def on_response(_dialog, response: str) -> None:
                outcome["accepted"] = response == "accept"
                loop.quit()

            dialog.connect("response", on_response)
            dialog.present()
        elif hasattr(Gtk, "AlertDialog"):  # GTK >= 4.10
            dialog = Gtk.AlertDialog()
            dialog.set_message(title)
            dialog.set_detail(body)
            dialog.set_modal(True)
            dialog.set_buttons([cancel_label, ok_label])
            dialog.set_cancel_button(0)
            dialog.set_default_button(1)

            def on_choose(alert: Gtk.AlertDialog, result) -> None:
                try:
                    outcome["accepted"] = alert.choose_finish(result) == 1
                except GLib.Error:  # pragma: no cover - dismissed
                    outcome["accepted"] = False
                loop.quit()

            dialog.choose(parent, None, on_choose)
        else:  # pragma: no cover - GTK 4.0 .. 4.8 without libadwaita
            dialog = Gtk.MessageDialog(
                transient_for=parent,
                modal=True,
                text=title,
                secondary_text=body,
                buttons=Gtk.ButtonsType.NONE,
            )
            dialog.add_button(cancel_label, Gtk.ResponseType.CANCEL)
            dialog.add_button(ok_label, Gtk.ResponseType.OK)
            dialog.set_default_response(Gtk.ResponseType.OK)

            def on_response_legacy(_dialog, response: int) -> None:
                outcome["accepted"] = response == Gtk.ResponseType.OK
                loop.quit()

            dialog.connect("response", on_response_legacy)
            dialog.present()
    except Exception:  # pragma: no cover - never block the caller
        log.exception("cannot show confirmation dialog")
        return False

    loop.run()
    with contextlib.suppress(AttributeError, TypeError):  # pragma: no cover
        dialog.destroy()
    return outcome["accepted"]
