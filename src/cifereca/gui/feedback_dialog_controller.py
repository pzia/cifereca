"""Single-window feedback dialogs without a GTK import (#1132).

The owner MainWindow retains service diagnostics, toast timers and lifetime.
Adw is injected by its compatibility facade; this module creates no timers
or secondary observer subscriptions.
"""
from __future__ import annotations


def show_error(window, message: str, title: str = "Une erreur est survenue", *, Adw) -> None:
    """Keep the functional error mapping and toast-before-dialog order."""
    if getattr(window, "_closed", False):
        return
    display_message = window._functional_error_message(message)
    window.toast(display_message, 6)
    dialog = Adw.MessageDialog.new(window, title, display_message)
    dialog.add_response("ok", "Fermer")
    dialog.set_default_response("ok")
    dialog.set_close_response("ok")
    dialog.present()


def show_attention(window, message: str, *, code: str = "attention_required", Adw) -> None:
    """Render a remediable condition distinctly from a functional failure."""
    if getattr(window, "_closed", False):
        return
    dialog = Adw.MessageDialog.new(window, "Action requise", message)
    dialog.add_response("close", "Fermer")
    dialog.set_default_response("close")
    dialog.set_close_response("close")
    # Untrusted provider text must never be interpreted as Pango/HTML markup.
    dialog.set_body_use_markup(False)
    dialog.present()
    window.diagnostics.add(
        "UX", "attention required", "warning",
        detail={"code": code, "message": message}, level="detailed",
    )