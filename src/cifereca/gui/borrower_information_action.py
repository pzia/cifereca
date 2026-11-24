"""Explicit advisory display, separate from the borrow action."""
from __future__ import annotations

from datetime import datetime
from inspect import getattr_static

from ..models import Account, SearchResult
from ..task_queue import TaskPriority
from .manual_borrow_controller import _capture_borrow_ui_scope


def advisory_text(values):
    """Closed, plain-text presentation, no identifiers or permission inference."""
    if type(values) is not dict or type(values.get("observed_at")) is not int:
        return "Informations non vérifiées."
    try:
        date = datetime.fromtimestamp(values["observed_at"]).strftime("%d/%m/%Y %H:%M")
    except (ValueError, OverflowError, OSError):
        return "Informations non vérifiées."
    offer = ("Indisponibilité observée" if values.get("item_offer") == "unavailable"
             else "Disponibilité inconnue")
    return (f"Observation du {date} · {offer}. "
            "Pass : inconnu · Quota : inconnu · Exemplaires : inconnus · "
            "Permission : inconnue. Information sans autorisation d’emprunt.")


def _candidate(window, item):
    """Local visibility only; no admission, network or account remapping."""
    try:
        if (type(item) is not SearchResult or item.medium != "digital"
                or type(item.metadata) is not dict
                or item.metadata.get("catalogue_access") == "shared_read_only"
                or item.metadata.get("search_provenance") == "known_catalog_stale"):
            return None
        service = window.service
        identity = service.current_identity.identity_id
        session = service._sessions.get(service._session_key(
            item.source_provider, item.source_credential))
        if not all(callable(getattr_static(session, name, None)) for name in (
                "borrower_information_for", "borrower_information_display")):
            return None
        accounts = [a for a in service.accounts if type(a) is Account
                    and a.is_primary is True
                    and (a.identity_id, a.provider_id, a.credential_id)
                    == (identity, item.source_provider, item.source_credential)
                    and item.source_account_id in (None, a.unique_id)]
        if len(accounts) != 1:
            return None
        scope = _capture_borrow_ui_scope(window, item, accounts[0], session,
                                         provider_id=item.source_provider)
        return (accounts[0], scope[2]) if scope and scope[2]() else None
    except Exception:
        return None


def add_borrower_information_action(panel, item):
    """Render a separate button; only its click schedules a provider read."""
    # Independent rollout: no action until the Core bridge is installed.
    try:
        from ..core.borrower_information import prepare_borrower_information
    except ImportError:
        return False
    window = panel.window
    candidate = _candidate(window, item)
    if candidate is None:
        return False
    from gi.repository import Adw
    account, current = candidate
    service = window.service
    group = Adw.PreferencesGroup()
    group.set_title("Informations du prêteur")
    row = Adw.ActionRow()
    row.set_use_markup(False)
    row.set_title("Observation facultative")
    row.set_subtitle("Informations non vérifiées. Ne conditionne pas l’emprunt.")
    button = window.action_button("Consulter", "view-refresh-symbolic")
    row.add_suffix(button)
    group.add(row)

    source_account = item.source_account_id
    pending = False

    def visible():
        return (current() and row.get_root() is not None
                and item.source_account_id == source_account
                and type(item.metadata) is dict
                and item.metadata.get("catalogue_access") != "shared_read_only"
                and item.metadata.get("search_provenance") != "known_catalog_stale")

    def invoke(_button):
        nonlocal pending
        if pending or not visible():
            return
        row.set_subtitle("Informations non vérifiées.")
        read = prepare_borrower_information(
            service, item, identity_id=account.identity_id, account_id=account.unique_id)
        if not callable(read):
            return

        pending = True

        def done(values):
            nonlocal pending
            pending = False
            if visible():
                row.set_subtitle(advisory_text(values))

        def failed(_error):
            done(None)

        try:
            window.runner.submit(
                "Consultation informative…", read, done, failed,
                control=button, priority=TaskPriority.USER_ACTION,
                kind="borrow-information", origin="user",
            )
        except Exception:
            failed(None)

    button.connect("clicked", invoke)
    panel._append_content(group)
    return True
