"""Bounded opt-in Numilog *materialization* worker for a real startup event.

Never called by inventory or render. Every candidate is re-admitted by the
existing locked, credential/edition-scoped explicit acquisition path (#2043).
There is no periodic mode, silent "missing" inference, or automatic retry of
pending ADEPT/index receipts.
"""
from __future__ import annotations

from collections import Counter
from time import time, monotonic
from typing import Any

from ..models import SearchResult
from ..plugin_api import Cancelled, SyncResult
from .numilog_startup_diagnostics import StartupTrace, local_proof
from .numilog_local_preview import _item_id
from .numilog_materialization_checkpoint import index_delivered
from .numilog_manifest_rights import mirror_numilog_manifest_rights
from .numilog_recovery import recovery_gate, recovery_id
from .numilog_materialization_consent import (
    _binding, _same_binding, _MAX_ITEMS_PER_OPENING, mode,
)


def _result(status: str, *, ok: bool = False,
            downloaded: int = 0, attempted: int = 0,
            skipped: int = 0, failed: int = 0,
            blocked: int = 0, claimed: int = 0, declared_local: int = 0,
            declared_pending: int = 0, source_results=()) -> SyncResult:
    return SyncResult(
        ok=ok, changed=downloaded, warnings=() if ok else (status,),
        facts={
            "phase": "materialize", "trigger": "startup",
            "status": status, "downloaded": downloaded,
            "attempted": attempted, "skipped": skipped, "failed": failed,
            "blocked": blocked, "sources_claimed": claimed,
            "already_local_declared": declared_local,
            "declared_needs_review": declared_pending,
            "acquisition_authorized": False, "source_results": tuple(source_results),
        },
    )


def _completed_local_purchase_verified(
    broker: Any, account: Any, source_id: str, item_id: str,
    *, source_current: Any,
) -> bool:
    """Reuse existing exact Numilog receipt/placement/SHA checks, never acquire.

    A prior ledger alone is not proof of a local EPUB. Refuse foreign,
    duplicate, incomplete or changed receipts and recheck SQLite after the
    physical hash. Existing local facts are journaled per purchase; no new
    provider call or acquisition permission is produced.
    """
    try:
        if not source_current():
            local_proof(account, source_id, item_id, "source_changed")
            return False
        storage = broker.service.storage
        options = dict(
            identity_id=account.identity_id, credential_id=account.credential_id,
            account_id=account.unique_id, item_id=item_id,
        )
        before = storage.numilog_purchase_preflight_evidence(**options)
        rights = before.get("entitlements")
        ledger = before.get("acquisitions")
        if (type(rights) is not list or len(rights) > 1
                or type(ledger) is not list or not 1 <= len(ledger) <= 2):
            local_proof(account, source_id, item_id, "rights_or_receipt_ambiguous",
                        right_count=len(rights) if type(rights) is list else None,
                        receipt_count=len(ledger) if type(ledger) is list else None)
            return False
        right = rights[0] if rights else None
        from ..acquisition_engine import stable_id
        from .numilog_recovery import recovery_id
        from .numilog_materialization_checkpoint import _checked_receipt
        original_id = stable_id(
            "numilog-materialization", account.identity_id,
            account.credential_id, account.unique_id, item_id,
        )
        retry_id = recovery_id(
            account.identity_id, account.credential_id,
            account.unique_id, item_id,
        )
        checkpoint = [r for r in ledger if type(r) is dict
                      and r.get("acquisition_id") in (original_id, retry_id)]
        # The old preview verifies legacy receipts with an exact right; new
        # Core delivery checkpoints can be verified without a cached right.
        new_delivery = [r for r in checkpoint
                        if r.get("acquisition_id") == retry_id or right is None]
        if len(new_delivery) == 1:
            receipt = new_delivery[0]
            if (receipt.get("status") != "complete"
                    or (right is not None and
                        receipt.get("entitlement_id") not in (
                            None, right.get("entitlement_id")))
                    or any(type(other) is not dict
                           or other.get("identity_id") != account.identity_id
                           or other.get("account_id") != account.unique_id
                           or other.get("item_id") != item_id
                           or (other is not receipt and other.get("status") != "complete")
                           for other in ledger)):
                local_proof(account, source_id, item_id, "receipt_incomplete_or_scope_mismatch")
                return False
            try:
                checked, path = _checked_receipt(
                    broker.service, receipt["acquisition_id"],
                )
                placement = storage.placement_for_path(path)
                sha = checked["metadata"]["sha256"]
                artifact = storage.artifact_get("sha256:" + sha)
                verified = bool(
                    source_current()
                    and placement is not None
                    and placement.get("identity_id") == account.identity_id
                    and placement.get("provider_id") == "numilog"
                    and placement.get("source_item_id") == item_id
                    and placement.get("state") == "present"
                    and placement.get("artifact_id") == "sha256:" + sha
                    and artifact is not None
                    and artifact.get("sha256") == sha
                    and artifact.get("size") == checked["metadata"]["size"]
                    and before == storage.numilog_purchase_preflight_evidence(**options)
                    and source_current()
                )
                local_proof(account, source_id, item_id,
                            "verified" if verified else "local_presence_unverified")
                return verified
            except Cancelled:
                raise
            except Exception:
                local_proof(account, source_id, item_id, "verification_failed", exc_info=True)
                return False
        if right is None:
            # Legacy rows without an exact acquisition checkpoint cannot
            # establish current physical presence, but this observation alone
            # is never a veto on an authorized provider re-download.
            local_proof(account, source_id, item_id, "receipt_incomplete_or_scope_mismatch")
            return False
        if len(ledger) != 1:
            local_proof(account, source_id, item_id, "rights_or_receipt_ambiguous",
                        right_count=len(rights), receipt_count=len(ledger))
            return False
        receipt = ledger[0]
        if (type(right) is not dict or type(receipt) is not dict
                or receipt.get("status") != "complete"
                or receipt.get("identity_id") != account.identity_id
                or receipt.get("account_id") != account.unique_id
                or receipt.get("item_id") != item_id
                or receipt.get("entitlement_id") != right.get("entitlement_id")):
            local_proof(account, source_id, item_id, "receipt_incomplete_or_scope_mismatch",
                        acquisition_id=receipt.get("acquisition_id") if type(receipt) is dict else None,
                        receipt_status=receipt.get("status") if type(receipt) is dict else None,
                        receipt_identity_id=receipt.get("identity_id") if type(receipt) is dict else None,
                        receipt_account_id=receipt.get("account_id") if type(receipt) is dict else None,
                        receipt_item_id=receipt.get("item_id") if type(receipt) is dict else None,
                        receipt_entitlement_id=receipt.get("entitlement_id") if type(receipt) is dict else None,
                        expected_entitlement_id=right.get("entitlement_id") if type(right) is dict else None)
            return False
        from .numilog_local_preview import preview
        check = preview(
            broker, account, source_id, verify_local_files=True,
            _only_item_id=item_id,
        )
        counts = check.facts.get("counts")
        if (check.ok is not True or type(counts) is not dict
                or counts.get("owned_purchases") != 1
                or counts.get("already_local_verified") != 1):
            local_proof(account, source_id, item_id, "local_presence_unverified",
                        preview_status=check.facts.get("status"), preview_counts=counts)
            return False
        # A verified file from a previous account/session or a changed right
        # never becomes a successful presence just because bytes match.
        if not source_current():
            local_proof(account, source_id, item_id, "source_changed")
            return False
        after = storage.numilog_purchase_preflight_evidence(**options)
        if before != after:
            local_proof(account, source_id, item_id, "evidence_changed_after_file_check")
            return False
        if not source_current():
            local_proof(account, source_id, item_id, "source_changed")
            return False
        local_proof(account, source_id, item_id, "verified")
        return True
    except Cancelled:
        raise
    except Exception:
        local_proof(account, source_id, item_id, "verification_failed", exc_info=True)
        return False


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
    total = attempts = skipped = failures = blocked = claimed = declared_local = declared_pending = 0
    source_results = []
    sources = eligible_source_ids(broker, identity_id)
    if not sources:
        return _result("not_due", ok=True)
    for source_id in sources:
        bound = _binding(broker, source_id)
        if bound is None or mode(broker, source_id) != "on_start":
            continue
        account, session, roots = bound
        trace = StartupTrace(broker.service, source_id)
        previous = (total, attempts, skipped, failures, blocked)
        previous_declared_local = declared_local
        previous_declared_pending = declared_pending
        presence_seconds_remaining = 8.0

        def observe(phase, code="started"):
            trace.counts.update(downloaded=total - previous[0], attempted=attempts - previous[1],
                                skipped=skipped - previous[2], failed=failures - previous[3])
            trace.event(phase, code)

        observe("admission")
        scope = (source_id, identity_id, account.credential_id, account.unique_id)

        def same_source() -> bool:
            # Freeze dispatch coordinates, including the preview's local root.
            # A configuration change cannot move an in-flight startup batch.
            return _same_binding(broker, source_id, scope, session, roots)
        # A persisted "on_start" may be revoked and explicitly re-enabled
        # while this batch is in flight. Its new consent is NOT a grant to
        # revive this earlier job (or to reuse an already-fetched manifest).
        try:
            consent_revision = broker.service.storage.numilog_materialization_consent_revision(
                source_id,
            )
        except Cancelled:
            trace.fail("interrupted")
            source_results.append(trace.finish("blocked"))
            raise
        except Exception:
            failures += 1
            trace.counts["failed"] = 1
            trace.fail()
            source_results.append(trace.finish("failed"))
            continue
        if type(consent_revision) is not str:
            # An active policy without its confirmation epoch is incomplete.
            # Block only this source and never consume a startup claim for it.
            failures += 1
            trace.counts["failed"] = 1
            trace.fail("consent_revoked")
            source_results.append(trace.finish("failed"))
            continue
        # Counters and receipts are per source, not leaked across accounts.
        started_at = None
        manifest_ready = False
        interrupted = False
        try:
            # The pre-claim cancellation check must share the same diagnostic
            # finalization as every later check. Otherwise a cancelled startup
            # has no phase/cause at all, even though admission was entered.
            if control is not None and callable(getattr(control, "check_cancelled", None)):
                control.check_cancelled()
            # Claim and 'running' receipt are written in the same SQLite
            # transaction, before any network I/O or ADEPT. No blind retry.
            observe("durable_claim")
            attempt_clock = int(time())
            if not broker.service.storage.numilog_materialization_claim_startup(
                source_id=source_id, now=attempt_clock, expected_revision=consent_revision,
            ):
                continue
            started_at = attempt_clock
            trace.clock = str(started_at)
            trace.claimed = True
            claimed += 1
            if (
                not same_source()
                or mode(broker, source_id) != "on_start"
                or broker.service.storage.numilog_materialization_consent_revision(
                    source_id,
                ) != consent_revision
            ):
                # A revoked/rebound binding after the durable claim is a
                # blocked attempt, not a silently completed backup.
                blocked += 1
                trace.fail("consent_revoked")
                continue
            observe("manifest_fetch")
            raw = session.library()
            # The private catalogue can contain hundreds of purchases.
            # Bound memory without blocking ordinary 300+ book libraries.
            if (type(raw) not in {list, tuple} or len(raw) > 2048
                    or any(type(row) is not SearchResult for row in raw)
                    or any(
                        type(row.source_provider) is not str
                        or row.source_provider != "numilog"
                        or type(row.source_credential) is not str
                        or row.source_credential != account.credential_id
                        or type(row.source_collection) is not str
                        or row.source_collection != "library"
                        or type(row.scenario) is not str
                        or row.scenario != "NUMILOG_LIBRARY"
                        for row in raw
                    )):
                # A typed result attributed to another connection or to the
                # public catalogue cannot prove this authenticated account's
                # library. Never mistake a mixed/foreign manifest for an empty
                # successful backup, mirror its rights or acquire its ACSM.
                trace.fail("manifest_invalid")
                failures += 1
                continue
            # Before any owned-rights write, require a complete and uniquely
            # scoped identifier for every provider-advertised downloadable
            # purchase. The real Numilog adapter deduplicates its own pages.
            # Silently skipping a malformed or duplicated purchase would
            # produce a false "complete / 0 attempted" initial backup.
            count = Counter(
                row.record_id for row in raw if type(row.record_id) is str
            )
            if (any(
                (row.owned is not None and type(row.owned) is not bool)
                or (row.downloadable is not None and type(row.downloadable) is not bool)
                or (
                        type(row.id) is not str
                        or type(row.record_id) is not str
                        or row.id != row.record_id
                        or not _item_id(row.record_id, account.credential_id)
                        or count[row.record_id] != 1
                    )
                for row in raw
            )):
                trace.fail("manifest_invalid")
                failures += 1
                continue
            manifest_ready = True
            if (not same_source()
                    or mode(broker, source_id) != "on_start"
                    or broker.service.storage.numilog_materialization_consent_revision(
                        source_id,
                    ) != consent_revision):
                trace.fail("consent_revoked")
                failures += 1
                continue
            observe("rights_mirror")
            # Freshly purchased editions may have no cached local right yet.
            # Mirror only attested purchases from THIS authenticated library;
            # the generic public catalogue never enters this path. The mirror
            # performs its own account/session re-admission before writing.
            # This is still NOT permission to acquire: each edition passes
            # preflight plus the locked admission after this read.
            try:
                mirrored = mirror_numilog_manifest_rights(
                    broker.service, raw,
                    identity_id=identity_id,
                    credential_id=account.credential_id,
                    account_id=account.unique_id,
                )
            except Cancelled:
                raise
            except Exception:
                # Metadata projection is best effort. Preserve the reason in
                # the local journal; the fresh downloadable offer stays valid.
                from .numilog_startup_diagnostics import mirror_observation_error
                mirror_observation_error(source_id)
                mirrored = None
            # A cached entitlement is informational, not a mandatory
            # predecessor to provider delivery (#3171/2026-10-30).
            # Failed metadata mirroring must not veto an independently
            # admitted, account-scoped downloadable offer.
            expected_rights = sum(row.owned is True for row in raw)
            if type(mirrored) is not int or mirrored != expected_rights:
                trace.event("rights_mirror", "rights_mirror_incomplete")
            if (
                not same_source()
                or mode(broker, source_id) != "on_start"
                or broker.service.storage.numilog_materialization_consent_revision(
                    source_id,
                ) != consent_revision
            ):
                trace.fail("consent_revoked")
                failures += 1
                continue
            # The checked manifest snapshot already has bounded, typed IDs
            # and duplicate counts; do not re-evaluate them after mirroring.
            candidates = [
                row for row in raw
                if type(row) is SearchResult
                and row.source_provider == "numilog"
                and row.source_credential == account.credential_id
                and row.source_collection == "library"
                and row.scenario == "NUMILOG_LIBRARY"
                and row.id == row.record_id
                and _item_id(row.record_id, account.credential_id)
                and count[row.record_id] == 1
            ]
            for position, row in enumerate(candidates, start=1):
                # The total is only for the aggregate receipt. The consent
                # and durable claim are per account, so the download quota
                # must reset for each independently opted-in account.
                if attempts - previous[1] >= _MAX_ITEMS_PER_OPENING:
                    # The per-opening quota leaves purchases unexamined.
                    # A partial batch must never be reported as complete.
                    blocked += 1
                    trace.record_item(position, 'quota_reached', item_id=row.record_id)
                    trace.fail('preflight_failed')
                    break
                if control is not None and callable(getattr(control, "check_cancelled", None)):
                    control.check_cancelled()
                if (mode(broker, source_id) != "on_start"
                        or broker.service.storage.numilog_materialization_consent_revision(
                            source_id,
                        ) != consent_revision
                        or not same_source()):
                    trace.record_item(position, "consent_revoked", item_id=row.record_id)
                    trace.fail("consent_revoked")
                    failures += 1
                    break
                observe("preflight")
                prior = broker.preflight_numilog_purchase(source_id, row.record_id)
                if prior.facts.get('status') == 'declared_link_recorded':
                    from .library_declared_presence import verify_declared_presence
                    if presence_seconds_remaining <= 0:
                        # A verification budget is not a download permission
                        # veto; the declared link remains only an observation.
                        prior_status = 'needs_reconciliation'
                        declared_pending += 1
                    else:
                        presence_started = monotonic()
                        try:
                            presence = verify_declared_presence(
                            broker.service, identity_id=account.identity_id,
                            provider_id='numilog', credential_id=account.credential_id,
                            account_id=account.unique_id, item_id=row.record_id,
                            source_current=lambda: (
                                same_source() and mode(broker, source_id) == 'on_start'
                                and broker.service.storage.numilog_materialization_consent_revision(
                                    source_id) == consent_revision
                            ),
                            cancelled=lambda: bool(control is not None
                                and callable(getattr(control, 'cancelled', None)) and control.cancelled()),
                            seconds=presence_seconds_remaining,
                            )
                        finally:
                            presence_seconds_remaining = max(0.0, presence_seconds_remaining
                                - max(0.0, monotonic() - presence_started))
                        if control is not None and callable(getattr(control, 'check_cancelled', None)):
                            control.check_cancelled()
                        if presence.status == 'declared_current':
                            declared_local += 1
                            skipped += 1
                            trace.record_item(position, 'already_local_declared', item_id=row.record_id)
                            continue
                        # Invalid/obsolete declaration stays historical.
                        declared_pending += 1
                        prior_status = 'needs_reconciliation'
                else:
                    prior_status = prior.facts.get('status')
                # The offline preflight conservatively reports *any*
                # existing receipt as needs_reconciliation. A previous
                # completed purchase must nevertheless count as present if
                # the existing exact receipt, placement and current SHA all
                # agree. Unknown/foreign/damaged evidence remains blocked.
                is_recovery = False
                if prior_status in {
                    'needs_reconciliation', 'purchase_unproven', 'not_downloadable',
                }:
                    if presence_seconds_remaining > 0:
                        proof_started = monotonic()
                        try:
                            verified = _completed_local_purchase_verified(
                                broker, account, source_id, row.record_id,
                                source_current=lambda: (
                                    same_source() and mode(broker, source_id) == 'on_start'
                                    and broker.service.storage.numilog_materialization_consent_revision(
                                        source_id) == consent_revision
                                ),
                            )
                        finally:
                            presence_seconds_remaining = max(
                                0.0, presence_seconds_remaining
                                - max(0.0, monotonic() - proof_started),
                            )
                        if control is not None and callable(
                            getattr(control, 'check_cancelled', None)
                        ):
                            control.check_cancelled()
                        if verified:
                            skipped += 1
                            trace.record_item(position, 'already_local_verified', item_id=row.record_id)
                            continue
                    # A legacy complete receipt or same-owner placement is
                    # not proof of a current file. It must not veto a fresh
                    # purchase already authorized by the persisted policy.
                    gate = recovery_gate(
                        broker.service.storage, identity_id=account.identity_id,
                        credential_id=account.credential_id,
                        account_id=account.unique_id, item_id=row.record_id,
                    )
                    # A fresh delivery without an existing receipt/placement
                    # can use the normal checkpoint. Legacy files or receipts
                    # use the isolated recovery receipt, never overwriting.
                    evidence = broker.service.storage.numilog_purchase_preflight_evidence(
                        identity_id=account.identity_id,
                        credential_id=account.credential_id,
                        account_id=account.unique_id, item_id=row.record_id,
                    )
                    conflicts = bool(evidence.get('acquisitions')
                                     or evidence.get('placement_conflict')
                                     or prior_status == 'needs_reconciliation')
                    if gate == 'recoverable':
                        is_recovery = conflicts
                    else:
                        blocked += 1
                        trace.record_item(position, gate, item_id=row.record_id)
                        trace.fail('preflight_failed')
                        continue
                # The fresh authenticated inventory is authoritative for
                # delivery capability. SQLite's older owned right may omit
                # 'downloadable'; a permissive preflight must never override
                # an explicit no-download observation from THIS source.
                if row.downloadable is not True:
                    blocked += 1
                    trace.record_item(position, 'not_downloadable', item_id=row.record_id)
                    trace.fail('preflight_failed')
                    continue
                if (not is_recovery and
                        prior_status not in {"candidate_unverified", "purchase_unproven",
                                              "not_downloadable"}):
                    # A prior receipt, conflicting placement, missing proof or
                    # non-downloadable purchased title does not attest a
                    # local file. Keep it pending instead of counting a
                    # silently skipped purchase in a 'complete' sync.
                    blocked += 1
                    # Use the already-qualified offline result, never a
                    # remote message, product ID, path or exception string.
                    reason = prior.facts.get("status")
                    trace.record_item(
                        position,
                        reason if type(reason) is str and reason in {
                            "needs_reconciliation", "purchase_unproven",
                            "source_not_ready", "evidence_unavailable",
                            "source_changed", "invalid_source",
                            "invalid_item_id",
                        } else "preflight_failed",
                        item_id=row.record_id,
                    )
                    trace.fail('preflight_failed')
                    continue

                def admission() -> SearchResult:
                    # Called inside global acquisition lock, at each
                    # readmission boundary including after provider/ADEPT.
                    if (mode(broker, source_id) != "on_start"
                            or broker.service.storage.numilog_materialization_consent_revision(
                                source_id,
                            ) != consent_revision
                            or not same_source()):
                        trace.fail("consent_revoked")
                        raise ValueError("numilog_auto_consent_revoked")
                    check = broker.preflight_numilog_purchase(source_id, row.record_id)
                    if is_recovery:
                        status_ok = (
                            check.facts.get('status') in {
                                'needs_reconciliation', 'declared_link_recorded',
                                'purchase_unproven', 'not_downloadable',
                            }
                            and recovery_gate(
                                broker.service.storage,
                                identity_id=account.identity_id,
                                credential_id=account.credential_id,
                                account_id=account.unique_id,
                                item_id=row.record_id,
                            ) == 'recoverable'
                        )
                    else:
                        status_ok = (
                            check.facts.get('status') in {
                                'candidate_unverified', 'purchase_unproven',
                                'not_downloadable',
                            }
                        )
                    if not status_ok:
                        trace.fail("preflight_failed")
                        raise ValueError("numilog_auto_purchase_changed")
                    return row

                attempts += 1
                observe("acquisition")
                try:
                    from contextlib import nullcontext
                    from .numilog_recovery import recovery_request
                    with trace.acquisition(), (recovery_request() if is_recovery else nullcontext()):
                        acquired = broker.service.acquire_direct_item(
                            row, _admission=admission,
                        )
                    # The provider created a durable receipt under the lock;
                    # index without downloading again. Failed indexing leaves
                    # an attention-required receipt and is NOT auto-retried.
                    observe("index")
                    index_delivered(broker.service, acquired, title=row.title)
                    total += 1
                    trace.record_item(position, 'acquired', item_id=row.record_id)
                    if control is not None and callable(getattr(control, "report", None)):
                        control.report(current=total, message="Sauvegarde Numilog")
                except Cancelled:
                    raise
                except Exception:
                    # One unverified/corrupt/blocked edition must not starve
                    # the independently authorized remaining purchases.
                    # Never retry this edition in this run, never replay its
                    # signed ACSM, and preserve the first failure phase/code.
                    trace.record_item(position, 'acquisition_failed', item_id=row.record_id)
                    trace.fail()
                    failures += 1
                    continue
        except Cancelled:
            trace.fail("interrupted")
            interrupted = True
            raise
        except Exception:
            # One source's private manifest/ACSM/index failure cannot deny
            # another independently confirmed account its startup attempt.
            # Its receipt is finalized in the finally block below.
            trace.fail()
            failures += 1
            continue
        finally:
            if started_at is not None:
                own_downloaded = total - previous[0]
                own_attempted = attempts - previous[1]
                own_skipped = skipped - previous[2]
                own_failed = failures - previous[3]
                own_blocked = blocked - previous[4]
                own_declared_local = declared_local - previous_declared_local
                own_declared_pending = declared_pending - previous_declared_pending
                result_status = (
                    "partial" if own_downloaded and (own_failed or own_blocked or interrupted)
                    else "failed" if own_failed
                    else "blocked" if own_blocked or interrupted or not manifest_ready
                    else "complete"
                )
                observe("receipt_finalization")
                try:
                    finalized = broker.service.storage.numilog_materialization_finish_startup(
                        source_id=source_id, started_at=started_at,
                        status=result_status, attempted=own_attempted,
                        downloaded=own_downloaded, skipped=own_skipped,
                        failed=own_failed, declared_local=own_declared_local,
                        declared_pending=own_declared_pending,
                    )
                    if finalized is not True:
                        # The stale-run SQL guard can refuse a final receipt
                        # without raising. "running" is not "complete":
                        # report a bounded failure while preserving its
                        # durable evidence for explicit reconciliation.
                        trace.fail("receipt_finalize_failed")
                        failures += 1
                except Exception:
                    # Never expose SQLite/provider arguments or override a
                    # cooperative Cancelled exception from the in-flight task.
                    # Do not report success when the durable receipt was not
                    # finalized, even if the ebook bytes were indexed.
                    trace.fail("receipt_finalize_failed")
                    failures += 1
                final_failed = failures - previous[3]
                final_status = ("partial" if own_downloaded and (final_failed or interrupted)
                                else "failed" if final_failed else result_status)
            else:
                final_status = "failed" if failures > previous[3] else "blocked" if interrupted else "not_due"
            trace.counts.update(downloaded=total - previous[0], attempted=attempts - previous[1],
                                skipped=skipped - previous[2], failed=failures - previous[3])
            source_results.append(trace.finish(final_status))
    # A startup with no successful claims never backed anything up. Never
    # tell the GUI it completed a run when every account was throttled.
    # Likewise, a post-claim revocation/rebind has a durable "blocked"
    # receipt and must not be reported as aggregate success.
    if failures:
        status = "partial" if total else "failed"
    elif blocked:
        status = "partial" if total else "blocked"
    else:
        status = "complete" if claimed else "not_due"
    return _result(
        status, ok=not (failures or blocked),
        downloaded=total, attempted=attempts,
        skipped=skipped, failed=failures,
        blocked=blocked, claimed=claimed, declared_local=declared_local,
        declared_pending=declared_pending, source_results=source_results,
    )
