"""Application synchronization coordinator and explicit event recording.

Facade-owned sessions, acquisition helpers, locks and storage remain unique;
no widgets or additional remote calls are introduced."""
from __future__ import annotations

from .source_context import source_context_epoch

from contextvars import ContextVar

import time
import hashlib
import json
import logging
from contextlib import ExitStack
from inspect import getattr_static
from .source_sync_timing import measure, timing_scope
from datetime import datetime, timezone
from typing import Any, Callable
from ..diagnostics import _core_journal_record
from ..locking import InterProcessLock, LockBusyError
from ..paths import acquisition_lock_file

_OPENING_CANONICAL_LEASE: ContextVar[InterProcessLock | None] = ContextVar(
    'cifereca.opening.canonical.lease', default=None,
)
from ..models import Account, Loan
from ..providers import ProviderConfigurationError


def _sync_loan_inventory_observation(self, account: Account, session: Any):
    """Read exactly once, preserving v1 Core normalization until migration.

    Typed sessions use the validated atomic envelope. Legacy installations
    still depend on the facade's account cache, positive normalization and
    provider/session lease checks. Retiring that path before their Plugin API
    migration loses positive loans; never mistake a v1 list for completeness.
    The two synchronization entrypoints share this one selection point.
    """
    from ..plugin_api import Cancelled
    from ..plugin_api.loan_inventory import read_loan_inventory
    if session is not None and getattr_static(
        session, "loan_inventory_snapshot", None,
    ) is not None:
        snapshot = read_loan_inventory(session, account)
        # A cancelled typed read is neither an empty successful inventory
        # nor a provider failure. Preserve its cancellation through source
        # Sync without a legacy retry or a rights/cache mutation.
        if snapshot.status == "cancelled":
            raise Cancelled("loan_inventory_cancelled")
        if snapshot.status == "failed":
            raise ProviderConfigurationError("loan_inventory_read_unavailable")
        return snapshot, list(snapshot.loans)
    # No second read after a failed/malformed typed snapshot, even when the
    # v1 list reader is available. The legacy facade also covers test shims
    # and currently installed v1 plugins which do not publish an envelope.
    return None, self._loans_for_account(account, force=True)


def _synchronize_account_source_impl(self, account: Account, *, deep: bool=False, control: Any=None) -> dict[str, Any]:
    """Synchronize one explicit account/source without changing account selection.

    This is the Core-owned primitive used by the public sync broker.  It
    deliberately isolates one source so disabling or failing a provider does
    not prevent the other sources from being synchronized.
    """
    result: dict[str, Any] = {"provider": account.provider_id, "account_id": account.unique_id or "primary"}
    # The low-level facade is callable without the public broker. Re-evaluate
    # the same enablement switch under the shared acquisition lock, before
    # any provider/session request. A corrupt preference must fail closed.
    if not self.sync_source_enabled(account):
        return {**result, "status": "skipped", "reason": "source_disabled"}
    session = self._session_for_account(account)

    def valid_kobo_source() -> bool:
        """Refresh must not publish rights from a stale Kobo binding."""
        try:
            matching = [
                current for current in self.accounts
                if current.provider_id == "kobo"
                and current.credential_id == account.credential_id
            ]
            bound = self.storage.identity_for_connection(
                "kobo", account.credential_id,
            )
            connections = (
                self.storage.provider_connections(identity_id=bound)
                if bound else []
            )
            return bool(
                len(matching) == 1
                and matching[0].identity_id == account.identity_id
                and matching[0].unique_id == account.unique_id
                and self.identity_by_id(account.identity_id) is not None
                and bound == account.identity_id
                and sum(
                    row.get("provider_id") == "kobo"
                    and row.get("credential_id") == account.credential_id
                    and row.get("enabled") is True
                    for row in connections
                ) == 1
                and self.sync_source_enabled(matching[0])
                and self._sessions.get(
                    self._session_key("kobo", account.credential_id)
                ) is session
                and session.connected is True
            )
        except Exception:
            return False

    if account.provider_id == "kobo" and not valid_kobo_source():
        raise ProviderConfigurationError("Source Kobo non liée au compte demandé")
    if account.provider_id == "numilog":
        from .numilog_manifest_rights import numilog_inventory_binding_valid
        if not numilog_inventory_binding_valid(self, account, session=session):
            # No provider call or inventory authorization on a stale account,
            # detached identity or disabled connection. Never select the
            # current GTK account as a fallback owner.
            raise ProviderConfigurationError("Source Numilog non liée au compte demandé")
    if not session.connected:
        raise ProviderConfigurationError(f"Provider non connecté: {account.provider_id}")
    if control is not None and callable(getattr(control, "check_cancelled", None)):
        control.check_cancelled()
    if session.capabilities.loans:
        # Snapshot the live source before provider I/O. A stale/disabled
        # account must never publish rights belonging to a former identity.
        source_scope = (
            account.identity_id, account.provider_id,
            account.credential_id, account.unique_id,
            source_context_epoch(self),
        )
        # One selection point for typed envelope and installed v1 adapters.
        # Legacy positive lists can never certify missing/returned loans.
        with measure("loan_refresh_ms"):
            snapshot, loans = _sync_loan_inventory_observation(
                self, account, session,
            )
        if not _opening_loans_scoped(account, source_scope[0], loans):
            raise ProviderConfigurationError("loan_source_scope_mismatch")
        matching = [
            row for row in self.accounts
            if type(row) is Account
            and (row.identity_id, row.provider_id,
                 row.credential_id, row.unique_id) == source_scope[:4]
        ]
        if (len(matching) != 1 or matching[0] is not account
                or source_context_epoch(self) != source_scope[4]
                or self.identity_by_id(account.identity_id) is None
                or self._sessions.get(self._session_key(
                    account.provider_id, account.credential_id,
                )) is not session
                or session.connected is not True
                or not self.sync_source_enabled(account)):
            raise ProviderConfigurationError("loan_source_scope_changed")
        if control is not None and callable(getattr(control, "check_cancelled", None)):
            control.check_cancelled()
        # Only a typed snapshot with explicit complete digital coverage
        # may prove absence. Legacy, unknown and partial stay positive-only.
        complete_scope = (
            _complete_digital_loan_scope(account, snapshot)
            if snapshot is not None else None
        )
        if loans or complete_scope is not None:
            from . import library_mutation
            with measure("rights_mirror_ms"):
                library_mutation._persist_active_loans(
                    self, account.identity_id, loans,
                    reconcile_missing=complete_scope is not None,
                    complete_scopes={complete_scope} if complete_scope else None,
                    realize_expired=False,
                )
        if snapshot is not None:
            # A typed read is not a committed observation if rights-mirroring
            # rejected it. Never poison subsequent cache reads with that batch.
            _cache_validated_typed_loan_observation(self, account, loans)
        result["loans"] = len(loans)
        if account.provider_id == 'bnf-pret':
            from .bnf_auto_materialization import enabled, materialize
            if enabled(self, account):
                # One private bounded inventory per source batch, not one
                # whole-directory scan for every active loan.
                physical_scan_cache = {}
                outcomes = [
                    materialize(self, account, loan, control=control,
                                physical_scan_cache=physical_scan_cache)
                    for loan in loans
                ]
                result['files_materialized'] = sum(row['status'] == 'complete' for row in outcomes)
                result['files_unchanged'] = sum(row['status'] == 'unchanged' for row in outcomes)
                # Confirmed user declarations are present local files, not
                # new ADEPT deliveries and not provider-verified receipts.
                result['files_declared_local'] = sum(
                    row['status'] == 'declared_local' for row in outcomes
                )
                # A file needing an explicit local reconciliation is not
                # newly materialized or already linked. Emit only an aggregate
                # counter; no title, path, loan ID or licence in sync facts.
                result['files_reconciliation_required'] = sum(
                    row.get('code') in {
                        'bnf_local_candidate_reconciliation_required',
                        'bnf_local_declaration_needs_review',
                    }
                    for row in outcomes
                )
                if any(row['status'] not in {'complete', 'unchanged', 'declared_local', 'skipped'} for row in outcomes):
                    result['status'] = 'partial'
        # A v1 loans() list never proves full branch coverage, even when
        # last_loan_errors is empty or absent. That mutable side channel is
        # deliberately not an authority for this explicit sync result.
        # Typed producers may attest a successful observation, but only
        # explicit branch coverage can authorize a negative rights transition.
        if snapshot is None or snapshot.status == "partial":
            result["status"] = "partial"
    if session.capabilities.personal_library:
        # A provider can implement a one-shot iterable: both the entitlement
        # mirror and remote catalog must see the exact same authenticated rows.
        with measure("library_read_ms"):
            rows = list(session.library())
        if account.provider_id == "numilog" and not numilog_inventory_binding_valid(
            self, account, session=session,
        ):
            raise ProviderConfigurationError("Source Numilog non liée au compte demandé")
        if account.provider_id == "numilog":
            from .numilog_private_rows import private_library_rows_valid
            if not private_library_rows_valid(rows, account.credential_id):
                raise ProviderConfigurationError("Inventaire privé Numilog non conforme")
        if account.provider_id == "kobo" and not valid_kobo_source():
            raise ProviderConfigurationError("Source Kobo modifiée pendant la lecture")
        if account.provider_id == "kobo":
            # Persist attested personal rights, but never start Kobo acquisition.
            from .kobo_manifest_rights import mirror_kobo_manifest_rights
            with measure("rights_mirror_ms"):
                result["owned_rights"] = mirror_kobo_manifest_rights(
                    self, rows, identity_id=account.identity_id,
                    credential_id=account.credential_id,
                    account_id=account.unique_id or None,
                )
        elif account.provider_id == "numilog":
            # The private connected library is the only positive evidence of
            # purchases. Public search, even with owned=True, cannot grant
            # ownership. No signed ACSM resolution or download happens here.
            from .numilog_manifest_rights import mirror_numilog_manifest_rights
            with measure("rights_mirror_ms"):
                result["owned_rights"] = mirror_numilog_manifest_rights(
                    self, rows, identity_id=account.identity_id,
                    credential_id=account.credential_id,
                    account_id=account.unique_id or None,
                )
        # A source-level sync is an inventory/rights refresh, not a request
        # to materialize local files. In particular, Numilog may advertise
        # direct_download but that capability must not implicitly acquire
        # every returned EPUB from the generic Sync broker (#1346).
        # The separate legacy opening/acquisition workflow remains unchanged.
        if account.provider_id == "numilog" and not numilog_inventory_binding_valid(
            self, account, session=session,
        ):
            # Revoke a stale source even if the ownership projection completed.
            raise ProviderConfigurationError("Source Numilog non liée au compte demandé")
        with measure("catalog_mirror_ms"):
            result["library"] = self._mirror_remote_results(rows, identity_id=account.identity_id)
    if deep:
        with measure("catalog_scan_ms"):
            result["catalog_scan"] = self.catalog_scan()
    return result


def _run_observed_canonical_convergence(
    self, account: Account, admitted_snapshots: list, *,
    admitted_epoch: int, sync_lease: InterProcessLock,
    cancelled_for_rights: Callable[[], bool],
) -> dict[str, int] | None:
    """Run the SAME canonical A→B executor for explicit and opening Sync.

    The caller must already hold the real global acquisition lease. Only one
    freshly admitted typed source snapshot can supply A. No legacy backup
    preference, v1 list, catalog match or opening event grants consent: the
    persisted exact SourceKey authorization and session/offers are checked by
    the original executor, under that same lock, before and after network I/O.
    """
    if account.provider_id == "bnf-pret":
        from .bnf_auto_materialization import enabled as bnf_legacy_auto_enabled
        # The legacy BnF auto route still owns declared-local loan matching
        # and receipts. Running its fulfilled ACSM again through A→B would
        # duplicate ADEPT/placements. While that source's explicit auto
        # switch is ON, let exactly one existing path own the acquisition.
        # Other providers and BnF exact-right/manual scopes retain canonical.
        if bnf_legacy_auto_enabled(self, account):
            return None
    if (type(admitted_snapshots) is not list or len(admitted_snapshots) != 1
            or type(sync_lease) is not InterProcessLock
            or sync_lease._handle is None
            or sync_lease.path.resolve() != acquisition_lock_file().resolve()):
        return None
    from ..plugin_api import Cancelled
    # An optional provider-neutral candidate ABI is now
    # available on installed BnF/Numilog sessions. Core owns
    # its *finalization* callback; the provider never emits
    # a CompletedDelivery or OriginLink. No compatibility
    # fallback or implied consent for older sessions.
    delivery = getattr(self, "_canonical_right_delivery", None)
    consent_reader = getattr(self, "_canonical_persisted_consent", None)
    if (delivery is None and admitted_snapshots
            and callable(consent_reader)):
        from inspect import getattr_static
        try:
            candidate_session = self._session_for_account(account)
        except (AttributeError, KeyError, TypeError, ValueError):
            candidate_session = None
        if candidate_session is not None and any(
            callable(getattr_static(candidate_session, name, None))
            for name in ("downloadable_selected_delivery_candidate",
                         "rights_selected_delivery_candidate")
        ):
            from .canonical_selected_delivery import complete_selected_delivery
            delivery = lambda key, offer: complete_selected_delivery(
                self, account, candidate_session, key, offer,
                library_root=self.library_dir(), shared_lock=sync_lease,
            )
    if (admitted_snapshots and callable(delivery)
            and callable(consent_reader)):
        from .convergence_execution import execute_admitted_convergence
        from .source_convergence import SourceKey
        from .source_auto_consent_bridge import (
            consent_generation, consent_generation_in_transaction,
        )
        # Freeze an exact source/type epoch BEFORE the first grant read.
        # The legacy unversioned Kobo switch remains admissible; any later
        # ON→OFF→ON advances its revision and invalidates this older attempt.
        versioned_source = account.provider_id in {"numilog", "kobo", "bnf-pret"}
        frozen_epoch = (
            consent_generation(self.storage, admitted_snapshots[0].scope)
            if versioned_source else None
        )

        try:
            consent = consent_reader(account)
        except Exception:
            # A consent backend exception may serialize private credentials.
            # Deny delivery and publish a closed, scope-free diagnostic.
            logging.getLogger("cifereca").warning(
                "TRACE canonical Sync code=canonical_consent_read_failed"
            )
            consent = frozenset()
        if type(consent) is not frozenset or any(
            type(source) is not SourceKey for source in consent
        ):
            consent = frozenset()

        # An unchanged final boolean is insufficient: a new source-wide
        # consent generation must not revive an old signed/one-use delivery.
        def epoch_current(key: SourceKey) -> bool:
            return (
                not versioned_source
                or (frozen_epoch not in {"unavailable", "invalid"}
                    and key.scope == admitted_snapshots[0].scope
                    and consent_generation(self.storage, key.scope)
                    == frozen_epoch)
            )

        def current_consent(key: SourceKey) -> bool:
            # Re-admission on every offer, GET and post-GET boundary.
            if not epoch_current(key):
                return False
            latest = consent_reader(account)
            return (type(latest) is frozenset
                    and key in latest
                    and all(type(x) is SourceKey for x in latest)
                    and epoch_current(key))

        existing_publication_guard = getattr(
            self, "_canonical_publication_consent_current", None,
        )

        def publication_consent(db, key: SourceKey) -> bool:
            # Execute in publisher's BEGIN IMMEDIATE, not a stale read.
            if versioned_source and (
                frozen_epoch in {"unavailable", "invalid"}
                or key.scope != admitted_snapshots[0].scope
                or consent_generation_in_transaction(db, key) != frozen_epoch
            ):
                return False
            return (existing_publication_guard(db, key)
                    if callable(existing_publication_guard) else True)

        # The optional selected-offer preflight is permitted
        # only under the already held lock, exact persisted
        # consent and the registered Core delivery route.
        # It returns A only; the executor still owns all GET,
        # receipt, revalidation and atomic B publication.
        from .canonical_selected_offers import qualify_selected_offers
        session = self._session_for_account(account)
        root = self.library_dir()
        qualified_snapshot = qualify_selected_offers(
            self, account, session, admitted_snapshots[0],
            admitted_epoch=admitted_epoch, shared_lock=sync_lease,
            library_root=root, persisted_consent=consent,
            consent_still_current=current_consent,
            cancelled=cancelled_for_rights,
        )
        outcomes = execute_admitted_convergence(
            self, account, session, qualified_snapshot,
            admitted_epoch=admitted_epoch,
            shared_lock=sync_lease, library_root=root,
            persisted_consent=consent,
            consent_still_current=current_consent,
            deliver=delivery, cancelled=cancelled_for_rights,
            publication_guard=publication_consent,
        )
        counts = {
            status: sum(row.status == status for row in outcomes)
            for status in ("downloaded", "recovered", "present_attested",
                           "pending", "blocked", "failed", "cancelled", "skipped_archived", "inactive")
        }
        return counts


def synchronize_account_source(self, account: Account, *, deep: bool=False, control: Any=None) -> dict[str, Any]:
    from .public_provider_diagnostics import registered_public_provider_id
    from ..plugin_api import Cancelled
    started = time.monotonic()
    timings: dict[str, int] = {}

    def observe(phase, status, code, result=None):
        try:
            provider = registered_public_provider_id(self.registry, account.provider_id)
            self.diagnostics.add_core_source_sync(
                provider, phase=phase, status=status, code=code,
                duration_ms=(max(0, int((time.monotonic() - started) * 1000))
                             if phase == "completed" else None), counts=result,
                timings=timings if phase == "completed" else None,
            )
        except Exception:
            pass  # telemetry is optional and never changes the sync outcome

    observe("start", "running", "source_sync_started")
    try:
        # Source synchronization is also a library writer. Serialize it
        # with maintenance destruction and opening/Kobo acquisition across
        # processes; never wait for the lock on a caller/UI thread.
        with timing_scope(timings), ExitStack() as locks:
            with measure("lock_acquire_ms"):
                sync_lease = locks.enter_context(
                    InterProcessLock(acquisition_lock_file())
                )
            with measure("locked_work_ms"):
                result = self._synchronize_account_source_impl(account, deep=deep, control=control)
                if result.get("status") != "skipped":
                    # #3171: one optional typed A read and verified B plan
                    # within the existing source Sync's acquisition lock.
                    from .canonical_sync_stage import observe_canonical_rights_in_sync

                    def cancelled_for_rights() -> bool:
                        if control is not None:
                            check = getattr(control, "check_cancelled", None)
                            if callable(check):
                                check()
                        return False

                    admitted_epoch = source_context_epoch(self)
                    admitted_snapshots = []
                    canonical = observe_canonical_rights_in_sync(
                        self, account, cancelled=cancelled_for_rights,
                        snapshot_sink=admitted_snapshots.append,
                    )
                    if canonical is not None:
                        result["canonical_rights"] = canonical
                        if canonical["status"] != "success":
                            # A partial or failed source is not proof of
                            # complete refresh or of any negative right.
                            result["status"] = "partial"

                    counts = _run_observed_canonical_convergence(
                        self, account, admitted_snapshots,
                        admitted_epoch=admitted_epoch, sync_lease=sync_lease,
                        cancelled_for_rights=cancelled_for_rights,
                    )
                    if (counts is None and canonical is not None
                            and canonical.get("pending", 0) > 0):
                        # An optional delivery ABI may be absent. Its absence
                        # cannot turn the known unsatisfied A→B plan into PASS.
                        result["status"] = "partial"
                    if counts is not None:
                        result["canonical_execution"] = counts
                        if counts["cancelled"]:
                            raise Cancelled("canonical_sync_cancelled")
                        # Inventory success does not finish A→B while a right
                        # still awaits an offer/consent. This is not an error.
                        if counts["failed"] or counts["blocked"] or counts["pending"]:
                            result["status"] = "partial"
                        if counts["downloaded"]:
                            result["files_materialized"] = (
                                int(result.get("files_materialized") or 0)
                                + counts["downloaded"]
                            )
        outcome = (
            "skipped" if result.get("status") == "skipped"
            else "partial" if result.get("status") == "partial"
            else "success"
        )
        raw_reason = result.get("reason")
        reason = (raw_reason if type(raw_reason) is str
                  and raw_reason in {"source_disabled"} else "")
        self._record_action(
            account.identity_id, "synchronization", outcome,
            provider_id=account.provider_id, facts={
                "deep": bool(deep), "reason": reason,
            },
        )
        observe("completed", outcome, {
            "skipped": "source_disabled",
            "partial": "source_sync_partial",
            "success": "source_sync_complete",
        }[outcome], result)
        return result
    except LockBusyError as exc:
        observe("completed", "failed", "operation_busy")
        self._record_action(account.identity_id, "synchronization", "failed", provider_id=account.provider_id, facts={"deep": bool(deep), "error": "operation_busy"})
        raise ProviderConfigurationError("Synchronisation ou maintenance de la bibliothèque déjà en cours") from exc
    except Exception as exc:
        # Cancellation must be recorded as cancellation in BOTH the source
        # event and the action history, never counted as a provider failure.
        outcome = "cancelled" if isinstance(exc, Cancelled) else "failed"
        code = "source_sync_cancelled" if outcome == "cancelled" else "source_sync_failed"
        observe("completed", outcome, code)
        self._record_action(
            account.identity_id, "synchronization", outcome,
            provider_id=account.provider_id,
            facts={"deep": bool(deep), "error": code},
        )
        raise


def opening_sync(self, progress: Callable[[dict[str, Any]], None] | None=None, *, deep: bool=False, lock_factory, lock_path) -> dict[str, Any]:
    """Synchronize remote state; filesystem reconciliation is opt-in/deep."""
    lock = lock_factory(lock_path())
    try:
        with lock:
            lease_token = _OPENING_CANONICAL_LEASE.set(lock)
            try:
                result = self._opening_sync_locked(progress=progress, deep=deep)
            finally:
                _OPENING_CANONICAL_LEASE.reset(lease_token)
        for summary in result.get("identities", ()) if isinstance(result, dict) else ():
            if not isinstance(summary, dict):
                continue
            outcome = "failed" if str(summary.get("status") or "") == "failed" else "success"
            self._record_action(str(summary.get("identity_id") or "") or None, "synchronization", outcome, facts={"deep": bool(deep), "status": summary.get("status"), "errors": int(summary.get("errors") or 0)})
        return result
    except LockBusyError as exc:
        raise ProviderConfigurationError("Une synchronisation automatique est déjà en cours") from exc


def _kobo_opening_decision(self, *, deep: bool = False) -> dict[str, Any]:
    """Passive decision: only explicit *source* opt-ins permit acquisition.

    Historical global kobo_backup.enabled remains diagnostic/rollback data,
    never an authorization to download. Deep inventory scanning cannot
    silently change an acquisition policy. No provider calls here.
    """
    from .source_materialization_schedule import (
        kobo_on_start_sessions, on_start_due,
    )
    legacy_enabled = self.kobo_backup_enabled() is True
    kobo = [s for s in self._sessions.values() if s.provider_id == "kobo"]
    connected = [s for s in kobo if s.connected]
    capable = [s for s in connected if getattr(s.capabilities, "backup", False)]
    eligible_accounts = [
        a for a in self.accounts
        if a.provider_id == "kobo"
        and any(s.credential_id == a.credential_id for s in capable)
        and self.sync_source_enabled(a)
    ]
    eligible = [
        s for s in capable
        if any(a.credential_id == s.credential_id for a in eligible_accounts)
    ]
    scheduled = kobo_on_start_sessions(self)
    scheduled_credentials = {s.credential_id for s in scheduled}
    scheduled_accounts = [
        a for a in eligible_accounts if a.credential_id in scheduled_credentials
    ]
    if not legacy_enabled and not scheduled:
        reason = "disabled"
    elif not connected:
        reason = "no_session"
    elif not capable:
        reason = "capability_unavailable"
    elif not eligible:
        reason = "source_disabled"
    elif not scheduled:
        reason = "manual_only"
    elif any(on_start_due(self, account) for account in scheduled_accounts):
        reason = "started"
    else:
        reason = "fresh_cache"
    # Only age/counts, never user/account identifiers, file paths or tokens.
    raw_last = self.storage.metadata_get("kobo_backup_last_success_at")
    try:
        last = int(raw_last)
        age = int(time.time()) - last
        last_success_age_minutes = age // 60 if 0 <= age <= 366 * 86400 else None
    except (TypeError, ValueError, OverflowError):
        last_success_age_minutes = None
    return {
        "status": reason, "persisted_enabled": legacy_enabled,
        "configured_kobo": len(kobo), "connected_kobo": len(connected),
        "eligible_kobo": len(eligible),
        "eligible_kobo_bindings": len(eligible_accounts),
        "scheduled_kobo": len(scheduled),
        "interval_minutes": self.kobo_backup_interval_minutes(),
        "last_success_age_minutes": last_success_age_minutes,
        "deep": bool(deep),
    }


def _opening_loan_acquisition_allowed(loan: Loan, *, session: Any = None) -> bool:
    """Opening acquisition requires an explicit capability, never a provider-ID exception.

    This is a read-only admission check, not a replacement for the detailed
    ADEPT/identity preflight in _acquire_active_loan_automatically. The
    connection and the exact borrower must still be live at the moment of
    dispatch. New providers are default-deny; only legacy Paris opts in.
    """
    if (type(loan) is not Loan or loan.medium != "digital"
            or not bool(loan.ebook_url or loan.raw) or session is None):
        return False
    try:
        caps = session.capabilities
        if not (
            getattr(session, "connected", None) is True
            and getattr(caps, "opening_loan_auto_acquisition", None) is True
            and getattr(caps, "loans", None) is True
            and getattr(caps, "digital_download", None) is True
            and loan.source_provider == session.provider_id
            and loan.source_credential == session.credential_id
            and type(loan.identity_id) is str and bool(loan.identity_id)
            and type(loan.source_account_id) is str
        ):
            return False
        # The primary Paris account can be serialized under its empty
        # upstream ID or the Core alias "primary", but no other account
        # may inherit that alias. All other account IDs match exactly.
        for a in session.accounts:
            if type(a) is not Account:
                continue
            if (a.provider_id != loan.source_provider
                    or a.credential_id != loan.source_credential
                    or a.identity_id != loan.identity_id):
                continue
            valid_ids = {a.unique_id}
            if a.is_primary is True and a.unique_id == "":
                valid_ids.add("primary")
            if loan.source_account_id in valid_ids:
                return True
    except Exception:
        return False
    return False


def _opening_loans_scoped(account: Account, identity_id: str, rows: Any) -> bool:
    """Reject a mixed or malformed supplier batch before writing loan rights.

    An account listing may contain several upstream branches (Paris DILICOM
    and SYRACUSE), but may never carry another credential/borrower/identity.
    This is a positive-row safety gate, NOT an absence/completeness proof.
    """
    if type(account) is not Account or type(identity_id) is not str or type(rows) is not list:
        return False
    provider, credential = account.provider_id, account.credential_id
    if type(provider) is not str or not provider or type(credential) is not str or not credential:
        return False
    # "primary" is a Core alias for the Paris principal account's empty
    # upstream UID, NOT a default for any malformed/unpaired Account. A
    # secondary account with no UID (or the reserved alias as its UID)
    # must never publish or certify the principal borrower's loans.
    if type(account.unique_id) is not str:
        return False
    if (provider == "paris" and account.is_primary is not True
            and account.unique_id in ("", "primary")):
        return False
    expected_account = account.unique_id or ("primary" if provider == "paris" else "")
    if type(identity_id) is not str or not identity_id:
        return False
    # Do not silently keep whichever conflicting upstream row happens to
    # arrive first. Opening sync deduplicates later by loan ID; without this
    # test an item/branch/expiry contradiction disappears before SQLite sees it.
    from .library_mutation import _positive_loan_identity
    seen: dict[tuple, tuple] = {}
    for loan in rows:
        if not (
            type(loan) is Loan
            and type(loan.id) is str and bool(loan.id)
            and type(loan.source_provider) is str and loan.source_provider == provider
            and type(loan.source_credential) is str and loan.source_credential == credential
            and type(loan.source_account_id) is str and loan.source_account_id == expected_account
            and type(loan.identity_id) is str and loan.identity_id == identity_id
            and type(loan.medium) is str and loan.medium in ("digital", "physical", "unknown")
        ):
            return False
        key, signature = _positive_loan_identity(loan)
        if key in seen and seen[key] != signature:
            return False
        seen[key] = signature
    return True


def _complete_digital_loan_scope(
    account: Account, snapshot: Any,
) -> tuple[str, str, str] | None:
    """Only exact, exhaustive digital branches can authorize missing-loan writes.

    Paris and BnF have one known digital upstream each. A second digital or
    unclassified branch prevents whole-medium absence from being proven.
    """
    from ..plugin_api.loan_inventory import (
        LoanInventoryScope, LoanInventorySnapshot,
    )
    expected = {"paris": "dilicom", "bnf-pret": "bnf_html"}.get(
        account.provider_id if type(account) is Account else None,
    )
    if (expected is None or type(snapshot) is not LoanInventorySnapshot
            or snapshot.scope != LoanInventoryScope.for_account(account)
            or snapshot.status != "success"
            or not snapshot.branch_claims_complete(expected, "digital")):
        return None
    if (any(branch.medium == "unknown" for branch in snapshot.branches)
            or {branch.upstream for branch in snapshot.branches
                if branch.medium == "digital"} != {expected}):
        return None
    if account.provider_id == "paris" and account.unique_id == "":
        if account.is_primary is not True:
            return None
        borrower = "primary"
    else:
        if not account.unique_id:
            return None
        borrower = account.unique_id
    return account.provider_id, account.credential_id, borrower


def _registered_typed_loan_scope_current(
    self, account: Account, session: Any, scope: tuple[Any, ...],
) -> bool:
    """Revalidate the Core owner/epoch in addition to the provider envelope.

    A live session's credential/account can be replaced without closing the
    old Python object. The provider adapter cannot see the Core registry.
    """
    try:
        if (type(account) is not Account or (
            account.identity_id, account.provider_id,
            account.credential_id, account.unique_id,
            source_context_epoch(self),
        ) != scope):
            return False
        matching = [
            row for row in self.accounts
            if type(row) is Account and (
                row.identity_id, row.provider_id,
                row.credential_id, row.unique_id,
            ) == scope[:4]
        ]
        return bool(
            len(matching) == 1 and matching[0] is account
            and self.identity_by_id(account.identity_id) is not None
            and self._sessions.get(self._session_key(
                account.provider_id, account.credential_id,
            )) is session
            and getattr(session, "connected", None) is True
            and getattr(session, "provider_id", None) == scope[1]
            and getattr(session, "credential_id", None) == scope[2]
            and getattr(getattr(session, "capabilities", None), "loans", None) is True
            and any(
                type(reader) is Account and (
                    reader.identity_id, reader.provider_id,
                    reader.credential_id, reader.unique_id,
                ) == scope[:4]
                for reader in session.accounts
            )
            and self.sync_source_enabled(account)
        )
    except Exception:
        return False


def _cache_validated_typed_loan_observation(
    self, account: Account, loans: list[Loan],
) -> None:
    """Match legacy positive caching after successful rights persistence.

    No remote payload is retained: raw loan evidence remains transient.
    """
    from dataclasses import asdict
    from ..services import _loan_cache_key
    rows = []
    for loan in loans:
        item = asdict(loan)
        item["raw"] = {}
        rows.append(item)
    self.storage.cache_put(
        "account", _loan_cache_key(account), rows,
        int(self.config.get("cache.dashboard_seconds", 300)),
    )
    self._record_loan_statistics(account, loans)


def _opening_sync_locked(self, progress: Callable[[dict[str, Any]], None] | None=None, *, deep: bool=False, logger) -> dict[str, Any]:
    from ..brokers import SynchronizationBrokerImpl
    from ..plugin_api import Cancelled
    source_status = SynchronizationBrokerImpl(self)

    def emit(event: str, **detail: Any) -> None:
        if progress:
            try: progress({"event": event, **detail})
            except Exception: pass

    started = int(time.time())
    acsm_cleanup = self._cleanup_old_acsm_work()
    recovered = self.acquisition_reconciler.recover_interrupted()
    expired_before = self.acquisition_reconciler.realize_expired_loans()
    summaries: list[dict[str, Any]] = []
    all_loans: list[Loan] = []
    # One opening cycle must not schedule a second Kobo GET after Core A→B.
    # Exact scoped IDs are safer than a credential-only flag (multi-profiles).
    # Value records whether Core convergence completed without pending errors.
    canonical_kobo_sources: dict[str, bool] = {}
    # Device/profile state is provider-owned by Kobo-Adept. Read it once per
    # opening sync and only project it into the dashboard summary; Paris does
    # not persist a competing device inventory.
    adept_profiles: list[dict[str, Any]] = []
    try:
        if self._kobo_adapter().available():
            adept_profiles = self.kobo_profiles()
    except Exception:
        self.diagnostics.add(
            "ADEPT", "profile list for dashboard", "error",
            detail={"code": "adept_profile_lookup_failed"},
        )
    for identity in self.identities:
        run_id = self.storage.sync_run_put({
            "identity_id": identity.identity_id, "scope": "opening", "status": "running", "started_at": started,
            "detail": {"label": identity.label},
        })
        emit("identity_begin", identity_id=identity.identity_id, label=identity.label)
        errors = 0; new_acquisitions = 0; attention = 0
        detail: dict[str, Any] = {
            "label": identity.label, "providers": [], "loans": 0, "realized": 0,
            "skipped_borrower_binding": 0, "skipped_cross_identity": 0,
        }
        bound_profile = str(self.storage.adept_profile_binding_get("identity", identity.identity_id) or "").strip()
        if bound_profile:
            profile = next((p for p in adept_profiles if str(p.get("profile_id") or "") == bound_profile or str(p.get("name") or "") == bound_profile), None)
            detail["adept_profile_id"] = str((profile or {}).get("profile_id") or bound_profile)
            detail["adept_profile_name"] = str((profile or {}).get("name") or bound_profile)
            detail["adept_provisioning"] = dict((profile or {}).get("provisioning") or {})
            if adept_profiles and profile is None:
                detail["adept_attention"] = "profile_missing"
        accounts: list[Account] = []
        # Source-level rights/discovery outcomes must not inherit failures from
        # another account of the same identity (#1346). Keep only opaque source
        # IDs in this bookkeeping; the identity-level run remains aggregate.
        source_outcomes: dict[str, str] = {}
        try:
            loans: list[Loan] = []
            # Preserve the exact positive observation for each upstream
            # loan. A repeated ID with another edition/expiry is ambiguous,
            # never a reason to silently accept whichever page came first.
            seen_loans: dict[tuple[str, str, str, str], tuple] = {}
            provider_errors: list[dict[str, str]] = []
            # Each typed reader is pinned to the Core registry/epoch. Recheck
            # the whole staged set just before the identity-level rights write.
            typed_observations: list[tuple[Account, Any, tuple[Any, ...], list[Loan]]] = []
            complete_by_source: dict[str, tuple[str, str, str]] = {}
            accounts = sorted(
                [a for a in self.accounts_for_identity(identity.identity_id) if self.sync_source_enabled(a)],
                key=lambda a: (0 if a.provider_id == "paris" else 1, a.provider_id, a.credential_id, a.unique_id),
            )
            for account in accounts:
                source_id = self.sync_source_id(account)
                # A provider exception leaves this source failed, never successful.
                source_outcomes[source_id] = "failed"
                provider = account.provider_id
                if provider not in detail["providers"]:
                    detail["providers"].append(provider)
                try:
                    loan_started = time.monotonic()
                    logger.info("TRACE loans sync begin")
                    session = self._sessions.get(
                        self._session_key(account.provider_id, account.credential_id),
                    )
                    typed_scope = (
                        account.identity_id, account.provider_id,
                        account.credential_id, account.unique_id,
                        source_context_epoch(self),
                    )
                    # Preserve legacy positive normalization while typed
                    # snapshots carry coverage; neither allows unproven
                    # negative rights changes or a second provider read.
                    snapshot, account_loans = _sync_loan_inventory_observation(
                        self, account, session,
                    )
                    logger.info(
                        "TRACE loans sync end count=%s dt=%.3fs",
                        len(account_loans), time.monotonic() - loan_started,
                    )
                    if not _opening_loans_scoped(account, identity.identity_id, account_loans):
                        # Do not persist any row from an ambiguous account batch;
                        # avoid displaying or logging provider-controlled details.
                        raise ProviderConfigurationError("loan_source_scope_mismatch")
                    if snapshot is not None and not _registered_typed_loan_scope_current(
                        self, account, session, typed_scope,
                    ):
                        raise ProviderConfigurationError("loan_source_scope_changed")
                    # A v1 loans() list has NO verified branch/pagination
                    # coverage. Reading last_loan_errors (a mutable producer
                    # side channel) cannot prove completion, even when empty.
                    # Do not consult it. Personal-library-only providers are
                    # unaffected: they have no loan inventory to classify.
                    legacy_unverified = (
                        snapshot is None and session is not None
                        and getattr(getattr(session, "capabilities", None),
                                    "loans", None) is True
                    )
                    inventory_partial = (
                        snapshot is not None and snapshot.status == "partial"
                    ) or legacy_unverified
                    if inventory_partial:
                        # Unknown v1 coverage is not a *provider error*: do
                        # not inflate failure counts (or blame an acquisition).
                        # It still makes this source and identity incomplete.
                        if not legacy_unverified:
                            errors += 1
                        provider_errors.append({
                            "code": (
                                "loan_legacy_unverified" if legacy_unverified
                                else "loan_partial_failure"
                            ),
                        })
                        logger.warning(
                            "TRACE loans sync partial code=%s",
                            "loan_legacy_unverified" if legacy_unverified
                            else "loan_partial_failure",
                        )
                    # Stage this WHOLE account before accepting any row. If
                    # page N contradicts page 1, mark the account failed but
                    # retain independent successful accounts. A partial
                    # supplier response cannot prove negative loan status.
                    additions: dict[tuple[str, str, str, str], tuple] = {}
                    accepted: list[Loan] = []
                    for loan in account_loans:
                        key = (
                            loan.source_provider, loan.source_credential,
                            loan.source_account_id, loan.id,
                        )
                        signature = (
                            loan.provider, loan.medium, loan.record_id or loan.id,
                            loan.due_at, loan.document_type,
                        )
                        previous = (
                            additions[key] if key in additions else
                            seen_loans.get(key)
                        )
                        if previous is not None:
                            if previous != signature:
                                raise ProviderConfigurationError("loan_source_duplicate_conflict")
                            continue
                        additions[key] = signature
                        accepted.append(loan)
                    seen_loans.update(additions)
                    loans.extend(accepted)
                    if snapshot is not None:
                        typed_observations.append(
                            (account, session, typed_scope, list(account_loans)),
                        )
                        complete_scope = _complete_digital_loan_scope(account, snapshot)
                        if complete_scope is not None and not inventory_partial:
                            complete_by_source[source_id] = complete_scope

                    # #3171: opening Sync consumes the same optional A
                    # reader and verifies B with the exact canonical Core
                    # phase used by explicit source Sync. No additional
                    # reconciliation orchestration or automatic delivery
                    # permissions are invented by the startup path.
                    from .canonical_sync_stage import observe_canonical_rights_in_sync
                    admitted_epoch = source_context_epoch(self)
                    admitted_snapshots = []
                    canonical = observe_canonical_rights_in_sync(
                        self, account, snapshot_sink=admitted_snapshots.append,
                    )
                    if canonical is not None:
                        rights_detail = detail.setdefault("canonical_rights", {
                            "sources": 0, "observed": 0,
                            "present_attested": 0, "source_references_changed": 0,
                        })
                        for field in ("observed", "present_attested",
                                      "source_references_changed"):
                            rights_detail[field] += canonical[field]
                        rights_detail["sources"] += 1
                        if canonical["status"] != "success":
                            inventory_partial = True
                        if canonical["status"] == "failed":
                            errors += 1
                            provider_errors.append({
                                "code": "canonical_rights_observation_failed",
                            })
                    # The outer opening Sync already holds the global lock.
                    # Never acquire it a second time or create a parallel
                    # materialization/reconciliation worker. Older providers
                    # and accounts without exact persisted consent remain
                    # inventory-only, with zero selected-offer or candidate GET.
                    counts = _run_observed_canonical_convergence(
                        self, account, admitted_snapshots,
                        admitted_epoch=admitted_epoch,
                        sync_lease=_OPENING_CANONICAL_LEASE.get(),
                        cancelled_for_rights=lambda: False,
                    )
                    if (counts is None and canonical is not None
                            and canonical.get("pending", 0) > 0):
                        inventory_partial = True
                    if counts is not None:
                        # The canonical executor has already decided acquisition
                        # for this exact Kobo source (including pending/blocked).
                        # A legacy worker must not make a second decision/GET.
                        if account.provider_id == "kobo":
                            canonical_kobo_sources[source_id] = not any(
                                counts[name] for name in (
                                    "pending", "blocked", "failed", "cancelled"
                                )
                            )
                        completed = detail.setdefault("canonical_execution", {
                            status: 0 for status in counts
                        })
                        for key, count in counts.items():
                            completed[key] += count
                        if counts["cancelled"]:
                            raise Cancelled("canonical_opening_cancelled")
                        # Keep pending convergence visible without inflating
                        # provider errors or changing any source right.
                        if counts["blocked"] or counts["failed"] or counts["pending"]:
                            inventory_partial = True
                        new_acquisitions += counts["downloaded"]
                    source_outcomes[source_id] = (
                        "partial" if inventory_partial else "success"
                    )
                except Exception:
                    source_outcomes[source_id] = "failed"
                    errors += 1
                    provider_errors.append({"code": "loan_source_failed"})
                    # logger.exception would serialize a provider-controlled
                    # traceback, including credentials or a signed ACSM URL.
                    logger.warning("TRACE loans sync error code=loan_source_failed")
            loans.sort(key=lambda row: (str(row.due_at or "9999"), row.title.casefold()))
            # A batch may be rejected atomically on a later owner collision.
            # Do not publish its uncommitted positive loans in last_loans.
            detail["loans"] = len(loans)
            if provider_errors:
                detail["provider_errors"] = provider_errors
            # The identity batch may span multiple provider GETs. An earlier
            # typed reader must still be registered under its exact scope
            # after the last account read, before a single rights write.
            if any(
                not _registered_typed_loan_scope_current(self, account, session, scope)
                for account, session, scope, _rows in typed_observations
            ):
                raise ProviderConfigurationError("loan_source_scope_changed")
            # Current Paris/BnF plugins still publish unknown/partial digital
            # coverage. Only an explicit exhaustive, currently bound typed
            # result authorizes a scoped missing-loan transition.
            complete_scopes = {
                scope for source_id, scope in complete_by_source.items()
                if source_outcomes.get(source_id) == "success"
            }
            if complete_scopes:
                # Negative evidence uses the Core helper directly; keep the
                # LibraryService public method signature unchanged.
                from . import library_mutation
                lifecycle = library_mutation._persist_active_loans(
                    self, identity.identity_id, loans,
                    reconcile_missing=True, complete_scopes=complete_scopes,
                )
            else:
                lifecycle = self._persist_active_loans(identity.identity_id, loans)
            all_loans.extend(loans)
            for account, session, scope, observed in typed_observations:
                # Cache is a positive read optimization, never a source of
                # authority. If publication races with a logout, drop cache
                # rather than publishing an observation for the old binding.
                if _registered_typed_loan_scope_current(self, account, session, scope):
                    try:
                        _cache_validated_typed_loan_observation(self, account, observed)
                    except Exception:
                        logger.warning("TRACE typed loan cache failed code=loan_cache_unavailable")
            detail["realized"] = lifecycle["returned_realized"] + lifecycle["expired_realized"]
            physical_scan_cache = {}
            for loan in loans:
                # Re-resolve the currently registered provider session: an
                # earlier inventory GET may have run before disconnect/swap.
                # The admission check never invokes a provider method.
                session = self._sessions.get(
                    self._session_key(loan.source_provider, loan.source_credential)
                )
                if loan.source_provider == 'bnf-pret':
                    from .bnf_auto_materialization import enabled, materialize
                    account = next((a for a in self.accounts
                                    if a.identity_id == loan.identity_id
                                    and a.provider_id == 'bnf-pret'
                                    and a.credential_id == loan.source_credential
                                    and a.unique_id == loan.source_account_id), None)
                    if account is not None and enabled(self, account):
                        outcome = materialize(
                            self, account, loan, physical_scan_cache=physical_scan_cache,
                        )
                        if outcome['status'] == 'complete':
                            new_acquisitions += 1
                        elif outcome['status'] == 'attention_required':
                            attention += 1
                    continue
                if not _opening_loan_acquisition_allowed(loan, session=session):
                    continue
                result = self._acquire_active_loan_automatically(loan)
                if result["status"] == "complete":
                    new_acquisitions += 1
                    self._record_action(identity.identity_id, "acquisition", "success", provider_id=loan.source_provider, facts={"origin": "opening_sync"})
                elif result["status"] == "attention_required":
                    attention += 1
                    # Fixed public counters only: a completed sync task does
                    # not mean these protected loans were acquired. Keep the
                    # existing guards and their source-specific outcome intact.
                    if result.get("code") == "adept_borrower_binding_required":
                        detail["skipped_borrower_binding"] += 1
                    elif result.get("code") == "adept_loan_identity_conflict":
                        detail["skipped_cross_identity"] += 1
                    self._record_action(identity.identity_id, "acquisition", "failed", provider_id=loan.source_provider, facts={"origin": "opening_sync", "status": "attention_required"})
                elif result["status"] == "retryable_error":
                    errors += 1
                    self._record_action(identity.identity_id, "acquisition", "failed", provider_id=loan.source_provider, facts={"origin": "opening_sync", "status": "retryable_error"})
                if result["status"] in {"retryable_error", "attention_required"}:
                    # The acquiring loan belongs to one source. Do not mark
                    # healthy siblings partial because its ADEPT action failed.
                    # Reuse the same exact positive-loan scope contract
                    # used before persistence. Paris primary has
                    # Account.unique_id=="" but Loan.source_account_id=="primary";
                    # comparing raw UIDs would miss it and incorrectly mark
                    # EVERY other healthy source partial. Never infer owner
                    # solely from an identical credential or a book title.
                    affected = [
                        self.sync_source_id(account) for account in accounts
                        if _opening_loans_scoped(account, identity.identity_id, [loan])
                    ]
                    # An orphan/ambiguous loan cannot prove that any source
                    # completed its opening cycle. Be conservative.
                    if len(affected) != 1:
                        affected = list(source_outcomes)
                    for source_id in affected:
                        if source_outcomes.get(source_id) == "success":
                            source_outcomes[source_id] = "partial"
                    issue = {"provider": loan.source_provider, "loan_id": loan.id,
                                    "title": loan.title, "status": result["status"],
                                    "code": result.get("code"), "error": result.get("error"),
                                    "process": result.get("process", {})}
                    detail.setdefault("acquisition_errors", []).append(issue)
                    self.diagnostics.add("ACQUISITION", "loan sync", "error" if result["status"] == "retryable_error" else "warning", detail=issue, level="errors")
            if errors or attention or any(
                state == "partial" for state in source_outcomes.values()
            ):
                # Unknown v1 coverage is partial, not a transport error.
                # A partially successful source is not a total failure even
                # when its list contains zero entries. Provider error counts
                # need not equal the number of accounts (Paris has 2 branches).
                status = (
                    "partial" if loans or any(
                        state in ("partial", "success") for state in source_outcomes.values()
                    ) else "failed"
                )
            else:
                status = "success"
        except Exception:
            # Persist a stable reason, never an identity/storage/provider
            # exception that may disclose database paths or loan credentials.
            errors += 1; status = "failed"; detail["error"] = "opening_sync_failed"
            # An identity-level persistence/coordination failure invalidates
            # confirmation of the entire batch, even when a provider replied.
            source_outcomes = {
                self.sync_source_id(account): "failed" for account in accounts
            }
        finished = int(time.time())
        self.storage.sync_run_put({
            "sync_run_id": run_id, "identity_id": identity.identity_id, "scope": "opening", "status": status,
            "started_at": started, "finished_at": finished, "new_acquisitions": new_acquisitions,
            "attention_required": attention, "errors": errors, "detail": detail,
        })
        summary = {"identity_id": identity.identity_id, "label": identity.label, "status": status,
                   "new_acquisitions": new_acquisitions, "attention_required": attention, "errors": errors, **detail}
        summaries.append(summary)
        # GTK reads sync.source.state.*. Project each confirmed source result,
        # not the aggregate identity status (which can include other providers).
        attempted_at = datetime.fromtimestamp(started, timezone.utc).isoformat(timespec="seconds")
        for account in accounts:
            try:
                source_status.record_opening_state(
                    self.sync_source_id(account), identity_id=identity.identity_id,
                    status=source_outcomes.get(self.sync_source_id(account), "failed"),
                    attempted_at=attempted_at,
                )
            except Exception:
                # A source's provider ID and exception class are not safe
                # diagnostic strings. The persisted outcome remains failed.
                logger.warning("TRACE sync source projection failed code=source_state_projection_failed")
        emit("identity_end", **summary)

    # Personal store catalog mirroring is INVENTORY ONLY (#1862). Do not
    # report an automatic local acquisition merely because a provider advertises
    # direct_download or a mirrored notice is already owned. The old Numilog
    # bulk-backup path is intentionally disabled until a separate scoped policy.
    catalog_mirror = {
        "status": "not_run", "notices_mirrored": 0, "sources_reported": 0,
        "sources_failed": 0, "sources_blocked": 0,
        "materialization": {
            "status": "not_requested", "complete": 0, "unchanged": 0,
            "skipped": 0, "blocked": 0, "failed": 0, "attention_required": 0,
        },
    }
    try:
        mirror = self.mirror_personal_libraries()
        counts = (
            list(mirror.values()) if isinstance(mirror, dict)
            and all(type(count) is int and 0 <= count <= 1_000_000
                    for count in mirror.values())
            and len(mirror) <= 1_000_000
            else None
        )
        failures = getattr(mirror, "failed_sources", 0)
        blocked = getattr(mirror, "blocked_sources", 0)
        if (
            counts is None or type(failures) is not int
            or type(blocked) is not int
            or not 0 <= failures <= 1_000_000
            or not 0 <= blocked <= 1_000_000
        ):
            catalog_mirror["status"] = "unknown"
        else:
            catalog_mirror["notices_mirrored"] = min(1_000_000, sum(counts))
            catalog_mirror["sources_reported"] = len(counts)
            catalog_mirror["sources_failed"] = failures
            catalog_mirror["sources_blocked"] = blocked
            catalog_mirror["status"] = (
                "partial" if counts and (failures or blocked)
                else "failed" if failures
                else "blocked" if blocked
                else "inventory_only"
            )
    except Exception:
        # Even an exception *class name* can be supplied by a plugin. Only
        # the closed phase code belongs in an exportable TRACE.
        catalog_mirror["status"] = "failed"
        logger.warning("TRACE catalog mirror opening error code=catalog_mirror_failed")
    emit("catalog_mirror", result=catalog_mirror)

    # Inventory/rights above always remain independent of local acquisition.
    # No legacy global fallback: startup materializes only an explicitly
    # enabled, unambiguous account under the existing global acquisition lock.
    from .source_materialization_schedule import (
        kobo_on_start_sessions, on_start_due, mark_on_start_attempt,
    )
    from .kobo_backup import _kobo_checkpoint_key
    kobo_summary: dict[str, Any] | None = None
    kobo_decision = _kobo_opening_decision(self, deep=deep)
    eligible_kobo = kobo_on_start_sessions(self)
    scheduled_credentials = {session.credential_id for session in eligible_kobo}
    scheduled_accounts = {
        account.credential_id: account for account in self.accounts
        if account.provider_id == "kobo"
        and account.credential_id in scheduled_credentials
    }
    if kobo_decision["status"] in {"started", "fresh_cache"}:
        legacy_eligible = [
            session for session in eligible_kobo
            if self.sync_source_id(
                scheduled_accounts[session.credential_id]
            ) not in canonical_kobo_sources
        ]
        # The outer opening-sync already owns the cross-process lock.
        # The scoped helper is the ONLY automatic materialization path.
        due = [
            session for session in legacy_eligible
            if on_start_due(self, scheduled_accounts[session.credential_id])
        ]
        executed: list[dict[str, Any]] = []
        for session in due:
            account = scheduled_accounts[session.credential_id]
            # Settings/identity bindings may change while an opening task
            # progresses. A stale planned source never gains authorization.
            if session.credential_id not in {
                current.credential_id for current in kobo_on_start_sessions(self)
            }:
                executed.append({"status": "failed", "errors": 1, "sources": []})
                continue
            if not mark_on_start_attempt(self, account):
                executed.append({"status": "failed", "errors": 1, "sources": []})
                continue
            try:
                executed.append(
                    self._sync_kobo_backup_account_locked(
                        session.credential_id, progress=progress,
                    )
                )
            except Exception:
                logger.warning(
                    "TRACE kobo scheduled account sync error code=kobo_scheduled_sync_failed"
                )
                executed.append({"status": "failed", "errors": 1, "sources": []})
        metric_keys = (
            "errors", "downloaded", "unchanged", "attempted", "pending",
            "attention_required", "skipped_archived", "remaining", "manifest_items",
        )
        counters = {
            key: sum(int(result.get(key) or 0) for result in executed)
            for key in metric_keys
        }
        due_credentials = {session.credential_id for session in due}
        cached_keys = {
            _kobo_checkpoint_key(
                scheduled_accounts[session.credential_id].identity_id,
                session.credential_id,
            ).rsplit(".", 1)[-1]
            for session in legacy_eligible
            if session.credential_id not in due_credentials
        }
        cached_sources = [
            row for row in self.kobo_backup_diagnostic().get("sources", ())
            if row.get("source_id") in cached_keys
        ]
        sources = [
            source for result in executed
            for source in result.get("sources", ())
            if isinstance(source, dict)
        ] + cached_sources
        states = {str(row.get("status") or "partial") for row in sources}
        if any(result.get("status") == "error" for result in executed):
            batch_status = "error"
        elif (counters["errors"] or any(not ok for ok in canonical_kobo_sources.values())
              or {"aborted", "cancelled", "failed", "partial", "interrupted"} & states):
            batch_status = "partial"
        elif "attention_required" in states:
            batch_status = "attention_required"
        elif len(sources) < len(legacy_eligible):
            batch_status = "partial"
        else:
            batch_status = "complete"
        if not due and not canonical_kobo_sources:
            batch_status = (
                "fresh_cache" if batch_status == "complete"
                else "fresh_cache_partial"
            )
        kobo_summary = {
            **counters, "scope": "accounts", "status": batch_status,
            "sessions": len(eligible_kobo), "synced_accounts": len(due),
            "cached_accounts": len(legacy_eligible) - len(due),
            "canonical_sources": len(canonical_kobo_sources),
            "sources": sources, "skipped_fresh": not bool(due or canonical_kobo_sources),
        }
        kobo_decision["status"] = (
            "error" if batch_status == "error"
            else "partial" if batch_status in {
                "partial", "attention_required", "fresh_cache_partial", "cancelled",
            } else "fresh_cache" if batch_status == "fresh_cache" else "started"
        )
    elif kobo_decision["status"] == "manual_only":
        # A formerly enabled global toggle never licenses new downloads.
        kobo_summary = {"status": "manual_only", "skipped": True}
    for field in ("manifest_items", "owned", "archived", "downloadable",
                  "candidate_count", "attempted", "downloaded", "pending",
                  "attention_required", "reconcile_required", "errors"):
        value = (kobo_summary or {}).get(field)
        if type(value) is int and 0 <= value <= 1_000_000:
            kobo_decision[field] = value
    # One physical log record: the canonical formatter persists the complete
    # Core decision while existing TRACE listeners keep their original text.
    _decision_format = (
        "TRACE kobo backup decision status=%s enabled=%s connected=%s eligible=%s interval=%s"
    )
    _decision_args = (
        kobo_decision["status"], kobo_decision["persisted_enabled"],
        kobo_decision["connected_kobo"], kobo_decision["eligible_kobo"],
        kobo_decision["interval_minutes"],
    )
    _decision_severity = (
        logging.ERROR if kobo_decision["status"] == "error"
        else logging.WARNING if kobo_decision["status"] == "partial"
        else logging.INFO
    )  # Never discard a failed opening in errors-only mode.
    logger.log(
        _decision_severity, _decision_format, *_decision_args,
        extra=_core_journal_record(
            "KOBO", "automatic backup decision",
            "warning" if kobo_decision["status"] in {"partial", "error"} else "ok",
            _decision_format % _decision_args, kobo_decision,
        ),
    )
    # The no-item case has no backup rows or checkpoint. Persist only a
    # minimal, enumerated decision so kobo-sync-errors can explain *why* on a
    # fresh CLI process, without relying on volatile Diagnostics history.
    try:
        self.storage.metadata_set("kobo_backup_opening_decision_v1", json.dumps({
            "schema_version": 1,
            "status": kobo_decision["status"],
            "persisted_enabled": kobo_decision["persisted_enabled"] is True,
            "connected_kobo": kobo_decision["connected_kobo"],
            "eligible_kobo": kobo_decision["eligible_kobo"],
            "scheduled_kobo": kobo_decision["scheduled_kobo"],
            "checked_at": int(time.time()),
        }, sort_keys=True))
    except Exception:
        # Optional persistence must neither fail the opening nor reveal a
        # filesystem, plugin or database exception class in TRACE.
        logger.warning("TRACE kobo backup decision persistence error code=decision_save_failed")
    self.diagnostics.add_core_kobo_backup_decision(kobo_decision)
    for identity in self.identities:
        emit("adept_inventory", identity_id=identity.identity_id, result=self._reconcile_identity_adept_inventory(identity.identity_id))
    # The opening inventory covers ALL local identities to keep their rights
    # current, but last_loans is consumed directly by the ACTIVE profile's
    # Loans GTK page. Never publish the global multi-identity union there.
    # Selecting another profile during a long opening sync is handled by
    # reading current_identity at publication, not at task submission.
    with self._search_generation_lock:
        visible_identity = getattr(self.current_identity, "identity_id", None)
        self.last_loans = sorted(
            (row for row in all_loans
             if type(row) is Loan and row.identity_id == visible_identity
             and visible_identity is not None),
            key=lambda row: (str(row.due_at or "9999"), row.title.casefold()),
        )
    if deep:
        catalog = self.catalog_scan(repair_misnamed=True)
        self.storage.metadata_set("catalog_deep_last_at", int(time.time()))
        sync_mode = "deep"
    else:
        catalog = {"mode": "fast", "filesystem_scan": "skipped", "entries": len(self.storage.catalog_entries())}
        sync_mode = "fast"
    return {
        "started_at": started, "finished_at": int(time.time()), "recovered_interrupted": recovered,
        "expired_realized": expired_before, "identities": summaries, "kobo": kobo_summary, "kobo_decision": kobo_decision, "catalog": catalog,
        "sync_mode": sync_mode, "acsm_cleanup": acsm_cleanup,
        "catalog_mirror": catalog_mirror,
    }


def _record_action(self, identity_id: str | None, action_type: str, outcome: str, *, provider_id: str='', plugin_id: str='', facts: Mapping[str, Any] | None=None) -> None:
    try:
        self.storage.action_event_put(identity_id=identity_id, action_type=action_type, outcome=outcome, provider_id=provider_id, plugin_id=plugin_id, facts=facts)
    except Exception:
        # Never reflect an action label or a SQLite exception from an
        # optional statistic into the shareable diagnostic feed.
        self.diagnostics.add("STATISTICS", "action event", "error", detail={"code": "action_event_failed"})


def _record_loan_statistics(self, account, rows, *, from_cache=False):
    key='loan_statistics:'+str(account.identity_id)
    source=hashlib.sha256(f'{account.provider_id}:{account.credential_id}:{account.unique_id}'.encode()).hexdigest()
    value={'loan_keys':sorted({hashlib.sha256(f'{source}:{row.id}'.encode()).hexdigest() for row in rows}),
           'observed_at':None if from_cache else int(time.time()),'origin':'cache' if from_cache else 'provider'}
    self.storage.metadata_json_put_item(key,source,value,only_if_missing=from_cache)
