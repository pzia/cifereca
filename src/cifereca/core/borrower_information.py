"""Explicit advisory read, independent of borrowing admission and refresh."""
from __future__ import annotations

from time import time
from inspect import getattr_static, isfunction

from ..models import Account, SearchResult
from .borrow_action_scope import capture_borrow_action_scope
from .loan_action_scope import _inert_session_accounts
from .search_orchestration import capture_search_read_scope


def _display_values(raw):
    """Allow only closed display fields; never forward provider diagnostics."""
    if type(raw) is not dict:
        return None
    stamp = raw.get("observed_at")
    if type(stamp) is not int or not 0 < stamp <= int(time()):
        return None
    return {
        "observed_at": stamp,
        "pass_state": "unknown",
        "remaining_quota": None,
        "available_copies": None,
        "item_offer": ("unavailable" if raw.get("item_offer") == "unavailable"
                       else "unknown"),
        "permission": "unknown",
    }


def prepare_borrower_information(service, item, *, identity_id, account_id):
    """Capture a local lease; returned callable performs one explicit read.

    No admission policy, claim, storage write, refresh hook or fallback
    provider is consulted. Missing optional provider methods fail closed.
    """
    try:
        if (type(item) is not SearchResult or item.medium != "digital"
                or type(account_id) is not str or not account_id
                or type(item.metadata) is not dict
                or item.metadata.get("catalogue_access") == "shared_read_only"
                or item.metadata.get("search_provenance") == "known_catalog_stale"
                or item.source_account_id not in (None, account_id)):
            return None
        scope = capture_search_read_scope(service, identity_id)
        session = service._sessions.get(service._session_key(
            item.source_provider, item.source_credential))
        registered = _inert_session_accounts(session)
        if registered is None:
            return None
        accounts = [
            a for a in service.accounts
            if type(a) is Account and a.is_primary is True
            and (a.identity_id, a.provider_id, a.credential_id, a.unique_id)
            == (identity_id, item.source_provider, item.source_credential, account_id)
            and sum(member is a for member in registered) == 1
        ]
        if len(accounts) != 1:
            return None
        account = accounts[0]
        lease = capture_borrow_action_scope(
            service, item, account, session, expected_identity_id=identity_id,
            reader_scope=scope,
        )
        source_account = item.source_account_id

        def current():
            return (lease is not None and lease.current(service)
                    and item.source_account_id == source_account
                    and type(item.metadata) is dict
                    and item.metadata.get("catalogue_access") != "shared_read_only"
                    and item.metadata.get("search_provenance") != "known_catalog_stale")

        if not current():
            return None
        names = ("borrower_information_for", "borrower_information_display")
        abi = tuple(getattr_static(session, name, None) for name in names)
        # As for the selected delivery ABI, descriptors/properties are not
        # admissible methods: evaluating them could run I/O during preparation.
        if not all(isfunction(method) for method in abi):
            return None

        def abi_current():
            return all(getattr_static(session, name, None) is method
                       for name, method in zip(names, abi))

        observe, project = (getattr(session, name) for name in names)
        if not callable(observe) or not callable(project) or not current() or not abi_current():
            return None

        from threading import Lock
        lock = Lock()
        used = False

        def read():
            nonlocal used
            with lock:
                if used:
                    return None
                used = True
            try:
                if not current() or not abi_current():
                    return None
                observation = observe(item, account)
                if not current() or not abi_current():
                    return None
                values = _display_values(project(observation, item, account))
                return values if current() and abi_current() else None
            except Exception:
                return None

        return read
    except Exception:
        return None
