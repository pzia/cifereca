"""Identity-scoped synchronization issues dialog for the CoreUI shell (#1051).

The redaction/wording policy remains in sync_issues.sync_issues_text. The
MainWindow continues to own GTK widgets and navigation; Adw is injected
explicitly so this controller is importable without GI/PyGObject.
"""
from __future__ import annotations

from .sync_issues import sync_issues_text


def show_sync_issues(window, card, *, Adw) -> None:
    """Present one read-only report; navigate only on explicit, live consent."""
    if getattr(window, "_closed", False):
        return

    dialog = Adw.MessageDialog.new(
        window,
        "Synchronisation · " + str(card.get("label") or ""),
        sync_issues_text(card.get("sync") or {}),
    )
    dialog.add_response("settings", "Paramètres des identités")
    dialog.add_response("close", "Fermer")

    identity_generation = getattr(window, "_identity_context_generation", None)
    account_generation = getattr(window, "_account_context_generation", None)

    def response(_dialog, selected):
        if selected != "settings" or getattr(window, "_closed", False):
            return
        # A dialog from a previous user's identity/account is stale; it must
        # not navigate or touch the newly active account's settings widgets.
        if (
            identity_generation != getattr(window, "_identity_context_generation", None)
            or account_generation != getattr(window, "_account_context_generation", None)
        ):
            return
        window.navigate("settings")
        window._settings_tabs.set_visible_child_name("accounts")

    dialog.connect("response", response)
    dialog.present()
