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
from .numilog_startup_diagnostics import StartupTrace
from .numilog_local_preview import _item_id
from .numilog_materialization_checkpoint import index_delivered
from .numilog_manifest_rights import mirror_numilog_manifest_rights
from .numilog_materialization_consent import (
    _binding, _same_binding, _MAX_ITEMS_PER_OPENING, mode,
)


def _result(status: str, *, ok: bool = False,
            downloaded: int = 0, attempted: int = 0,
            skipped: int = 0, failed: int = 0,
            blocked: int = 0, claimed: int = 0, source_results=()) -> SyncResult:
    return SyncResult(
        ok=ok, changed=downloaded, warnings=() if ok else (status,),
        facts={
            "phase": "materialize", "trigger": "startup",
            "status": status, "downloaded": downloaded,
            "attempted": attempted, "skipped": skipped, "failed": failed,
            "blocked": blocked, "sources_claimed": claimed,
            "acquisition_authorized": False, "source_results": tuple(source_results),
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
    total = attempts = skipped = failures = blocked = claimed = 0
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
                type(row.owned) is not bool
                or type(row.downloadable) is not bool
                or (
                    row.owned is True
                    and (
                        type(row.id) is not str
                        or type(row.record_id) is not str
                        or row.id != row.record_id
                        or not _item_id(row.record_id, account.credential_id)
                        or count[row.record_id] != 1
                    )
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
            mirrored = mirror_numilog_manifest_rights(
                broker.service, raw,
                identity_id=identity_id,
                credential_id=account.credential_id,
                account_id=account.unique_id,
            )
            # The authenticated inventory is the authority for the number
            # of owned editions. An account rebind, collision or silent mirror
            # refusal is not a successful empty backup; never acquire until
            # every attested purchase has been recorded as a local right.
            expected_rights = sum(row.owned is True for row in raw)
            if type(mirrored) is not int or mirrored != expected_rights:
                trace.fail("rights_mirror_failed")
                failures += 1
                continue
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
                        or not same_source()):
                    trace.fail("consent_revoked")
                    failures += 1
                    break
                observe("preflight")
                prior = broker.preflight_numilog_purchase(source_id, row.record_id)
                if prior.facts.get('status') == 'declared_link_recorded':
                    # A recorded user association requires local verification,
                    # not a duplicate download or a successful "0 attempted".
                    blocked += 1
                    trace.fail('preflight_failed')
                    continue
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
                            or not same_source()):
                        trace.fail("consent_revoked")
                        raise ValueError("numilog_auto_consent_revoked")
                    check = broker.preflight_numilog_purchase(source_id, row.record_id)
                    if (check.ok is not True
                            or check.facts.get("status") != "candidate_unverified"):
                        trace.fail("preflight_failed")
                        raise ValueError("numilog_auto_purchase_changed")
                    return row

                attempts += 1
                observe("acquisition")
                try:
                    with trace.acquisition():
                        acquired = broker.service.acquire_direct_item(
                            row, _admission=admission,
                        )
                    # The provider created a durable receipt under the lock;
                    # index without downloading again. Failed indexing leaves
                    # an attention-required receipt and is NOT auto-retried.
                    observe("index")
                    index_delivered(broker.service, acquired, title=row.title)
                    total += 1
                    if control is not None and callable(getattr(control, "report", None)):
                        control.report(current=total, message="Sauvegarde Numilog")
                except Cancelled:
                    raise
                except Exception:
                    # One unverified/corrupt/blocked edition must not starve
                    # the independently authorized remaining purchases.
                    # Never retry this edition in this run, never replay its
                    # signed ACSM, and preserve the first failure phase/code.
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
                        failed=own_failed,
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
        blocked=blocked, claimed=claimed, source_results=source_results,
    )
