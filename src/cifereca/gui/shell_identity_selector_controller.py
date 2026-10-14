"""Identity and delegated-account selector projection for the GTK shell (#1130).

The window owns GTK widgets and identity/session selection. The pre-existing
shell_model.delegated_accounts remains the sole account visibility policy.
GTK is explicitly injected; this module is importable without PyGObject.
"""
from __future__ import annotations


def replace_dropdown_labels(dropdown, labels, *, Gtk) -> None:
    """Reuse the StringList model when possible, avoiding needless notify churn."""
    model = dropdown.get_model()
    if isinstance(model, Gtk.StringList):
        previous = [model.get_string(i) for i in range(model.get_n_items())]
        if previous != labels:
            model.splice(0, model.get_n_items(), labels)
    else:
        dropdown.set_model(Gtk.StringList.new(labels))


def populate_account_dropdown(window, identity_id: str | None, *, Gtk, delegated_accounts) -> None:
    """Present only identity-scoped private accounts, never public catalogues."""
    if getattr(window, "_closed", False):
        return
    window._account_changing = True
    try:
        public = {
            account.provider_id for account in window.service.accounts
            if getattr(
                getattr(
                    window.service._provider_plugin_status(account.provider_id),
                    "descriptor", None,
                ),
                "instantiation", "multiple",
            ) == "singleton_public"
        }
        window._visible_accounts = delegated_accounts(
            window.service.accounts, identity_id, public,
        )
        window.account_dropdown.set_visible(bool(window._visible_accounts))
        labels = [
            f"{account.provider_label} · {account.display_name or ('Compte principal' if account.is_primary else 'Compte lié')}"
            for account in window._visible_accounts
        ]
        window._replace_dropdown_labels(window.account_dropdown, labels)
        current = window.service.current_account
        index = next(
            (i for i, account in enumerate(window._visible_accounts) if account is current),
            Gtk.INVALID_LIST_POSITION,
        )
        window.account_dropdown.set_selected(
            index if window._visible_accounts else Gtk.INVALID_LIST_POSITION
        )
    finally:
        window._account_changing = False


def populate_accounts(window, *, Gtk) -> None:
    """Project identities first, then the active identity's delegated accounts."""
    if getattr(window, "_closed", False):
        return
    window._identity_changing = True
    try:
        identities = list(window.service.identities)
        window._replace_dropdown_labels(
            window.identity_dropdown, [identity.label for identity in identities],
        )
        selected_identity = (
            window.service.current_identity.identity_id
            if window.service.current_identity else None
        )
        selected_index = next(
            (i for i, identity in enumerate(identities)
             if identity.identity_id == selected_identity),
            0,
        )
        window.identity_dropdown.set_selected(
            selected_index if identities else Gtk.INVALID_LIST_POSITION
        )
    finally:
        window._identity_changing = False

    window._populate_account_dropdown(selected_identity)
    if hasattr(window, "_refresh_statistics"):
        window._refresh_statistics()
