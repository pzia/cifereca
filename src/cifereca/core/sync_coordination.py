"""Application synchronization coordinator and explicit event recording.

Facade-owned sessions, acquisition helpers, locks and storage remain unique;
no widgets or additional remote calls are introduced."""
from __future__ import annotations

import time
import hashlib
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
        rows = session.library()
        if account.provider_id == "kobo":
            # Persist attested personal rights, but never start Kobo acquisition.
            from .kobo_manifest_rights import mirror_kobo_manifest_rights
            result["owned_rights"] = mirror_kobo_manifest_rights(
                self, rows, identity_id=account.identity_id,
                credential_id=account.credential_id,
                account_id=account.unique_id or None,
            )
        result["library"] = self._mirror_remote_results(rows, identity_id=account.identity_id)
        if account.provider_id == "numilog" and session.capabilities.direct_download:
            acquired = 0
            for row in rows:
                if control is not None and callable(getattr(control, "check_cancelled", None)):
                    control.check_cancelled()
                outcome = self._acquire_personal_library_item(session, row, account.identity_id)
                if outcome.get("status") == "complete":
                    acquired += 1
            result["acquired"] = acquired
    if deep:
        result["catalog_scan"] = self.catalog_scan()
    return result


def synchronize_account_source(self, account: Account, *, deep: bool=False, control: Any=None) -> dict[str, Any]:
    try:
        result = self._synchronize_account_source_impl(account, deep=deep, control=control)
        self._record_action(account.identity_id, "synchronization", "success", provider_id=account.provider_id, facts={"deep": bool(deep)})
        return result
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

    try:
        mirror = self.mirror_personal_libraries()
        emit("catalog_mirror", result=mirror)
    except Exception as exc:
        logger.warning("TRACE catalog mirror opening error=%s", exc)

    # Kobo owned sync is one global batch because the existing engine already
    # serializes and reconciles all connected Vault sessions.
    kobo_summary: dict[str, Any] | None = None
    if self.kobo_backup_enabled() and any(
        s.connected and s.provider_id == "kobo" and s.capabilities.backup
        and any(a.provider_id == "kobo" and a.credential_id == s.credential_id and self.sync_source_enabled(a) for a in self.accounts)
        for s in self._sessions.values()
    ):
        if deep or self._kobo_backup_due():
            try:
                kobo_summary = self._sync_kobo_backup_locked(progress=progress)
            except Exception as exc:
                kobo_summary = {"errors": 1, "error": str(exc)}
        else:
            kobo_summary = {"errors": 0, "status": "fresh_cache", "skipped_fresh": True}
            logger.info("TRACE kobo sync skipped reason=fresh-cache interval_minutes=%s", self.config.get("kobo_backup.interval_minutes", 60))
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
        "expired_realized": expired_before, "identities": summaries, "kobo": kobo_summary, "catalog": catalog,
        "sync_mode": sync_mode, "acsm_cleanup": acsm_cleanup,
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
