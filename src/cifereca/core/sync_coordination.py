"""Application synchronization coordinator and explicit event recording.

Facade-owned sessions, acquisition helpers, locks and storage remain unique;
no widgets or additional remote calls are introduced."""
from __future__ import annotations

import time
import hashlib
import json
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
    session = self._session_for_account(account)
    if not session.connected:
        raise ProviderConfigurationError(f"Provider non connecté: {account.provider_id}")
    if control is not None and callable(getattr(control, "check_cancelled", None)):
        control.check_cancelled()
    if session.capabilities.loans:
        loans = self._loans_for_account(account, force=True)
        result["loans"] = len(loans)
    if session.capabilities.personal_library:
        # A provider can implement a one-shot iterable: both the entitlement
        # mirror and remote catalog must see the exact same authenticated rows.
        rows = list(session.library())
        if account.provider_id == "kobo":
            # Persist attested personal rights, but never start Kobo acquisition.
            from .kobo_manifest_rights import mirror_kobo_manifest_rights
            result["owned_rights"] = mirror_kobo_manifest_rights(
                self, rows, identity_id=account.identity_id,
                credential_id=account.credential_id,
                account_id=account.unique_id or None,
            )
        # A source-level sync is an inventory/rights refresh, not a request
        # to materialize local files. In particular, Numilog may advertise
        # direct_download but that capability must not implicitly acquire
        # every returned EPUB from the generic Sync broker (#1346).
        # The separate legacy opening/acquisition workflow remains unchanged.
        result["library"] = self._mirror_remote_results(rows, identity_id=account.identity_id)
    if deep:
        result["catalog_scan"] = self.catalog_scan()
    return result


def synchronize_account_source(self, account: Account, *, deep: bool=False, control: Any=None) -> dict[str, Any]:
    try:
        # Source synchronization is also a library writer. Serialize it
        # with maintenance destruction and opening/Kobo acquisition across
        # processes; never wait for the lock on a caller/UI thread.
        with InterProcessLock(acquisition_lock_file()):
            result = self._synchronize_account_source_impl(account, deep=deep, control=control)
        self._record_action(account.identity_id, "synchronization", "success", provider_id=account.provider_id, facts={"deep": bool(deep)})
        return result
    except LockBusyError as exc:
        self._record_action(account.identity_id, "synchronization", "failed", provider_id=account.provider_id, facts={"deep": bool(deep), "error": "operation_busy"})
        raise ProviderConfigurationError("Synchronisation ou maintenance de la bibliothèque déjà en cours") from exc
    except Exception as exc:
        self._record_action(account.identity_id, "synchronization", "failed", provider_id=account.provider_id, facts={"deep": bool(deep), "error": type(exc).__name__})
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
    """Passive, safe reason for a Kobo auto-backup decision.

    No provider calls: inspect only persisted flag, cached session capabilities,
    configured account switches, and the existing cache freshness marker.
    Never expose identity, credential, account, product, title, URL or path.
    """
    enabled = bool(self.kobo_backup_enabled())
    kobo = [s for s in self._sessions.values() if s.provider_id == "kobo"]
    connected = [s for s in kobo if s.connected]
    capable = [s for s in connected if getattr(s.capabilities, "backup", False)]
    # Credentials can be shared by several distinct accounts/identities.
    # A session is eligible only when a configured account is enabled and
    # belongs to that exact provider/credential pair. Count each account
    # once, not every cached session or an unrelated provider's switch.
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
    if not enabled:
        reason = "disabled"
    elif not connected:
        reason = "no_session"
    elif not capable:
        reason = "capability_unavailable"
    elif not eligible:
        reason = "source_disabled"
    elif deep or self._kobo_backup_due():
        reason = "started"
    else:
        reason = "fresh_cache"
    # Age alone communicates freshness; avoid exposing a user-specific
    # account identifier, timestamp string or private storage metadata.
    raw_last = self.storage.metadata_get("kobo_backup_last_success_at")
    try:
        last = int(raw_last)
        age = int(time.time()) - last
        last_success_age_minutes = age // 60 if 0 <= age <= 366 * 86400 else None
    except (TypeError, ValueError, OverflowError):
        last_success_age_minutes = None
    return {
        "status": reason, "persisted_enabled": enabled,
        "configured_kobo": len(kobo), "connected_kobo": len(connected),
        "eligible_kobo": len(eligible),
        "eligible_kobo_bindings": len(eligible_accounts),
        "interval_minutes": self.kobo_backup_interval_minutes(),
        "last_success_age_minutes": last_success_age_minutes,
        "deep": bool(deep),
    }


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
    except Exception as exc:
        self.diagnostics.add("ADEPT", "profile list for dashboard", "error", detail={"error": type(exc).__name__, "message": str(exc)})
    for identity in self.identities:
        run_id = self.storage.sync_run_put({
            "identity_id": identity.identity_id, "scope": "opening", "status": "running", "started_at": started,
            "detail": {"label": identity.label},
        })
        emit("identity_begin", identity_id=identity.identity_id, label=identity.label)
        errors = 0; new_acquisitions = 0; attention = 0
        detail: dict[str, Any] = {"label": identity.label, "providers": [], "loans": 0, "realized": 0}
        bound_profile = str(self.storage.adept_profile_binding_get("identity", identity.identity_id) or "").strip()
        if bound_profile:
            profile = next((p for p in adept_profiles if str(p.get("profile_id") or "") == bound_profile or str(p.get("name") or "") == bound_profile), None)
            detail["adept_profile_id"] = str((profile or {}).get("profile_id") or bound_profile)
            detail["adept_profile_name"] = str((profile or {}).get("name") or bound_profile)
            detail["adept_provisioning"] = dict((profile or {}).get("provisioning") or {})
            if adept_profiles and profile is None:
                detail["adept_attention"] = "profile_missing"
        accounts: list[Account] = []
        try:
            loans: list[Loan] = []
            seen_loans: set[tuple[str, str, str, str]] = set()
            provider_errors: list[dict[str, str]] = []
            accounts = sorted(
                [a for a in self.accounts_for_identity(identity.identity_id) if self.sync_source_enabled(a)],
                key=lambda a: (0 if a.provider_id == "paris" else 1, a.provider_id, a.credential_id, a.unique_id),
            )
            for account in accounts:
                provider = account.provider_id
                if provider not in detail["providers"]:
                    detail["providers"].append(provider)
                try:
                    loan_started = time.monotonic()
                    logger.info(
                        "TRACE loans sync begin identity=%s provider=%s credential=%s account=%s",
                        identity.identity_id, provider, account.credential_id, account.unique_id or "primary",
                    )
                    account_loans = self._loans_for_account(account, force=True)
                    logger.info(
                        "TRACE loans sync end identity=%s provider=%s credential=%s account=%s count=%s dt=%.3fs",
                        identity.identity_id, provider, account.credential_id, account.unique_id or "primary",
                        len(account_loans), time.monotonic() - loan_started,
                    )
                    session = self._sessions.get(self._session_key(account.provider_id, account.credential_id))
                    partial_errors = list(getattr(session, "last_loan_errors", []) or []) if session is not None else []
                    if partial_errors:
                        errors += len(partial_errors)
                        for part in partial_errors:
                            provider_errors.append({
                                "provider": provider, "credential_id": account.credential_id,
                                "account_id": account.unique_id,
                                "upstream": str(part.get("upstream") or ""),
                                "error": str(part.get("error") or ""),
                            })
                        logger.warning(
                            "TRACE loans sync partial identity=%s provider=%s credential=%s account=%s errors=%s",
                            identity.identity_id, provider, account.credential_id, account.unique_id or "primary", partial_errors,
                        )
                    for loan in account_loans:
                        key = (loan.source_provider, loan.source_credential, str(loan.source_account_id or ""), loan.id)
                        if key not in seen_loans:
                            seen_loans.add(key); loans.append(loan)
                except Exception as exc:
                    errors += 1
                    provider_errors.append({"provider": provider, "credential_id": account.credential_id, "account_id": account.unique_id, "error": str(exc)})
                    logger.exception(
                        "TRACE loans sync error identity=%s provider=%s credential=%s account=%s",
                        identity.identity_id, provider, account.credential_id, account.unique_id or "primary",
                    )
            loans.sort(key=lambda row: (str(row.due_at or "9999"), row.title.casefold()))
            all_loans.extend(loans)
            detail["loans"] = len(loans)
            if provider_errors:
                detail["provider_errors"] = provider_errors
            lifecycle = self._persist_active_loans(identity.identity_id, loans)
            detail["realized"] = lifecycle["returned_realized"] + lifecycle["expired_realized"]
            for loan in loans:
                if loan.medium != "digital" or not (loan.ebook_url or loan.raw):
                    continue
                result = self._acquire_active_loan_automatically(loan)
                if result["status"] == "complete":
                    new_acquisitions += 1
                    self._record_action(identity.identity_id, "acquisition", "success", provider_id=loan.source_provider, facts={"origin": "opening_sync", "loan_id": loan.id})
                elif result["status"] == "attention_required":
                    attention += 1
                    self._record_action(identity.identity_id, "acquisition", "failed", provider_id=loan.source_provider, facts={"origin": "opening_sync", "loan_id": loan.id, "status": result["status"]})
                elif result["status"] == "retryable_error":
                    errors += 1
                    self._record_action(identity.identity_id, "acquisition", "failed", provider_id=loan.source_provider, facts={"origin": "opening_sync", "loan_id": loan.id, "status": result["status"]})
                if result["status"] in {"retryable_error", "attention_required"}:
                    issue = redact({"provider": loan.source_provider, "loan_id": loan.id,
                                    "title": loan.title, "status": result["status"],
                                    "code": result.get("code"), "error": result.get("error"),
                                    "process": result.get("process", {})})
                    detail.setdefault("acquisition_errors", []).append(issue)
                    self.diagnostics.add("ACQUISITION", "loan sync", "error" if result["status"] == "retryable_error" else "warning", detail=issue, level="errors")
            if errors or attention:
                status = "partial" if loans or len(accounts) > len(provider_errors) else "failed"
            else:
                status = "success"
        except Exception as exc:
            errors += 1; status = "failed"; detail["error"] = str(exc)
        finished = int(time.time())
        self.storage.sync_run_put({
            "sync_run_id": run_id, "identity_id": identity.identity_id, "scope": "opening", "status": status,
            "started_at": started, "finished_at": finished, "new_acquisitions": new_acquisitions,
            "attention_required": attention, "errors": errors, "detail": detail,
        })
        summary = {"identity_id": identity.identity_id, "label": identity.label, "status": status,
                   "new_acquisitions": new_acquisitions, "attention_required": attention, "errors": errors, **detail}
        summaries.append(summary)
        # Opening sync previously wrote only sync_runs, while GTK reads
        # sync.source.state.*. Project the *confirmed* identity result into
        # each enabled account, without inventing a success on partial ADEPT.
        attempted_at = datetime.fromtimestamp(started, timezone.utc).isoformat(timespec="seconds")
        for account in accounts:
            try:
                source_status.record_opening_state(
                    self.sync_source_id(account), identity_id=identity.identity_id,
                    status=status, attempted_at=attempted_at,
                )
            except Exception as sync_state_exc:
                logger.warning(
                    "TRACE sync source projection failed provider=%s reason=%s",
                    account.provider_id, type(sync_state_exc).__name__,
                )
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
    except Exception as exc:
        # A provider exception may contain a signed ACSM URL or bearer cookie.
        # The error type and phase are sufficient to explain the failure.
        catalog_mirror["status"] = "failed"
        logger.warning("TRACE catalog mirror opening error type=%s",
                       type(exc).__name__)
    emit("catalog_mirror", result=catalog_mirror)

    # Kobo owned sync is one global batch because the existing engine already
    # serializes and reconciles all connected Vault sessions.
    # Record exactly one sanitized decision per opening, even when skipped.
    kobo_summary: dict[str, Any] | None = None
    kobo_decision = _kobo_opening_decision(self, deep=deep)
    # Once a v1 per-account checkpoint exists, freshness is isolated per
    # source. Do not create a competing checkpoint store.
    eligible_kobo = [
        session for session in self._sessions.values()
        if session.connected and session.provider_id == "kobo"
        and getattr(session.capabilities, "backup", False)
        and any(account.provider_id == "kobo"
                and account.credential_id == session.credential_id
                and self.sync_source_enabled(account)
                for account in self.accounts)
    ]
    from .kobo_backup import _kobo_checkpoint_for, _kobo_checkpoint_key
    have_per_account_checkpoint = any(
        _kobo_checkpoint_for(
            self, self.identity_for_connection("kobo", session.credential_id).identity_id,
            session.credential_id,
        ) is not None
        for session in eligible_kobo
    )
    if (not deep and have_per_account_checkpoint and
            kobo_decision["status"] in {"started", "fresh_cache"}):
        # Opening-sync already holds the global acquisition lock. Calling the
        # internal scoped helper must NOT attempt to acquire it a second time.
        due = [
            session for session in eligible_kobo
            if self._kobo_backup_account_due(session.credential_id)
        ]
        executed: list[dict[str, Any]] = []
        for session in due:
            try:
                executed.append(
                    self._sync_kobo_backup_account_locked(
                        session.credential_id, progress=progress,
                    )
                )
            except Exception as exc:
                logger.warning(
                    "TRACE kobo account sync error kind=%s", type(exc).__name__,
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
        cached_keys = set()
        for session in eligible_kobo:
            if session.credential_id not in due_credentials:
                identity_id = self.identity_for_connection(
                    "kobo", session.credential_id,
                ).identity_id
                cached_keys.add(
                    _kobo_checkpoint_key(identity_id, session.credential_id).rsplit(".", 1)[-1]
                )
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
        if counters["errors"] or {"aborted", "cancelled", "failed", "partial", "interrupted"} & states:
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
            "partial" if batch_status in {"partial", "attention_required", "fresh_cache_partial", "cancelled"}
            else "fresh_cache" if batch_status == "fresh_cache" else "started"
        )
    elif kobo_decision["status"] == "started":
        try:
            kobo_summary = self._sync_kobo_backup_locked(progress=progress)
            if (kobo_summary or {}).get("status") == "error":
                kobo_decision["status"] = "error"
            elif (kobo_summary or {}).get("status") in {
                "partial", "attention_required", "aborted", "failed", "cancelled"
            } or int((kobo_summary or {}).get("errors", 0) or 0) > 0:
                kobo_decision["status"] = "partial"
        except Exception:
            kobo_summary = {"errors": 1, "status": "error"}
            kobo_decision["status"] = "error"
    elif kobo_decision["status"] == "fresh_cache":
        previous_batch = str(
            self.storage.metadata_get("kobo_backup_last_batch_status") or ""
        )
        kobo_summary = {
            "errors": 0, "status": "fresh_cache", "skipped_fresh": True
        }
        if previous_batch and previous_batch != "complete":
            kobo_summary.update(
                status="fresh_cache_partial", last_batch_status=previous_batch
            )
            kobo_decision["status"] = "partial"
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
            "checked_at": int(time.time()),
        }, sort_keys=True))
    except Exception as exc:
        # A diagnostic breadcrumb must never prevent loans/sources syncing.
        logger.warning("TRACE kobo backup decision persistence error kind=%s",
                       type(exc).__name__)
    self.diagnostics.add(
        "KOBO", "automatic backup decision",
        "warning" if kobo_decision["status"] in ("partial", "error") else "ok",
        detail=kobo_decision, level="normal",
    )
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
    except Exception as exc:
        self.diagnostics.add("STATISTICS", "action event", "error", detail={"action": action_type, "error": type(exc).__name__})


def _record_loan_statistics(self, account, rows, *, from_cache=False):
    key='loan_statistics:'+str(account.identity_id)
    source=hashlib.sha256(f'{account.provider_id}:{account.credential_id}:{account.unique_id}'.encode()).hexdigest()
    value={'loan_keys':sorted({hashlib.sha256(f'{source}:{row.id}'.encode()).hexdigest() for row in rows}),
           'observed_at':None if from_cache else int(time.time()),'origin':'cache' if from_cache else 'provider'}
    self.storage.metadata_json_put_item(key,source,value,only_if_missing=from_cache)
