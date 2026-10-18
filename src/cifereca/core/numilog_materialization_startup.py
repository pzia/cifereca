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
from .numilog_manifest_rights import mirror_numilog_manifest_rights
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
            try:
                sid = broker._source_id(account)
                if (mode(broker, sid) == "on_start"
                        and _binding(broker, sid) is not None):
                    ids.append(sid)
            except Exception:
                # A corrupt sibling must not suppress other source-specific
                # consents for this identity. No provider request is made.
                continue
        return tuple(ids)
    except Exception:
        return ()


def execute(broker: Any, identity_id: str, trigger: str, control: Any = None) -> SyncResult:
    """At most eight attempts *per source*; failures never starve siblings."""
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
        # A persisted "on_start" may be revoked and explicitly re-enabled
        # while this batch is in flight. Its new consent is NOT a grant to
        # revive this earlier job (or to reuse an already-fetched manifest).
        consent_revision = (
            broker.service.storage.numilog_materialization_consent_revision(
                source_id,
            )
        )
        if control is not None and callable(getattr(control, "check_cancelled", None)):
            control.check_cancelled()
        # Counters and receipts are per source, not leaked across accounts.
        previous = (total, attempts, skipped, failures)
        started_at = None
        manifest_ready = False
        interrupted = False
        try:
            # Claim and 'running' receipt are written in the same SQLite
            # transaction, before any network I/O or ADEPT. No blind retry.
            attempt_clock = int(time())
            if not broker.service.storage.numilog_materialization_claim_startup(
                source_id=source_id, now=attempt_clock,
            ):
                continue
            started_at = attempt_clock
            refreshed = _binding(broker, source_id)
            if (
                refreshed is None or refreshed[1] is not session
                or refreshed[0].identity_id != identity_id
                or refreshed[0].unique_id != account.unique_id
                or mode(broker, source_id) != "on_start"
                or broker.service.storage.numilog_materialization_consent_revision(
                    source_id,
                ) != consent_revision
            ):
                continue
            raw = session.library()
            # The private catalogue can contain hundreds of purchases.
            # Bound memory without blocking ordinary 300+ book libraries.
            if type(raw) not in {list, tuple} or len(raw) > 2048:
                failures += 1
                continue
            manifest_ready = True
            if (_binding(broker, source_id) is None
                    or _binding(broker, source_id)[1] is not session
                    or mode(broker, source_id) != "on_start"
                    or broker.service.storage.numilog_materialization_consent_revision(
                        source_id,
                    ) != consent_revision):
                failures += 1
                continue
            # Freshly purchased editions may have no cached local right yet.
            # Mirror only attested purchases from THIS authenticated library;
            # the generic public catalogue never enters this path. The mirror
            # performs its own account/session re-admission before writing.
            # This is still NOT permission to acquire: each edition passes
            # preflight plus the locked admission after this read.
            mirror_numilog_manifest_rights(
                broker.service, raw,
                identity_id=identity_id,
                credential_id=account.credential_id,
                account_id=account.unique_id,
            )
            refreshed = _binding(broker, source_id)
            if (
                refreshed is None or refreshed[1] is not session
                or refreshed[0].identity_id != identity_id
                or refreshed[0].unique_id != account.unique_id
                or mode(broker, source_id) != "on_start"
                or broker.service.storage.numilog_materialization_consent_revision(
                    source_id,
                ) != consent_revision
            ):
                failures += 1
                continue
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
                # The total is only for the aggregate receipt. The consent
                # and durable claim are per account, so the download quota
                # must reset for each independently opted-in account.
                if attempts - previous[1] >= _MAX_ITEMS_PER_OPENING:
                    break
                if control is not None and callable(getattr(control, "check_cancelled", None)):
                    control.check_cancelled()
                if (mode(broker, source_id) != "on_start"
                        or broker.service.storage.numilog_materialization_consent_revision(
                            source_id,
                        ) != consent_revision
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
                            or broker.service.storage.numilog_materialization_consent_revision(
                                source_id,
                            ) != consent_revision
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
            interrupted = True
            raise
        except Exception:
            # One source's private manifest/ACSM/index failure cannot deny
            # another independently confirmed account its startup attempt.
            # Its receipt is finalized in the finally block below.
            failures += 1
            continue
        finally:
            if started_at is not None:
                own_downloaded = total - previous[0]
                own_attempted = attempts - previous[1]
                own_skipped = skipped - previous[2]
                own_failed = failures - previous[3]
                result_status = (
                    "partial" if own_downloaded and (own_failed or interrupted)
                    else "failed" if own_failed
                    else "blocked" if interrupted or not manifest_ready
                    else "complete"
                )
                try:
                    broker.service.storage.numilog_materialization_finish_startup(
                        source_id=source_id, started_at=started_at,
                        status=result_status, attempted=own_attempted,
                        downloaded=own_downloaded, skipped=own_skipped,
                        failed=own_failed,
                    )
                except Exception:
                    # Never leak a persistence exception containing source
                    # metadata, or mask a cooperative cancellation. The
                    # existing durable "running" receipt remains truthful.
                    pass
    return _result(
        "partial" if failures and total else "failed" if failures else "complete",
        ok=failures == 0, downloaded=total, attempted=attempts,
        skipped=skipped, failed=failures,
    )
