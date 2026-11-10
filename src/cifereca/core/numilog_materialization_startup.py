"""Read-only compatibility status for the retired Numilog v1 startup worker.

No legacy source policy, manifest row or diagnostic can authorize a GET.
Canonical source-wide ON is executed by source Sync's common A→B executor,
not by a second Numilog receipt namespace. Historical on_start consent is
still visible and explicitly revocable; it is NOT promoted automatically.
"""
from __future__ import annotations

from typing import Any

from ..plugin_api import SyncResult
from .canonical_delivery_abi import has_complete_delivery_abi


def _canonical_source_delivery_active(session: Any) -> bool:
    """Passive shared ABI detector; used for diagnostics only."""
    return has_complete_delivery_abi(session)


def eligible_source_ids(broker: Any, identity_id: str) -> tuple[str, ...]:
    """The old worker has no eligible sources, regardless of past consent."""
    return ()


def execute(broker: Any, identity_id: str, trigger: str, control: Any = None) -> SyncResult:
    """No provider I/O, claim, backup or additional GET under any trigger."""
    if trigger != "startup" or type(identity_id) is not str:
        return SyncResult(
            ok=False, changed=0, warnings=("invalid_trigger",),
            facts={"phase": "materialize", "status": "invalid_trigger",
                   "acquisition_authorized": False},
        )
    # The retired consent is historical state, not an implicit v2 ON grant.
    # Only notify when a source formerly opted into the incompatible worker.
    opted_in = False
    try:
        from .numilog_materialization_consent import mode
        opted_in = any(
            type(getattr(account, "identity_id", None)) is str
            and account.identity_id == identity_id
            and getattr(account, "provider_id", None) == "numilog"
            and mode(broker, broker._source_id(account)) == "on_start"
            for account in broker.service.accounts
        )
    except Exception:
        opted_in = True  # cannot assert a successful migration
    status = "migration_required" if opted_in else "not_due"
    return SyncResult(
        ok=not opted_in, changed=0,
        warnings=(status,) if opted_in else (),
        facts={"phase": "materialize", "status": status,
               "acquisition_authorized": False,
               "legacy_writer_active": False},
    )
