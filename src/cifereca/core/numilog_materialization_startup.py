"""Bounded opt-in Numilog *materialization* worker for a real startup event.

Never called by inventory or render. Every candidate is re-admitted by the
existing locked, credential/edition-scoped explicit acquisition path (#2043).
There is no periodic mode, silent "missing" inference, or automatic retry of
pending ADEPT/index receipts.
"""
from __future__ import annotations

from collections import Counter
from time import time
from typing import Any

from ..models import SearchResult
from ..plugin_api import Cancelled, SyncResult
from .numilog_local_preview import _item_id
from .numilog_materialization_checkpoint import index_delivered
from .numilog_materialization_consent import (
    _binding, _MAX_ITEMS_PER_OPENING, mode,
)


def _result(status: str, *, ok: bool = False,
            downloaded: int = 0, attempted: int = 0,
            skipped: int = 0, failed: int = 0) -> SyncResult:
    return SyncResult(
        ok=ok, changed=downloaded, warnings=() if ok else (status,),
        facts={
            "phase": "materialize", "trigger": "startup",
            "status": status, "downloaded": downloaded,
            "attempted": attempted, "skipped": skipped, "failed": failed,
            "acquisition_authorized": False,
        },
    )


def eligible_source_ids(broker: Any, identity_id: str) -> tuple[str, ...]:
    """Passive policy/connection check: no filesystem I/O or provider call."""
    try:
        accounts = [
            a for a in broker.service.accounts
            if getattr(a, "provider_id", None) == "numilog"
            and getattr(a, "identity_id", None) == identity_id
        ]
        if len(accounts) > 64:
            return ()
        ids = []
        for account in accounts:
            sid = broker._source_id(account)
            if mode(broker, sid) == "on_start":
                ids.append(sid)
        return tuple(ids)
    except Exception:
        return ()


def execute(broker: Any, identity_id: str, trigger: str, control: Any = None) -> SyncResult:
    """At most 8 exact purchases per source; each failure stops the batch."""
    if trigger != "startup" or type(identity_id) is not str:
        return _result("invalid_trigger")
    total = attempts = skipped = failures = 0
    sources = eligible_source_ids(broker, identity_id)
    if not sources:
        return _result("not_due", ok=True)
    for source_id in sources:
        bound = _binding(broker, source_id)
        if bound is None or mode(broker, source_id) != "on_start":
            continue
        account, session, _roots = bound
        if control is not None and callable(getattr(control, "check_cancelled", None)):
            control.check_cancelled()
        try:
            # The claim is taken BEFORE private-provider I/O, atomically
            # across application instances. On error we never roll it back.
            if not broker.service.storage.numilog_materialization_claim_startup(
                source_id=source_id, now=int(time()),
            ):
                continue
            if mode(broker, source_id) != "on_start":
                continue
            raw = session.library()
            if type(raw) not in {list, tuple} or len(raw) > 256:
                failures += 1
                break
            if (_binding(broker, source_id) is None
                    or _binding(broker, source_id)[1] is not session
                    or mode(broker, source_id) != "on_start"):
                failures += 1
                break
            # Duplicate editions on a private provider page are ambiguous.
            count = Counter(
                row.record_id for row in raw
                if type(row) is SearchResult and type(row.record_id) is str
            )
            candidates = [
                row for row in raw
                if type(row) is SearchResult
                and row.source_provider == "numilog"
                and row.source_credential == account.credential_id
                and row.source_collection == "library"
                and row.scenario == "NUMILOG_LIBRARY"
                and row.owned is True and row.downloadable is True
                and row.id == row.record_id
                and _item_id(row.record_id, account.credential_id)
                and count[row.record_id] == 1
            ]
            for row in candidates:
                if attempts >= _MAX_ITEMS_PER_OPENING:
                    break
                if control is not None and callable(getattr(control, "check_cancelled", None)):
                    control.check_cancelled()
                if (mode(broker, source_id) != "on_start"
                        or _binding(broker, source_id) is None
                        or _binding(broker, source_id)[1] is not session):
                    failures += 1
                    break
                prior = broker.preflight_numilog_purchase(source_id, row.record_id)
                if (prior.ok is not True
                        or prior.facts.get("status") != "candidate_unverified"):
                    skipped += 1
                    continue

                def admission() -> SearchResult:
                    # Called inside global acquisition lock, at each
                    # readmission boundary including after provider/ADEPT.
                    if (mode(broker, source_id) != "on_start"
                            or _binding(broker, source_id) is None
                            or _binding(broker, source_id)[1] is not session):
                        raise ValueError("numilog_auto_consent_revoked")
                    check = broker.preflight_numilog_purchase(source_id, row.record_id)
                    if (check.ok is not True
                            or check.facts.get("status") != "candidate_unverified"):
                        raise ValueError("numilog_auto_purchase_changed")
                    return row

                attempts += 1
                acquired = broker.service.acquire_direct_item(
                    row, _admission=admission,
                )
                # The provider created a durable receipt under the lock;
                # index without downloading again. Failed indexing leaves
                # an attention-required receipt and is NOT auto-retried.
                index_delivered(broker.service, acquired, title=row.title)
                total += 1
                if control is not None and callable(getattr(control, "report", None)):
                    control.report(current=total, message="Sauvegarde Numilog")
        except Cancelled:
            raise
        except Exception:
            failures += 1
            break
        if failures:
            break
    return _result(
        "partial" if failures and total else "failed" if failures else "complete",
        ok=failures == 0, downloaded=total, attempted=attempts,
        skipped=skipped, failed=failures,
    )
