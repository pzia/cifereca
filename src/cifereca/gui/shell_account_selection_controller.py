"""GTK-independent shell selection handlers (#1130).

MainWindow owns live widgets, service state and generation counters.
"""
from __future__ import annotations

import logging

_LOG = logging.getLogger("cifereca")


def identity_changed(window, dropdown, _pspec) -> None:
    """Select only a known identity, never from a stale GTK notification."""
    if getattr(window, "_closed", False) or window._identity_changing:
        return
    idx = dropdown.get_selected()
    if idx < 0 or idx >= len(window.service.identities):
        return
    identity = window.service.identities[idx]
    window._activate_identity(identity.identity_id)


def invalidate_secondary_account_pages(window) -> None:
    """Remove private account rows and invalidate pending async completions."""
    window._account_context_generation += 1
    window.service.last_history = []
    window.service.last_bookings = []
    window.history_objects = {}
    window.booking_objects = {}
    window._history_all_rows = []
    window._history_rows = []
    window._history_loaded_count = 0
    window._history_append_scheduled = False
    if hasattr(window, "history_page"):
        window.history_page.show_rows([])
        window.history_page.detail.empty(
            "Historique non chargé",
            "Ouvrez l’onglet Historique pour charger ce compte.",
        )
    if hasattr(window, "booking_page"):
        window.booking_page.show_rows([])
        window.booking_page.detail.empty(
            "Réservations non chargées",
            "Ouvrez l’onglet Réservations pour charger ce compte.",
        )


def account_changed(window, dropdown, _pspec) -> None:
    """Switch private account without leaking stale secondary-page content."""
    if getattr(window, "_closed", False) or window._account_changing:
        return
    idx = dropdown.get_selected()
    accounts = getattr(window, "_visible_accounts", [])
    if idx < 0 or idx >= len(accounts):
        return
    account = accounts[idx]
    current = window.service.current_account
    if current is not None and (
        current.unique_id == account.unique_id
        and current.provider_id == account.provider_id
        and current.credential_id == account.credential_id
    ):
        _LOG.debug(
            "Account selection notification ignored: already current account=%s",
            account.unique_id,
        )
        return
    window.service.select_account(
        account.unique_id, account.provider_id, account.credential_id,
    )
    invalidate_secondary_account_pages(window)
    window.toast(f"Compte : {account.display_name or account.provider_label}")
