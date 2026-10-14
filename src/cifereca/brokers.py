from __future__ import annotations
import ast
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from cifereca.plugin_api import AccountRef, AdeptProfileRef, AttentionRequired, HealthCheckResult, RuntimeAcquisitionCapabilities, SyncRequest, SyncResult, SyncSourceRef
from .artifact_runtime import ArtifactExecutionBrokerImpl, LibraryReadBrokerImpl, LibraryMaintenanceBrokerImpl, ArtifactRoutingBrokerImpl
from .cache_runtime import CacheBrokerImpl
from .task_runtime import TaskBrokerImpl

_SYNC_LOG = logging.getLogger("cifereca.sync")


def _account_id(account: Any) -> str:
    unique = str(getattr(account, "unique_id", "") or "").strip()
    if unique:
        return unique
    provider = str(getattr(account, "provider_id", "") or "provider")
    credential = str(getattr(account, "credential_id", "") or "default")
    return f"{provider}:{credential}:primary"


def _account_ref(service: Any, account: Any) -> AccountRef:
    provider = str(getattr(account, "provider_id", "") or "")
    credential = str(getattr(account, "credential_id", "") or "")
    session = getattr(service, "_sessions", {}).get(getattr(service, "_session_key")(provider, credential)) if provider else None
    capabilities: list[str] = []
    caps = getattr(session, "capabilities", None)
    if caps is not None:
        for name in ("catalog", "loans", "history", "bookings", "direct_download", "personal_library"):
            if bool(getattr(caps, name, False)):
                capabilities.append(name)
    return AccountRef(
        account_id=_account_id(account),
        identity_id=str(getattr(account, "identity_id", "") or ""),
        provider_id=provider,
        label=str(getattr(account, "display_name", "") or getattr(account, "provider_label", "") or provider),
        connected=bool(session is not None and getattr(session, "connected", False)),
        capabilities=tuple(capabilities),
    )


@dataclass(slots=True)
class CatalogBrokerImpl:
    service: Any
    def search(self, query: str, **options: Any): return self.service.search(query, **options)
    def search_progressive(self, query: str, *, on_batch: Callable[[Any], None], control: Any = None, **options: Any):
        return self.service.search_progressive(query, on_batch=on_batch, control=control, **options)
    def holdings(self, item: Any, **options: Any): return self.service.holdings(item, **options)
    def searchable_providers(self): return self.service.searchable_providers()
    def catalog_entries(self, **options: Any): return self.service.catalog_entries(**options)


@dataclass(slots=True)
class AcquisitionBrokerImpl:
    service: Any

    def can_download(self, item: Any) -> bool:
        return bool(self.service.can_direct_download(item))

    def eligible_accounts(self, item: Any, *, identity_id: str | None = None) -> tuple[AccountRef, ...]:
        provider = str(getattr(item, "source_provider", "") or "")
        source_credential = str(getattr(item, "source_credential", "") or "")
        rows = []
        for account in self.service.accounts_for_identity(identity_id):
            if provider and str(getattr(account, "provider_id", "")) != provider:
                continue
            # When the source is already bound to one credential, only that
            # credential is eligible.  Search results without a credential may
            # legitimately require an explicit account choice.
            if source_credential and str(getattr(account, "credential_id", "")) != source_credential:
                continue
            ref = _account_ref(self.service, account)
            if ref.connected:
                rows.append(ref)
        return tuple(rows)

    def acquire(self, item: Any, *, identity_id: str | None = None, account_id: str | None = None):
        eligible = self.eligible_accounts(item, identity_id=identity_id)
        if account_id:
            chosen = next((row for row in eligible if row.account_id == account_id), None)
            if chosen is None:
                raise ValueError(f"account not eligible: {account_id}")
        elif len(eligible) == 1:
            chosen = eligible[0]
        elif not eligible:
            raise ValueError("no eligible connected account")
        else:
            raise ValueError("account selection required")
        # Preserve the source object but bind the selected credential when the
        # model supports mutable dataclass attributes (SearchResult does).
        internal = next((account for account in self.service.accounts_for_identity(chosen.identity_id)
                         if _account_id(account) == chosen.account_id and str(getattr(account, "provider_id", "")) == chosen.provider_id), None)
        if internal is None:
            raise ValueError(f"account disappeared: {chosen.account_id}")
        try:
            setattr(item, "source_credential", str(getattr(internal, "credential_id", "") or ""))
        except Exception:
            pass
        return self.service.acquire_direct_item(item)

    def import_acsm(self, path: Any, **options: Any):
        return self.service.import_acsm(path, **options)


@dataclass(slots=True)
class SynchronizationBrokerImpl:
    service: Any
    _subscribers: list[Callable[[], None]] | None = None

    def __post_init__(self) -> None:
        if self._subscribers is None:
            self._subscribers = []

    @staticmethod
    def _source_id(account: Any) -> str:
        raw = "\0".join((
            str(getattr(account, "identity_id", "") or ""),
            str(getattr(account, "provider_id", "") or ""),
            str(getattr(account, "credential_id", "") or ""),
            _account_id(account),
        ))
        return "sync:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def _preference_key(self, source_id: str) -> str:
        return "sync.source.enabled." + source_id

    def _enabled(self, source_id: str) -> bool:
        raw = self.service.storage.metadata_get(self._preference_key(source_id))
        if raw is None:
            return True
        return str(raw).strip().lower() not in {"0", "false", "no", "off"}

    def _state(self, source_id: str) -> dict[str, Any]:
        key = "sync.source.state." + source_id
        raw = self.service.storage.metadata_get(key)
        if not raw:
            return {}
        # Metadata is untrusted persisted text. Older versions accidentally
        # stored str(dict), so accept only bounded Python *literals* as a
        # one-time compatibility migration; never call eval().
        if not isinstance(raw, str) or len(raw) > 8192:
            _SYNC_LOG.warning("invalid sync source metadata size for %s", source_id)
            return {}
        legacy = False
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            try:
                value = ast.literal_eval(raw)
                legacy = True
            except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
                _SYNC_LOG.warning("invalid sync source metadata for %s", source_id)
                return {}
        if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
            _SYNC_LOG.warning("invalid sync source state shape for %s", source_id)
            return {}
        state = {
            key: value[key] for key in (
                "last_attempt", "last_success", "status", "error", "scope", "identity_id",
            ) if key in value and isinstance(value[key], str)
        }
        if legacy:
            self._set_state(source_id, state)
        return state

    def _set_state(self, source_id: str, value: dict[str, Any]) -> None:
        self.service.storage.metadata_set(
            "sync.source.state." + source_id,
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )

    def record_opening_state(self, source_id: str, *, identity_id: str,
                             status: str, attempted_at: str) -> None:
        """Project the opening sync into the same per-source state read by GTK.

        An identity-level partial result is not proof that a source succeeded:
        conservatively report partial for every involved source. Keep any
        previously confirmed success, including after failed opening sync.
        """
        state = self._state(source_id)
        if state.get("last_attempt", "") > attempted_at:
            return  # a more recent manual sync should not be overwritten
        state.update(last_attempt=attempted_at, status=status, error="",
                     scope="opening", identity_id=identity_id)
        if status == "success":
            state["last_success"] = attempted_at
        elif status in {"partial", "failed"}:
            state["error"] = ("Ouverture partielle" if status == "partial"
                              else "Échec de synchronisation au démarrage")
        self._set_state(source_id, state)
        self._notify()

    def _find_account(self, source_id: str) -> Any:
        for account in getattr(self.service, "accounts", ()) or ():
            if self._source_id(account) == source_id:
                return account
        raise ValueError(f"unknown sync source: {source_id}")

    def _ref(self, account: Any) -> SyncSourceRef:
        source_id = self._source_id(account)
        account_ref = _account_ref(self.service, account)
        state = self._state(source_id)
        source_caps = set(account_ref.capabilities)
        sync_capable = bool(source_caps & {"loans", "personal_library"})
        declared_sync_caps = []
        if "personal_library" in source_caps:
            declared_sync_caps.append("remote_inventory")
        if source_caps & {"loans", "personal_library"}:
            declared_sync_caps.append("entitlements")
        # The service owns this session. Kobo's explicit backup capability
        # alone licenses declaring the *availability* of an opt-in local
        # materialization phase; this metadata read never executes it.
        # Do NOT announce scheduled_refresh: the current Kobo timer does
        # not yet implement periodic source refresh (#1346).
        provider_id = account_ref.provider_id
        credential_id = str(getattr(account, "credential_id", "") or "")
        session = getattr(self.service, "_sessions", {}).get(
            self.service._session_key(provider_id, credential_id)
        )
        if (
            provider_id == "kobo" and session is not None
            and bool(getattr(getattr(session, "capabilities", None), "backup", False))
        ):
            declared_sync_caps.append("local_materialization")
        return SyncSourceRef(
            source_id=source_id,
            identity_id=account_ref.identity_id,
            provider_id=account_ref.provider_id,
            account_id=account_ref.account_id,
            label=account_ref.label,
            connected=account_ref.connected,
            sync_capable=sync_capable,
            enabled=self._enabled(source_id),
            last_attempt=str(state.get("last_attempt") or ""),
            last_success=str(state.get("last_success") or ""),
            status=str(state.get("status") or "idle"),
            error=str(state.get("error") or ""),
            sync_capabilities=tuple(declared_sync_caps),
        )

    def sources(self, identity_id: str | None = None) -> tuple[SyncSourceRef, ...]:
        rows = [self._ref(account) for account in self.service.accounts_for_identity(identity_id)]
        rows.sort(key=lambda row: (row.identity_id, row.provider_id, row.label.casefold(), row.source_id))
        return tuple(rows)

    def set_enabled(self, source_id: str, enabled: bool) -> SyncSourceRef:
        account = self._find_account(source_id)
        self.service.storage.metadata_set(self._preference_key(source_id), "1" if enabled else "0")
        self._notify()
        return self._ref(account)

    def synchronize_source(self, source_id: str, request: SyncRequest | None = None, control: Any = None) -> SyncResult:
        account = self._find_account(source_id)
        # The source_id is a stable digest of the exact identity/provider/
        # credential/account tuple. An explicit request must not override any
        # coordinate or silently broaden one-account work to other identities.
        expected_identity = str(getattr(account, "identity_id", "") or "")
        if request is not None and not isinstance(request, SyncRequest):
            return SyncResult(ok=False, warnings=("invalid source sync request",),
                              facts={"reason": "invalid_request"})
        request = request or SyncRequest(identity_id=expected_identity)
        if not expected_identity or (
            request.identity_id is not None and request.identity_id != expected_identity
        ):
            return SyncResult(ok=False, warnings=("source identity mismatch",),
                              facts={"reason": "identity_mismatch"})
        # The current generic broker only implements inventory/rights refresh.
        # Never reinterpret an explicit materialization, narrow phase or
        # unsupported option as a safe metadata refresh with fake success.
        if (
            type(request.deep) is not bool
            or type(request.scopes) is not tuple or request.scopes
            or not isinstance(request.options, dict) and
               not hasattr(request.options, "items")
        ):
            return SyncResult(ok=False, warnings=("unsupported source sync scope",),
                              facts={"reason": "unsupported_scope"})
        options = dict(request.options)
        actual = {
            "identity_id": expected_identity,
            "provider_id": str(getattr(account, "provider_id", "") or ""),
            "credential_id": str(getattr(account, "credential_id", "") or ""),
            "account_id": _account_id(account),
        }
        if any(
            key not in actual or type(value) is not str or value != actual[key]
            for key, value in options.items()
        ):
            return SyncResult(ok=False, warnings=("source target mismatch",),
                              facts={"reason": "target_mismatch"})
        if not self._enabled(source_id):
            return SyncResult(ok=True, changed=0, warnings=("source disabled",),
                              facts={"source_id": source_id, "skipped": True})
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state = self._state(source_id)
        state.update(last_attempt=now, status="running", error="", scope="manual",
                     identity_id=str(getattr(account, "identity_id", "") or ""))
        self._set_state(source_id, state); self._notify()
        try:
            result = self.service.synchronize_account_source(
                account, deep=bool(request.deep), control=control,
            )
            changed = sum(
                v for v in result.values() if isinstance(v, int) and not isinstance(v, bool)
            ) if isinstance(result, dict) else 0
            facts = dict(result) if isinstance(result, dict) else {"result": result}
            outcome = str(facts.get("status") or "").lower()
            incomplete = (outcome in {"partial", "failed", "error", "attention_required",
                                      "retryable_error"}
                          or bool(facts.get("errors")) or bool(facts.get("attention_required"))
                          or bool(facts.get("warnings")))
            if incomplete:
                state.update(status="partial", error="Synchronisation partielle")
                self._set_state(source_id, state); self._notify()
                return SyncResult(ok=False, changed=changed,
                                  warnings=("synchronisation partielle",), facts=facts)
            state.update(last_success=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         status="ok", error="")
            self._set_state(source_id, state); self._notify()
            return SyncResult(ok=True, changed=changed, facts=facts)
        except Exception as exc:
            state.update(status="error", error=str(exc))
            self._set_state(source_id, state); self._notify()
            return SyncResult(ok=False, warnings=(str(exc),), facts={"source_id": source_id})

    def synchronize(self, request: SyncRequest, control: Any = None) -> SyncResult:
        # An omitted identity must never turn a nominal "Sync" into an
        # unpreviewed batch across all provider accounts.
        if not isinstance(request, SyncRequest) or (
            type(request.identity_id) is not str or not request.identity_id
        ):
            return SyncResult(ok=False, changed=0, warnings=("explicit identity required",),
                              facts={"reason": "identity_required"})
        sources = [row for row in self.sources(request.identity_id) if row.enabled and row.connected and row.sync_capable]
        total = 0; warnings: list[str] = []; facts: dict[str, Any] = {"sources": {}}
        for index, source in enumerate(sources, 1):
            if control is not None and callable(getattr(control, "check_cancelled", None)):
                control.check_cancelled()
            if control is not None and callable(getattr(control, "report", None)):
                control.report(current=index - 1, total=len(sources), message=f"Synchronisation {source.label}")
            result = self.synchronize_source(source.source_id, request, control)
            total += result.changed
            warnings.extend(result.warnings)
            facts["sources"][source.source_id] = dict(result.facts)
        if control is not None and callable(getattr(control, "report", None)):
            control.report(current=len(sources), total=len(sources), message="Synchronisation terminée")
        return SyncResult(ok=not warnings, changed=total, warnings=tuple(warnings), facts=facts)

    def subscribe(self, callback: Any):
        if not callable(callback):
            raise TypeError("callback must be callable")
        assert self._subscribers is not None
        self._subscribers.append(callback)
        active = True
        def cancel():
            nonlocal active
            if active:
                active = False
                try: self._subscribers.remove(callback)
                except ValueError: pass
        return cancel

    def _notify(self) -> None:
        for callback in tuple(self._subscribers or ()):
            try: callback()
            except Exception: pass


@dataclass(slots=True)
class MetadataBrokerImpl:
    service: Any
    def library_entries(self, **options: Any): return self.service.library_entries(**options)
    def scan(self, **options: Any): return self.service.catalog_scan(**options)


@dataclass(slots=True)
class ReconciliationBrokerImpl:
    service: Any
    def reconcile_acquisitions(self): return self.service.reconcile_acquisitions()


@dataclass(slots=True)
class IdentityBrokerImpl:
    service: Any
    def identities(self):
        rows=[]
        for identity in getattr(self.service, "identities", ()) or ():
            rows.append({"identity_id": getattr(identity, "identity_id", ""), "name": getattr(identity, "name", ""), "label": getattr(identity, "label", "")})
        return rows
    def select(self, identity_id: str): return self.service.select_identity(identity_id)
    def accounts(self, identity_id: str | None = None): return tuple(_account_ref(self.service, row) for row in self.service.accounts_for_identity(identity_id))
    def statistics(self, identity_id: str, *, window_seconds: int = 2592000): return self.service.identity_statistics(identity_id, window_seconds=window_seconds)


@dataclass(slots=True)
class RuntimeCapabilitiesBrokerImpl:
    service: Any

    @staticmethod
    def _formats(download: Any, fallback: Any) -> tuple[str, ...]:
        values: list[str] = []
        if isinstance(download, dict):
            for name in ("http", "acsm", "kobo"):
                row = download.get(name)
                if not isinstance(row, dict):
                    continue
                formats = row.get("formats")
                if not isinstance(formats, (list, tuple)):
                    continue
                for value in formats:
                    normalized = str(value or "").strip().casefold()
                    if normalized and normalized not in values:
                        values.append(normalized)
        if isinstance(fallback, (list, tuple)):
            for value in fallback:
                normalized = str(value or "").strip().casefold()
                if normalized and normalized not in values:
                    values.append(normalized)
        return tuple(values)

    def acquisition(self, provider_id: str, *, refresh: bool = False) -> RuntimeAcquisitionCapabilities:
        provider = str(provider_id or "").strip().casefold()
        if provider != "kobo":
            return RuntimeAcquisitionCapabilities(provider_id=provider)

        state = self.service.kobo_adept_state(force=bool(refresh))
        if not isinstance(state, dict):
            state = {}
        caps = state.get("capabilities") if isinstance(state.get("capabilities"), dict) else {}
        download = caps.get("download") if isinstance(caps.get("download"), dict) else {}
        declared = {
            str(value)
            for value in (state.get("declared_capabilities") or ())
            if str(value)
        }

        modes: list[str] = []
        http = download.get("http") if isinstance(download.get("http"), dict) else {}
        if bool(http.get("available")) or "download_http_epub_pdf" in declared:
            modes.append("http_plain")
        if state.get("acsm_available") is True:
            modes.append("adobe_adept")
        if state.get("kobo_delegated_available") is True:
            modes.append("kobo_delegated")
        if state.get("kobo_kdrm_available") is True:
            modes.append("kobo_kdrm")

        try:
            contract_version = int(state.get("contract_version")) if state.get("contract_version") is not None else None
        except (TypeError, ValueError):
            contract_version = None

        return RuntimeAcquisitionCapabilities(
            provider_id="kobo",
            available=state.get("available") is True,
            compatible=state.get("compatible") is True,
            operational=state.get("operational") is True,
            contract_version=contract_version,
            modes=tuple(modes),
            formats=self._formats(download, state.get("kobo_formats")),
            software_version=str(state.get("software_version") or "")[:80],
        )


@dataclass(slots=True)
class AdeptProfileBrokerImpl:
    service: Any

    @staticmethod
    def _payload(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        nested = value.get("profile")
        return dict(nested) if isinstance(nested, dict) else dict(value)

    def _ref(self, row: dict[str, Any]) -> AdeptProfileRef:
        stable = str(row.get("profile_id") or row.get("id") or "").strip()
        name = str(row.get("name") or row.get("profile") or "").strip()
        if not stable or not name:
            raise AttentionRequired(
                "Le runtime ADEPT ne publie pas de référence de profil stable.",
                code="adept_profile_stable_id_required",
            )
        provisioning = row.get("provisioning") if isinstance(row.get("provisioning"), dict) else {}
        return AdeptProfileRef(
            profile_id=stable,
            name=name,
            complete=row.get("complete") is True,
            fetch_capable=row.get("fetch_capable") is True,
            acsm_fetch_ready=provisioning.get("acsm_fetch_ready") is True,
        )

    def profiles(self) -> tuple[AdeptProfileRef, ...]:
        return tuple(self._ref(row) for row in self.service.kobo_profiles())

    def _find(self, profile_id: str) -> AdeptProfileRef:
        wanted = str(profile_id or "").strip()
        matches = [row for row in self.profiles() if row.profile_id == wanted]
        if len(matches) != 1:
            raise AttentionRequired("Profil ADEPT absent ou ambigu.", code="adept_origin_profile_missing")
        return matches[0]

    def bound(self, identity_id: str) -> AdeptProfileRef | None:
        identity = self.service.identity_by_id(str(identity_id or ""))
        if identity is None:
            raise ValueError("unknown identity")
        stable = self.service.storage.adept_profile_binding_get("identity", identity.identity_id)
        return self._find(stable) if stable else None

    def resolve_name(self, profile_id: str) -> str:
        return self._find(profile_id).name

    def bind(self, identity_id: str, profile_id: str) -> AdeptProfileRef:
        identity = self.service.identity_by_id(str(identity_id or ""))
        if identity is None:
            raise ValueError("unknown identity")
        profile = self._find(profile_id)
        if not (profile.complete and profile.fetch_capable and profile.acsm_fetch_ready):
            raise AttentionRequired(
                "Le profil ADEPT n’est pas prêt pour les ACSM.",
                code="adept_profile_not_ready",
            )
        self.service.storage.adept_profile_binding_set("identity", identity.identity_id, profile.profile_id)
        return profile

    def provision(self, name: str, username: str, *, identity_id: str | None = None) -> AdeptProfileRef:
        if identity_id is not None and self.service.identity_by_id(str(identity_id)) is None:
            raise ValueError("unknown identity")
        runtime = self.service._kobo_adapter()
        provision = getattr(runtime, "provision_profile", None)
        if not callable(provision):
            raise AttentionRequired(
                "Provisioning de profil ADEPT indisponible.",
                code="adept_profile_provisioning_unsupported",
            )
        shown = self._payload(provision(name, username))
        refresh_capabilities = getattr(runtime, "capabilities", None)
        if callable(refresh_capabilities):
            refresh_capabilities()
        rows = list(self.service.kobo_profiles())
        shown_name = str(shown.get("name") or shown.get("profile") or name or "").strip()
        candidates = [row for row in rows if str(row.get("name") or row.get("profile") or "").strip() == shown_name]
        profile = self._ref(candidates[0] if len(candidates) == 1 else shown)
        if not (profile.complete and profile.fetch_capable and profile.acsm_fetch_ready):
            raise AttentionRequired(
                "Le profil ADEPT a été créé mais n’est pas prêt pour les ACSM.",
                code="adept_profile_provisioning_incomplete",
            )
        if identity_id is not None:
            self.service.storage.adept_profile_binding_set("identity", str(identity_id), profile.profile_id)
        return profile


@dataclass(slots=True)
class AdeptLoanBrokerImpl:
    """Public facade over Core ADEPT binding and confirmed-return orchestration."""

    service: Any

    @staticmethod
    def _profiles(profile_ids: tuple[str, ...]) -> list[str] | None:
        values = [str(value).strip() for value in profile_ids if str(value).strip()]
        if len(values) != len(set(values)):
            values = list(dict.fromkeys(values))
        return values or None

    def reconcile(self, acquisition_id: str, *, profile_ids: tuple[str, ...] = ()):
        return self.service.reconcile_adept_acquisition(
            str(acquisition_id),
            profile_references=self._profiles(profile_ids),
        )

    def choices(self, acquisition_id: str, *, profile_ids: tuple[str, ...] = ()):
        return self.service.adept_loan_choices(
            str(acquisition_id),
            profile_references=self._profiles(profile_ids),
        )

    def attach(self, acquisition_id: str, profile_id: str, adept_loan_id: str):
        return self.service.attach_adept_loan(
            str(acquisition_id),
            str(profile_id),
            str(adept_loan_id),
        )

    def return_acquisition(self, acquisition_id: str):
        return self.service.return_adept_acquisition(str(acquisition_id))


@dataclass(slots=True)
class DiagnosticsBrokerImpl:
    service: Any

    def snapshot(self):
        return tuple(self.service.diagnostics.snapshot())

    def events(
        self, *, source: str = "all", module: str = "", severity: str = "",
        since: str | None = None, until: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> dict[str, Any]:
        """Read-only, bounded public feed; filesystem paths are Core-owned."""
        from .core.diagnostic_event_feed import query_diagnostic_events
        from .paths import log_file
        return query_diagnostic_events(
            self.service.diagnostics, log_path=log_file(),
            source=source, module=module, severity=severity,
            since=since, until=until, limit=limit, offset=offset,
        )

    def system_status(self):
        from .system_status import system_status
        return system_status(getattr(self.service, "plugin_manager", None))

    def inventory(self) -> tuple[dict[str, Any], ...]:
        """Return the versions and states of the plugin manifests actually loaded."""
        from .diagnostics import redact
        manager = getattr(self.service, "plugin_manager", None) or getattr(self.service, "_plugin_manager", None)
        if manager is None:
            return ()
        rows = []
        for raw in manager.diagnostic():
            row = {
                "plugin_id": str(raw.get("plugin_id") or ""),
                "display_name": str(raw.get("display_name") or raw.get("plugin_id") or ""),
                "type": str(raw.get("type") or ""),
                "version": str(raw.get("version") or ""),
                "package_id": str(raw.get("package_id") or ""),
                "package_version": str(raw.get("package_version") or ""),
                "expected_package_version": str(raw.get("expected_package_version") or ""),
                "package_freshness": str(raw.get("package_freshness") or "unknown"),
                "package_freshness_reason": str(raw.get("package_freshness_reason") or ""),
                "enabled": bool(raw.get("enabled")),
                "state": str(raw.get("state") or "unknown"),
                "builtin": bool(raw.get("builtin")),
                "installed_at": raw.get("installed_at"),
                "reason_code": str(raw.get("reason_code") or ""),
                "capabilities": tuple(str(value) for value in (raw.get("capabilities") or ())),
            }
            rows.append(redact(row))
        return tuple(rows)

    def runtime_diagnostic(self, plugin_id: str) -> dict[str, Any]:
        """Expose only bounded process metadata, never raw runtime streams or IDs."""
        from .diagnostics import redact, redact_text
        getter = getattr(self.service, "runtime_diagnostic", None)
        if not callable(getter):
            return {}
        raw = getter(str(plugin_id or ""))
        if not isinstance(raw, dict):
            return {}
        allowed = {
            "operation", "duration_ms", "stdout_bytes", "stderr_bytes",
            "stderr_summary", "returncode", "stdout_mode", "stdout_summary",
            "json_line", "json_column", "launch_error",
        }
        bounded: dict[str, Any] = {}
        for key in allowed:
            if key not in raw:
                continue
            value = raw[key]
            if isinstance(value, str):
                bounded[key] = redact_text(value)[:240]
            elif isinstance(value, (int, float, bool)) or value is None:
                bounded[key] = value
        return dict(redact(bounded))

    def active_checks(self) -> tuple[HealthCheckResult, ...]:
        from .diagnostics import redact, redact_text

        rows: list[HealthCheckResult] = []
        try:
            sources = SynchronizationBrokerImpl(self.service).sources()
            if not sources:
                rows.append(HealthCheckResult(
                    "core.sync.sources",
                    "sync",
                    "ok",
                    "Sources configurées",
                    {"count": 0},
                    "core",
                ))
            for source in sources:
                error = str(source.error or "")
                connected = bool(source.connected)
                capable = bool(source.sync_capable)
                status = "error" if error else ("ok" if connected and capable else "warning")
                rows.append(HealthCheckResult(
                    f"core.sync.{source.source_id}",
                    str(source.provider_id or "sync"),
                    status,
                    "Source " + str(source.label or source.provider_id or "configurée"),
                    redact({
                        "provider_id": source.provider_id,
                        "connected": connected,
                        "sync_capable": capable,
                        "enabled": bool(source.enabled),
                        "last_success": source.last_success,
                        "error": error[:240] if error else "",
                    }),
                    "core",
                ))
        except Exception as exc:
            rows.append(HealthCheckResult(
                "core.sync.sources",
                "sync",
                "error",
                "Sources configurées",
                {"error_type": type(exc).__name__},
                "core",
            ))

        manager = getattr(self.service, "plugin_manager", None)
        if manager is None:
            manager = getattr(self.service, "_plugin_manager", None)
        if manager is None:
            rows.append(HealthCheckResult(
                "core.plugins",
                "plugins",
                "warning",
                "Plugins",
                {"reason": "plugin manager indisponible"},
                "core",
            ))
            return tuple(rows)

        try:
            providers = tuple(manager.services("provider.library"))
            rows.append(HealthCheckResult(
                "core.providers.loaded",
                "provider.library",
                "ok",
                "Providers chargés",
                {"count": len(providers)},
                "core",
            ))
        except Exception as exc:
            rows.append(HealthCheckResult(
                "core.providers.loaded",
                "provider.library",
                "error",
                "Providers chargés",
                {"error_type": type(exc).__name__},
                "core",
            ))

        try:
            contributions = tuple(manager.services("diagnostics.check"))
        except Exception as exc:
            rows.append(HealthCheckResult(
                "core.diagnostics.contributions",
                "diagnostics.check",
                "error",
                "Vérifications plugins",
                {"error_type": type(exc).__name__},
                "core",
            ))
            contributions = ()

        allowed_status = {"ok", "warning", "error", "unknown"}
        for resolved in contributions:
            try:
                checks = tuple(resolved.service.checks())
            except Exception as exc:
                rows.append(HealthCheckResult(
                    f"{resolved.plugin_id}.check",
                    resolved.plugin_id,
                    "error",
                    resolved.plugin_id,
                    {"error_type": type(exc).__name__},
                    "plugin",
                ))
                continue
            for item in checks:
                if not isinstance(item, HealthCheckResult):
                    rows.append(HealthCheckResult(
                        f"{resolved.plugin_id}.invalid",
                        resolved.plugin_id,
                        "error",
                        "Résultat de diagnostic invalide",
                        {"result_type": type(item).__name__},
                        "plugin",
                    ))
                    continue
                status = item.status if item.status in allowed_status else "unknown"
                rows.append(HealthCheckResult(
                    redact_text(str(item.check_id or resolved.plugin_id))[:160],
                    redact_text(str(item.component or resolved.plugin_id))[:160],
                    status,
                    redact_text(str(item.message or item.component or resolved.plugin_id))[:300],
                    redact(dict(item.detail or {})),
                    str(item.scope or "plugin")[:80],
                ))
        return tuple(rows)

    def export_text(self) -> str:
        from . import __version__
        return self.service.diagnostics.text(__version__, {"plugins": len(getattr(getattr(self.service, "plugin_manager", None), "statuses", lambda: [])())})

    def support_report(
        self, *, verbose: bool = False, include_checks: bool = True,
    ):
        from .support_diagnostic import build_support_report
        return build_support_report(
            self.service, verbose=verbose, include_checks=include_checks,
        )

    def export_support_text(self, *, verbose: bool = False) -> str:
        from .support_diagnostic import support_report_text
        return support_report_text(self.service, verbose=verbose)


@dataclass(slots=True)
class LibraryNamingBrokerImpl:
    service: Any
    def preview(self, *, title: str, author: str = "", series: str = "", index: str = "", extension: str = "epub") -> str:
        if hasattr(self.service, "preview_library_filename"):
            return self.service.preview_library_filename(title=title, author=author, series=series, index=index, extension=extension)
        stem = f"{author} - {title}" if author else title
        safe = self.service.acquisition._safe_filename(stem or "document")
        ext = str(extension or "epub").lstrip(".")
        return f"{safe}.{ext}"


def build_public_brokers(service: Any) -> dict[str, object]:
    artifact_execution = ArtifactExecutionBrokerImpl(service)
    return {
        "catalog": CatalogBrokerImpl(service),
        "acquisition": AcquisitionBrokerImpl(service),
        "sync": SynchronizationBrokerImpl(service),
        "metadata": MetadataBrokerImpl(service),
        "reconciliation": ReconciliationBrokerImpl(service),
        "identities": IdentityBrokerImpl(service),
        "runtime.capabilities": RuntimeCapabilitiesBrokerImpl(service),
        "adept.profiles": AdeptProfileBrokerImpl(service),
        "adept.loans": AdeptLoanBrokerImpl(service),
        "diagnostics": DiagnosticsBrokerImpl(service),
        "library.naming": LibraryNamingBrokerImpl(service),
        "artifact.execution": artifact_execution,
        "library.read": LibraryReadBrokerImpl(service),
        "library.maintenance": LibraryMaintenanceBrokerImpl(service),
        "artifact.routing": ArtifactRoutingBrokerImpl(service, artifact_execution),
        "cache": CacheBrokerImpl(service),
        "tasks": TaskBrokerImpl(service),
    }
