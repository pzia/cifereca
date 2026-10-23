"""Application synchronization coordinator and explicit event recording.

Facade-owned sessions, acquisition helpers, locks and storage remain unique;
no widgets or additional remote calls are introduced."""
from __future__ import annotations

import time
import hashlib
import json
from contextlib import ExitStack
from .source_sync_timing import measure, timing_scope
from datetime import datetime, timezone
from typing import Any, Callable
from ..diagnostics import redact
from ..locking import InterProcessLock, LockBusyError
from ..paths import acquisition_lock_file
from ..models import Account, Loan
from ..providers import ProviderConfigurationError


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
            self._account_context_epoch,
        )
        with measure("loan_refresh_ms"):
            loans = self._loans_for_account(account, force=True)
        if not _opening_loans_scoped(account, source_scope[0], loans):
            raise ProviderConfigurationError("loan_source_scope_mismatch")
        matching = [
            row for row in self.accounts
            if type(row) is Account
            and (row.identity_id, row.provider_id,
                 row.credential_id, row.unique_id) == source_scope[:4]
        ]
        if (len(matching) != 1 or matching[0] is not account
                or self._account_context_epoch != source_scope[4]
                or self.identity_by_id(account.identity_id) is None
                or self._sessions.get(self._session_key(
                    account.provider_id, account.credential_id,
                )) is not session
                or session.connected is not True
                or not self.sync_source_enabled(account)):
            raise ProviderConfigurationError("loan_source_scope_changed")
        # An explicit source refresh has the SAME positive-loan semantics as
        # opening sync. Never infer absence, close loans missing from an
        # empty/partial list, expire another account's loans or acquire EPUBs.
        if loans:
            from . import library_mutation
            with measure("rights_mirror_ms"):
                library_mutation._persist_active_loans(
                    self, account.identity_id, loans,
                    reconcile_missing=False, realize_expired=False,
                )
        result["loans"] = len(loans)
        # Use the same provider partial-inventory signal as opening sync.
        # BnF HTML observations are deliberately non-exhaustive even when
        # every request succeeded. Preserve Paris' legacy fail-closed default
        # when its independent upstream error inventory is missing.
        errors = getattr(session, "last_loan_errors",
                         None if account.provider_id == "paris" else ())
        if type(errors) not in (list, tuple) or errors:
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
                locks.enter_context(InterProcessLock(acquisition_lock_file()))
            with measure("locked_work_ms"):
                result = self._synchronize_account_source_impl(account, deep=deep, control=control)
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
        observe("completed", "cancelled" if isinstance(exc, Cancelled) else "failed",
                "source_sync_cancelled" if isinstance(exc, Cancelled) else "source_sync_failed")
        # A provider can use a dynamically named exception whose class name
        # contains a borrower, signed URL or local filesystem path.
        self._record_action(account.identity_id, "synchronization", "failed", provider_id=account.provider_id, facts={"deep": bool(deep), "error": "source_sync_failed"})
        raise


def opening_sync(self, progress: Callable[[dict[str, Any]], None] | None=None, *, deep: bool=False, lock_factory, lock_path) -> dict[str, Any]:
    """Synchronize remote state; filesystem reconciliation is opt-in/deep."""
    lock = lock_factory(lock_path())
    try:
        with lock:
            result = self._opening_sync_locked(progress=progress, deep=deep)
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


def _opening_loan_acquisition_allowed(loan: Loan) -> bool:
    """BnF Web loans are NEVER startup download consent (#2055).

    A positive provider observation may be persisted as a scoped loan by the
    owning Core contract, but browsing or opening Cifereca cannot trigger its
    Web download/ACSM fulfillment. That needs a separate explicit user action.
    Keep existing automatic acquisition decisions for other providers intact.
    """
    return (type(loan) is Loan and loan.source_provider != "bnf-pret"
            and loan.medium == "digital" and bool(loan.ebook_url or loan.raw))


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
    expected_account = account.unique_id or ("primary" if provider == "paris" else "")
    if type(expected_account) is not str:
        return False
    return all(
        type(loan) is Loan
        and type(loan.id) is str and bool(loan.id)
        and type(loan.source_provider) is str and loan.source_provider == provider
        and type(loan.source_credential) is str and loan.source_credential == credential
        and type(loan.source_account_id) is str and loan.source_account_id == expected_account
        and type(loan.identity_id) is str and loan.identity_id == identity_id
        and type(loan.medium) is str and loan.medium in ("digital", "physical", "unknown")
        for loan in rows
    )


def _opening_sync_locked(self, progress: Callable[[dict[str, Any]], None] | None=None, *, deep: bool=False, logger) -> dict[str, Any]:
    from ..brokers import SynchronizationBrokerImpl
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
            seen_loans: set[tuple[str, str, str, str]] = set()
            provider_errors: list[dict[str, str]] = []
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
                    account_loans = self._loans_for_account(account, force=True)
                    logger.info(
                        "TRACE loans sync end count=%s dt=%.3fs",
                        len(account_loans), time.monotonic() - loan_started,
                    )
                    if not _opening_loans_scoped(account, identity.identity_id, account_loans):
                        # Do not persist any row from an ambiguous account batch;
                        # avoid displaying or logging provider-controlled details.
                        raise ProviderConfigurationError("loan_source_scope_mismatch")
                    session = self._sessions.get(self._session_key(account.provider_id, account.credential_id))
                    partial_errors = list(getattr(session, "last_loan_errors", []) or []) if session is not None else []
                    if partial_errors:
                        errors += len(partial_errors)
                        # Provider partial errors are untrusted data (potentially
                        # a signed URL, account ID or HTTP body). Persist only
                        # closed event codes, preserving the exact failure count.
                        provider_errors.extend(
                            {"code": "loan_partial_failure"}
                            for _ in partial_errors
                        )
                        logger.warning(
                            "TRACE loans sync partial code=loan_partial_failure count=%s",
                            len(partial_errors),
                        )
                    source_outcomes[source_id] = (
                        "partial" if partial_errors else "success"
                    )
                    for loan in account_loans:
                        key = (loan.source_provider, loan.source_credential, str(loan.source_account_id or ""), loan.id)
                        if key not in seen_loans:
                            seen_loans.add(key); loans.append(loan)
                except Exception:
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
            # No current supplier offers a Core-attested exhaustive per-account
            # negative listing contract (including pagination and row validity).
            # Preserve old active rights until a specific complete scope is
            # explicitly proven; separately proven expiry remains effective.
            lifecycle = self._persist_active_loans(identity.identity_id, loans)
            all_loans.extend(loans)
            detail["realized"] = lifecycle["returned_realized"] + lifecycle["expired_realized"]
            for loan in loans:
                if not _opening_loan_acquisition_allowed(loan):
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
                    issue = redact({"provider": loan.source_provider, "loan_id": loan.id,
                                    "title": loan.title, "status": result["status"],
                                    "code": result.get("code"), "error": result.get("error"),
                                    "process": result.get("process", {})})
                    detail.setdefault("acquisition_errors", []).append(issue)
                    self.diagnostics.add("ACQUISITION", "loan sync", "error" if result["status"] == "retryable_error" else "warning", detail=issue, level="errors")
            if errors or attention:
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
        # The outer opening-sync already owns the cross-process lock.
        # The scoped helper is the ONLY automatic materialization path.
        due = [
            session for session in eligible_kobo
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
            "attention_required", "remaining", "manifest_items",
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
            for session in eligible_kobo
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
        elif counters["errors"] or {"aborted", "cancelled", "failed", "partial", "interrupted"} & states:
            batch_status = "partial"
        elif "attention_required" in states:
            batch_status = "attention_required"
        elif len(sources) < len(eligible_kobo):
            batch_status = "partial"
        else:
            batch_status = "complete"
        if not due:
            batch_status = (
                "fresh_cache" if batch_status == "complete"
                else "fresh_cache_partial"
            )
        kobo_summary = {
            **counters, "scope": "accounts", "status": batch_status,
            "sessions": len(eligible_kobo), "synced_accounts": len(due),
            "cached_accounts": len(eligible_kobo) - len(due),
            "sources": sources, "skipped_fresh": not bool(due),
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
    logger.info(
        "TRACE kobo backup decision status=%s enabled=%s connected=%s eligible=%s interval=%s",
        kobo_decision["status"], kobo_decision["persisted_enabled"],
        kobo_decision["connected_kobo"], kobo_decision["eligible_kobo"],
        kobo_decision["interval_minutes"],
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
    self.last_loans = sorted(all_loans, key=lambda row: (str(row.due_at or "9999"), row.title.casefold()))
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
