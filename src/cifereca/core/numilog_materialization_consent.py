"""Numilog startup *materialization* consent, distinct from inventory (#1905).

No legacy backup switch, cache hit, schedule for another store, selected GUI
identity or source-sync grants permission.  A two-step explicit confirmation is
required for each exact source; old/malformed preferences default to manual.
"""
from __future__ import annotations

from typing import Any
from ..plugin_api import SyncResult

_PREFIX = "sync.source.numilog.materialization.consent.v1."
_ACTIVE = "on_start:v1"
_MANUAL = "manual"


def policy_key(source_id: str) -> str:
    return _PREFIX + source_id


def _source_accounts(broker: Any, source_id: str) -> list[Any]:
    """Isolate malformed *other* account rows before source-specific actions.

    This remains a revocation-only account lookup; it cannot authorize
    delivery or infer identity from the selected GUI profile.
    """
    import re
    if (type(source_id) is not str
            or re.fullmatch(r"sync:[0-9a-f]{24}", source_id) is None):
        return []
    try:
        accounts = broker.service.accounts
        if type(accounts) not in {list, tuple}:
            return []
        # These are already loaded local account declarations, not a remote
        # provider page. Never make explicit *revocation* impossible merely
        # because many unrelated providers are also configured.
        found = []
        for account in accounts:
            try:
                if (getattr(account, "provider_id", None) == "numilog"
                        and broker._source_id(account) == source_id):
                    found.append(account)
            except Exception:
                # One corrupt unrelated row cannot withhold consent
                # revocation or safe startup eligibility from its siblings.
                continue
        return found
    except Exception:
        return []


def mode(broker: Any, source_id: str) -> str:
    try:
        # The configured consent must remain visible and revocable while
        # disconnected. Eligibility for I/O is managed by the canonical source-sync gate.
        return ("on_start" if broker.service.storage.metadata_get(
            policy_key(source_id),
        ) == _ACTIVE else "manual")
    except Exception:
        return "manual"


def _receipt(status: str, *, ok: bool = False, **facts: Any) -> SyncResult:
    return SyncResult(
        ok=ok, changed=0, warnings=() if ok else (status,),
        facts={"phase": "materialize", "status": status,
               "acquisition_authorized": False, **facts},
    )



def prepare(broker: Any, source_id: str) -> SyncResult:
    """Retired v1 start-up consent cannot produce another provider GET.

    The canonical source-wide consent is managed by source_sync_consent.
    A former on_start option is still readable and explicitly revocable.
    """
    return _receipt("canonical_source_consent_required",
                    schedule_available=False)


def confirm(broker: Any, source_id: str, token: str, *, mode: str) -> SyncResult:
    """Refuse new legacy acquisition grants, even with old cached tokens."""
    return _receipt("canonical_source_consent_required",
                    schedule_available=False)


def revoke(broker: Any, source_id: str) -> str:
    # Revocation must work even after a session disconnects or a source is
    # disabled. A source unknown to this installation cannot be fabricated.
    rows = _source_accounts(broker, source_id)
    if len(rows) != 1:
        raise ValueError("unknown_numilog_source")
    broker.service.storage.numilog_materialization_change_consent(
        source_id, activate=False,
    )
    with broker._numilog_consent_plans.lock:
        broker._numilog_consent_plans.entries.clear()
    broker._notify()
    return "manual"
