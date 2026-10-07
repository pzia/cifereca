from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from cifereca.plugin_api import AccountRef, SyncRequest, SyncResult, SyncSourceRef
from .artifact_runtime import ArtifactExecutionBrokerImpl, LibraryReadBrokerImpl, LibraryMaintenanceBrokerImpl, ArtifactRoutingBrokerImpl
from .cache_runtime import CacheBrokerImpl
from .task_runtime import TaskBrokerImpl


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
        raw = self.service.storage.metadata_get("sync.source.state." + source_id)
        if not raw:
            return {}
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _set_state(self, source_id: str, value: dict[str, Any]) -> None:
        self.service.storage.metadata_set("sync.source.state." + source_id, value)

    def _find_account(self, source_id: str) -> Any:
        for account in getattr(self.service, "accounts", ()) or ():
            if self._source_id(account) == source_id:
                return account
        raise ValueError(f"unknown sync source: {source_id}")

    def _ref(self, account: Any) -> SyncSourceRef:
        source_id = self._source_id(account)
        account_ref = _account_ref(self.service, account)
        state = self._state(source_id)
        sync_capable = bool(set(account_ref.capabilities) & {"loans", "personal_library"})
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
        if not self._enabled(source_id):
            return SyncResult(ok=True, changed=0, warnings=("source disabled",), facts={"source_id": source_id, "skipped": True})
        request = request or SyncRequest(identity_id=str(getattr(account, "identity_id", "") or ""))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state = {"last_attempt": now, "status": "running", "error": ""}
        self._set_state(source_id, state); self._notify()
        try:
            result = self.service.synchronize_account_source(account, deep=bool(request.deep), control=control)
            changed = sum(v for v in result.values() if isinstance(v, int) and not isinstance(v, bool)) if isinstance(result, dict) else 0
            state.update({"last_success": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": "ok", "error": ""})
            self._set_state(source_id, state); self._notify()
            return SyncResult(ok=True, changed=changed, facts=dict(result) if isinstance(result, dict) else {"result": result})
        except Exception as exc:
            state.update({"status": "error", "error": str(exc)})
            self._set_state(source_id, state); self._notify()
            return SyncResult(ok=False, warnings=(str(exc),), facts={"source_id": source_id})

    def synchronize(self, request: SyncRequest, control: Any = None) -> SyncResult:
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
class DiagnosticsBrokerImpl:
    service: Any
    def snapshot(self):
        return tuple(self.service.diagnostics.snapshot())
    def system_status(self):
        from .system_status import system_status
        return system_status(getattr(self.service, "plugin_manager", None))
    def export_text(self) -> str:
        from . import __version__
        return self.service.diagnostics.text(__version__, {"plugins": len(getattr(getattr(self.service, "plugin_manager", None), "statuses", lambda: [])())})


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
        "diagnostics": DiagnosticsBrokerImpl(service),
        "library.naming": LibraryNamingBrokerImpl(service),
        "artifact.execution": artifact_execution,
        "library.read": LibraryReadBrokerImpl(service),
        "library.maintenance": LibraryMaintenanceBrokerImpl(service),
        "artifact.routing": ArtifactRoutingBrokerImpl(service, artifact_execution),
        "cache": CacheBrokerImpl(service),
        "tasks": TaskBrokerImpl(service),
    }
