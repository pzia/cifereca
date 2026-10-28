from __future__ import annotations
import ast
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from inspect import getattr_static
from datetime import datetime, timezone
import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Callable

from cifereca.plugin_api import AccountRef, AdeptProfileRef, AttentionRequired, HealthCheckResult, RuntimeAcquisitionCapabilities, SyncRequest, SyncResult, SyncPhaseState, SyncSourceRef
from .artifact_runtime import ArtifactExecutionBrokerImpl, LibraryReadBrokerImpl, LibraryMaintenanceBrokerImpl, ArtifactRoutingBrokerImpl
from .cache_runtime import CacheBrokerImpl
from .task_runtime import TaskBrokerImpl
from .core.source_materialization import MaterializationPlans

_SYNC_LOG = logging.getLogger("cifereca.sync")
# The synchronization broker is a public plugin/UI boundary: no exception,
# provider message or persisted legacy error is safe to echo verbatim.
_SYNC_PUBLIC_FAILURE = "Échec de synchronisation"
_SYNC_PUBLIC_ERRORS = frozenset({
    "", _SYNC_PUBLIC_FAILURE, "Synchronisation partielle",
    "Ouverture partielle", "Échec de synchronisation au démarrage",
})
_SYNC_PUBLIC_COUNTS = (
    "loans", "library", "owned_rights", "acquired",
    "attention_required", "errors", "warnings",
)
_SYNC_PUBLIC_STATUSES = frozenset({
    "ok", "success", "skipped", "partial", "failed", "error",
    "attention_required", "retryable_error",
})


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
        credential_id=credential,
        scope_version=2 if credential else 1,
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
    def catalogue_source_capabilities(self, identity_id: str | None = None):
        """Optional additive Core inventory; legacy CatalogBroker stays unchanged."""
        return self.service.catalogue_source_capabilities(identity_id=identity_id)


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
            choices = [row for row in eligible if row.account_id == account_id]
            if not choices:
                raise ValueError(f"account not eligible: {account_id}")
            if len(choices) != 1:
                # Two credentials may legitimately share an upstream ID.
                # A first-match route would acquire under an arbitrary owner.
                raise ValueError("ambiguous eligible account: credential selection required")
            chosen = choices[0]
        elif len(eligible) == 1:
            chosen = eligible[0]
        elif not eligible:
            raise ValueError("no eligible connected account")
        else:
            raise ValueError("account selection required")
        # Preserve the source object but bind the selected credential when the
        # model supports mutable dataclass attributes (SearchResult does).
        matches = [
            account for account in self.service.accounts_for_identity(chosen.identity_id)
            if (_account_id(account), str(getattr(account, "identity_id", "") or ""),
                str(getattr(account, "provider_id", "") or ""),
                str(getattr(account, "credential_id", "") or "")) == (
                    chosen.account_id, chosen.identity_id, chosen.provider_id,
                    chosen.credential_id,
                )
        ]
        if len(matches) != 1:
            # Never silently rebind a selected account to another credential.
            raise ValueError("selected account binding changed or ambiguous")
        internal = matches[0]
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
    _materialization_plans: MaterializationPlans = field(default_factory=MaterializationPlans, repr=False)
    _numilog_consent_plans: MaterializationPlans = field(default_factory=MaterializationPlans, repr=False)
    _scheduled_inventory_requested: set[str] = field(default_factory=set, repr=False)
    _scheduled_inventory_running: set[str] = field(default_factory=set, repr=False)
    _scheduled_inventory_lock: Any = field(default_factory=threading.Lock, repr=False)

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
        from .core.source_enablement import source_enabled
        raw = self.service.storage.metadata_get(self._preference_key(source_id))
        return source_enabled(raw)

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
                "inventory_last_attempt", "inventory_last_success", "inventory_status",
            ) if key in value and isinstance(value[key], str)
        }
        # Historical state could contain raw exception strings with URLs,
        # book titles or credential-bearing paths. Reading it must not expose
        # those values via the public SyncSourceRef or the GTK page.
        if state.get("error", "") not in _SYNC_PUBLIC_ERRORS:
            state["error"] = _SYNC_PUBLIC_FAILURE
        # The legacy database also held free-form *status* and timestamps.
        # Never relay a signed provider URL or private path under another
        # public field merely because the error field was redacted.
        if state.get("status", "") not in (
            _SYNC_PUBLIC_STATUSES | {"", "idle", "running", "cancelled", "interrupted"}
        ):
            state["status"] = "unknown"
        for name in ("last_attempt", "last_success",
                     "inventory_last_attempt", "inventory_last_success"):
            if name in state:
                sanitized = self._safe_phase_timestamp(state[name])
                if name.startswith("inventory_") and state[name] and not sanitized:
                    # A damaged discover/rights clock must never authorize
                    # another unattended provider request.
                    state["inventory_status"] = "unknown"
                state[name] = sanitized
        if state.get("inventory_status", "") not in {
            "", "running", "success", "partial", "failed", "unknown",
        }:
            state["inventory_status"] = "unknown"
        if state.get("scope", "") not in {"", "manual", "opening", "scheduled"}:
            state["scope"] = ""
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
        # This is a routing key, not an instruction to pick the first
        # matching session. Duplicate account bindings must fail closed
        # before network access or local mutations.
        matches = []
        # Routing is based on an exact hash, never on the first account.
        # A malformed *unrelated* account must not deny source management
        # to healthy siblings (notably a disconnected Numilog revocation).
        for account in (getattr(self.service, "accounts", ()) or ()):
            try:
                if self._source_id(account) == source_id:
                    matches.append(account)
            except Exception:
                continue
        if len(matches) != 1:
            raise ValueError("ambiguous sync source" if matches else "unknown sync source")
        return matches[0]

    @staticmethod
    def _safe_phase_timestamp(value: Any) -> str:
        """Normalize strictly bounded UTC timestamps; never reflect raw storage."""
        if type(value) is not str or not 20 <= len(value) <= 35:
            return ""
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None or not 2000 <= parsed.year <= 2100:
                return ""
            return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
        except (ValueError, OverflowError):
            return ""

    def _phase_states(
        self, account: Any, state: dict[str, Any], sync_caps: tuple[str, ...],
    ) -> tuple[SyncPhaseState, ...]:
        """Safe, read-only projection: no remote call, migration or acquisition."""
        raw_status = state.get("status")
        inventory_status = {
            "ok": "success", "success": "success",
            "running": "running", "partial": "partial",
            "error": "failed", "failed": "failed",
        }.get(raw_status, "idle")
        if (
            getattr(account, "provider_id", None) in {"kobo", "numilog"}
            and "remote_inventory" in sync_caps
        ):
            # Opening sync may refresh loans, without reading the private
            # store inventory. It cannot attest discover/rights freshness.
            inventory_status = state.get("inventory_status", "idle") or "idle"
            inventory_attempt = state.get("inventory_last_attempt")
            inventory_success = state.get("inventory_last_success")
        else:
            inventory_attempt = state.get("last_attempt")
            inventory_success = state.get("last_success")
        inventory = SyncPhaseState(
            phase="discover_rights", status=inventory_status,
            last_attempt=self._safe_phase_timestamp(inventory_attempt),
            last_success=self._safe_phase_timestamp(inventory_success),
        )
        binding_valid = False
        if "local_materialization" not in sync_caps:
            materialization = SyncPhaseState("materialize", "not_supported")
        else:
            identity = str(getattr(account, "identity_id", "") or "")
            credential = str(getattr(account, "credential_id", "") or "")
            # A checkpoint is credible only for its currently owning identity.
            try:
                bound = self.service.storage.identity_for_connection("kobo", credential)
            except (AttributeError, TypeError, ValueError, KeyError):
                bound = None
            binding_valid = bool(identity and credential and bound == identity)
            if not binding_valid:
                materialization = SyncPhaseState("materialize", "unknown")
            else:
                from .core.kobo_backup import _kobo_checkpoint_for
                row = _kobo_checkpoint_for(self.service, identity, credential)
                if row is None:
                    materialization = SyncPhaseState("materialize")
                else:
                    raw_checkpoint_status = row.get("status")
                    status = {
                        "complete": "success", "partial": "partial",
                        "running": "running", "interrupted": "interrupted",
                        "attention_required": "attention_required",
                        "failed": "failed", "aborted": "failed",
                        "cancelled": "cancelled",
                    }.get(
                        raw_checkpoint_status if type(raw_checkpoint_status) is str
                        else "", "unknown",
                    )
                    timestamp = row.get("updated_at")
                    when = (
                        datetime.fromtimestamp(timestamp, timezone.utc)
                            .isoformat(timespec="seconds")
                        if type(timestamp) is int and 946684800 <= timestamp <= 4102444800
                        else ""
                    )
                    materialization = SyncPhaseState(
                        phase="materialize", status=status, last_attempt=when,
                        # A partial checkpoint does not attest historical success;
                        # never substitute an inventory clock or fabricate it.
                        last_success=when if status == "success" else "",
                    )
        explicit = self._materialization_state(self._source_id(account))
        # A stale explicit result cannot override a revoked or rebound source.
        if explicit and "local_materialization" in sync_caps and binding_valid:
            status = explicit.get("status")
            if status == "running":
                from .core.source_materialization import process_owner
                if explicit.get("owner") != process_owner():
                    status = "interrupted"
            if type(status) is str and status in {"running", "success", "partial", "blocked", "failed", "cancelled", "interrupted",
                          "materialization_failed", "source_changed", "source_disabled", "source_disconnected"}:
                materialization = SyncPhaseState(
                    "materialize", status, self._safe_phase_timestamp(explicit.get("last_attempt")),
                    self._safe_phase_timestamp(explicit.get("last_success")),
                )
        return (inventory, materialization, SyncPhaseState("reconcile", "not_supported"))

    def _materialization_state(self, source_id: str) -> dict:
        raw = self.service.storage.metadata_get("sync.source.materialization." + source_id)
        if type(raw) is not str or len(raw) > 2048:
            return {}
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except (ValueError, RecursionError):
            return {}

    def _set_materialization_state(self, source_id: str, status: str, attempted_at: str) -> None:
        previous = self._materialization_state(source_id)
        state = {"status": status, "last_attempt": attempted_at,
                 "last_success": attempted_at if status == "success" else
                 self._safe_phase_timestamp(previous.get("last_success"))}
        if status == "running":
            from .core.source_materialization import process_owner
            state["owner"] = process_owner()
        self.service.storage.metadata_set("sync.source.materialization." + source_id, json.dumps(state))

    def prepare_local_materialization(self, source_id: str) -> SyncResult:
        """Explicit fresh rights read and bounded one-use confirmation plan."""
        from .core.source_materialization import prepare
        return prepare(self, source_id)

    def materialize_source(self, source_id: str, confirmation_token: str, control: Any = None) -> SyncResult:
        """Confirm one previously prepared source; never falls back to a batch."""
        from .core.source_materialization import execute
        return execute(self, source_id, confirmation_token, control)

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
        # The opt-in BnF loan→ACSM→ADEPT path is distinct from the generic
        # Kobo materialization broker. Advertise only that this pathway exists,
        # never that consent, the ADEPT runtime or a successful delivery does.
        # Static inspection cannot invoke a provider getter or network call.
        if (
            provider_id == "bnf-pret" and "loans" in source_caps
            and session is not None
            and callable(getattr_static(session, "download_digital", None))
        ):
            declared_sync_caps.append("loan_auto_materialization")
        # Core owns real startup/periodic inventory dispatch for these stores.
        # Availability is not consent: policies still default to manual and
        # the scheduler revalidates exact source/account/session before I/O.
        if provider_id in {"kobo", "numilog"} and "personal_library" in source_caps:
            declared_sync_caps.append("scheduled_refresh")
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
            phase_states=self._phase_states(account, state, tuple(declared_sync_caps)),
        )

    def sources(self, identity_id: str | None = None) -> tuple[SyncSourceRef, ...]:
        rows = [self._ref(account) for account in self.service.accounts_for_identity(identity_id)]
        rows.sort(key=lambda row: (row.identity_id, row.provider_id, row.label.casefold(), row.source_id))
        return tuple(rows)

    def set_enabled(self, source_id: str, enabled: bool) -> SyncSourceRef:
        if type(enabled) is not bool:
            raise ValueError("invalid source enabled preference")
        account = self._find_account(source_id)
        if getattr(account, "provider_id", None) == "numilog":
            if enabled:
                # Old releases could leave a disabled source with an active
                # consent after a crash in the former two-step revoke.
                self.service.storage.numilog_materialization_enable_source(source_id)
            else:
                self.service.storage.numilog_materialization_disable_source(source_id)
            # Invalidate stale SHA-preview proposals after either source
            # transition, even when another account is selected in GTK.
            with self._numilog_consent_plans.lock:
                self._numilog_consent_plans.entries.clear()
        else:
            self.service.storage.metadata_set(
                self._preference_key(source_id), "1" if enabled else "0",
            )
        self._notify()
        return self._ref(account)

    def inventory_refresh_schedule(self, source_id: str) -> dict[str, object]:
        """Read the *discover/rights* opt-in, not the acquisition policy."""
        from .core.source_refresh_policy import load_source_refresh_schedule
        try:
            account = self._find_account(source_id)
        except (ValueError, TypeError):
            raise ValueError("unknown sync source") from None
        if getattr(account, "provider_id", None) not in {"kobo", "numilog"}:
            raise ValueError("inventory refresh schedule not supported")
        policy = load_source_refresh_schedule(self.service.storage, source_id)
        return {"mode": policy.mode, "interval_minutes": policy.interval_minutes}

    def set_inventory_refresh_schedule(
        self, source_id: str, mode: str, *, interval_minutes: int = 60,
    ) -> dict[str, object]:
        """Explicit per-source metadata/rights consent; no provider call.

        Neither this method nor its stored policy authorizes acquisition.
        Only an exact, enabled, connected Kobo/Numilog source can be opted in.
        An ineligible source may still have its schedule disabled manually.
        """
        from .core.source_refresh_policy import (
            SourceRefreshSchedule, inventory_source_eligible,
            save_source_refresh_schedule,
        )
        policy = SourceRefreshSchedule(mode=mode, interval_minutes=interval_minutes)
        try:
            account = self._find_account(source_id)
        except (ValueError, TypeError):
            raise ValueError("unknown sync source") from None
        if getattr(account, "provider_id", None) not in {"kobo", "numilog"}:
            raise ValueError("inventory refresh schedule not supported")
        if policy.mode != "manual" and not inventory_source_eligible(
            self.service, account,
        ):
            raise ValueError("source not eligible for scheduled inventory")
        saved = save_source_refresh_schedule(
            self.service.storage, source_id, mode=policy.mode,
            interval_minutes=policy.interval_minutes,
        )
        self._notify()
        return {"mode": saved.mode, "interval_minutes": saved.interval_minutes}

    def synchronize_scheduled_inventory(
        self, identity_id: str, trigger: str, *, control: Any = None,
    ) -> SyncResult:
        """Dispatch due Kobo/Numilog *inventory* refreshes for one explicit identity.

        A scheduler may call this at startup or on its periodic tick; this
        method does not install timers or silently enroll any accounts.
        Permissions and identity bindings are rechecked immediately before
        each metadata-only `synchronize_source` call. Acquisitions are never
        executed by this broker path.
        """
        from .core.source_refresh_policy import (
            inventory_source_eligible, load_source_refresh_schedule,
            source_refresh_due,
        )
        from .plugin_api import Cancelled
        if type(identity_id) is not str or not identity_id:
            return SyncResult(False, facts={"reason": "identity_required"})
        # A syntactically nonempty input is not proof of a configured
        # identity. Do not report a false-success empty scheduled batch for
        # an unknown profile or arbitrary credential-bearing string.
        known = getattr(self.service, "identities", None)
        if known is None:
            known = getattr(self.service, "accounts", ())
        if not any(
            getattr(row, "identity_id", None) == identity_id for row in known
        ):
            return SyncResult(False, facts={"reason": "unknown_identity"})
        if type(trigger) is not str or trigger not in {"startup", "periodic"}:
            return SyncResult(False, facts={"reason": "invalid_trigger"})
        # The broker clock is authoritative: plugins must not force a
        # future timestamp to bypass the persisted retry interval.
        instant = datetime.now(timezone.utc)
        total = 0
        attempted = 0
        failed = 0
        for account in sorted(
            self.service.accounts_for_identity(identity_id),
            key=self._source_id,
        ):
            # A cancelled batch must not start work for another account.
            if control is not None and callable(getattr(control, "check_cancelled", None)):
                control.check_cancelled()
            if getattr(account, "provider_id", None) not in {"kobo", "numilog"}:
                continue
            source_id = self._source_id(account)
            # Unknown/duplicate source references and invalid source state
            # must never become an automatic network retry.
            try:
                if self._find_account(source_id) is not account:
                    continue
                raw = self.service.storage.metadata_get(
                    "sync.source.state." + source_id
                )
                state = self._state(source_id)
                if raw and not state:
                    continue
                if (
                    state.get("last_attempt")
                    and not self._safe_phase_timestamp(state["last_attempt"])
                ):
                    continue
                is_private_inventory = getattr(account, "provider_id", None) in {"kobo", "numilog"}
                inventory_status = (
                    state.get("inventory_status", "idle") if is_private_inventory
                    else state.get("status", "idle")
                )
                if inventory_status not in {
                    "idle", "running", "success", "partial", "failed",
                    "error", "ok", "",
                }:
                    continue
                # Another active source sync (manual or opening) holds the
                # global writer lock. Even a due inventory must not race it.
                if state.get("status") == "running":
                    inventory_status = "running"
                inventory_last_attempt = (
                    state.get("inventory_last_attempt", "") if is_private_inventory
                    else state.get("last_attempt", "")
                )
                policy = load_source_refresh_schedule(
                    self.service.storage, source_id,
                )
                eligible = inventory_source_eligible(self.service, account)
                if not source_refresh_due(
                    policy, trigger=trigger, now=instant,
                    last_attempt=inventory_last_attempt,
                    phase_status=inventory_status,
                    enabled=eligible, connected=eligible, capable=eligible,
                    already_requested_this_startup=(
                        source_id in self._scheduled_inventory_requested
                    ),
                ):
                    continue
                # Revalidation is intentional: no cached account choice,
                # cross-identity batch or stale opt-in can authorize a call.
                if not inventory_source_eligible(self.service, account):
                    continue
            except Exception:
                continue
            # Singleflight within this broker: concurrent startup/timer
            # triggers must never dispatch the same account twice. The
            # persisted last_attempt additionally throttles new instances.
            with self._scheduled_inventory_lock:
                if source_id in self._scheduled_inventory_running:
                    continue
                if (trigger == "startup"
                        and source_id in self._scheduled_inventory_requested):
                    continue
                self._scheduled_inventory_running.add(source_id)
                if trigger == "startup":
                    self._scheduled_inventory_requested.add(source_id)
            attempted += 1
            try:
                result = self.synchronize_source(
                    source_id, SyncRequest(identity_id=identity_id), control,
                )
            except Cancelled:
                # Cancellation is a lifecycle signal, not a provider fault.
                raise
            except Exception:
                # A source may fail before synchronize_source's provider
                # exception handler (e.g. persisting its running state).
                # Do not abandon later accounts or echo private exceptions.
                failed += 1
                continue
            finally:
                with self._scheduled_inventory_lock:
                    self._scheduled_inventory_running.discard(source_id)
            # synchronize_source may have converted a cancellation thrown
            # inside a provider into a failed SyncResult; observe the control
            # again before returning a misleading normal batch completion.
            if control is not None and callable(getattr(control, "check_cancelled", None)):
                control.check_cancelled()
            total += result.changed
            if not result.ok:
                failed += 1
        return SyncResult(
            ok=failed == 0, changed=total,
            warnings=("synchronisation planifiée partielle",) if failed else (),
            facts={"phase": "discover_rights", "trigger": trigger,
                   "attempted": attempted, "failed": failed,
                   "acquisition_authorized": False},
        )

    def local_materialization_schedule(self, source_id: str) -> str:
        """Read the *materialize* schedule only; inventory uses its own flow.

        Defaults to manual for missing/legacy/malformed preferences, including
        the old global kobo_backup.enabled switch. Never exposes credentials.
        """
        from .core.source_materialization_schedule import policy_key
        try:
            account = self._find_account(source_id)
        except (ValueError, TypeError):
            raise ValueError("unknown sync source") from None
        if account.provider_id != "kobo":
            return "not_supported"
        raw = self.service.storage.metadata_get(policy_key(source_id))
        return "on_start" if raw == "1" else "manual"

    def bnf_auto_download_enabled(self, source_id: str) -> bool:
        from .core.bnf_auto_materialization import enabled
        account = self._find_account(source_id)
        return enabled(self.service, account)

    def set_bnf_auto_download(self, source_id: str, value: bool) -> bool:
        from .core.bnf_auto_materialization import enabled, policy_key
        account = self._find_account(source_id)
        if type(value) is not bool or account.provider_id != 'bnf-pret':
            raise ValueError('unsupported BnF acquisition policy')
        if value:
            # Check the binding before granting a persistent acquisition mode.
            if not self._enabled(source_id) or not self._account_bound_for_bnf(account):
                raise ValueError('BnF source not eligible')
        # A disabled policy can be re-enabled while a signed GET or ADEPT
        # import is in flight. A final boolean '1' is not authority to revive
        # that older consent: advance a durable opaque generation BEFORE the
        # new enabled/disabled value is published. Even a failed second write
        # invalidates old in-flight work, never grants new work accidentally.
        from secrets import token_hex
        self.service.storage.metadata_set(
            policy_key(account) + '.revision', token_hex(16),
        )
        self.service.storage.metadata_set(policy_key(account), '1' if value else '0')
        self._notify()
        return enabled(self.service, account)

    def _account_bound_for_bnf(self, account: Any) -> bool:
        from .core.bnf_auto_materialization import binding_ready
        return binding_ready(self.service, account)

    def set_local_materialization_schedule(self, source_id: str, mode: str) -> str:
        """Explicit, durable, per-source opt-in. No remote operation.

        There is deliberately no periodic option until a real source-owned
        scheduler exists; no hidden fallback to the old global backup batch.
        """
        from .core.source_materialization_schedule import (
            can_enable_kobo_on_start, policy_key,
        )
        if type(mode) is not str or mode not in {"manual", "on_start"}:
            raise ValueError("unsupported materialization schedule")
        try:
            account = self._find_account(source_id)
        except (ValueError, TypeError):
            raise ValueError("unknown sync source") from None
        if account.provider_id != "kobo":
            raise ValueError("local materialization not supported")
        if mode == "on_start" and not can_enable_kobo_on_start(self.service, account):
            raise ValueError("source not eligible for automatic materialization")
        self.service.storage.metadata_set(
            policy_key(source_id), "1" if mode == "on_start" else "0",
        )
        self._notify()
        return mode

    def synchronize_scheduled_numilog_materialization(
        self, identity_id: str, trigger: str, *, control: Any = None,
    ) -> SyncResult:
        """Run opt-in Numilog materialization independently of source-sync."""
        from .core.numilog_materialization_startup import execute
        return execute(self, identity_id, trigger, control=control)

    def numilog_materialization_run_status(self, source_id: str) -> dict[str, Any]:
        """Passive, privacy-preserving last run and next eligible opening."""
        from .core.numilog_materialization_consent import policy_key
        import re
        if type(source_id) is not str or re.fullmatch(
            r"sync:[0-9a-f]{24}", source_id,
        ) is None:
            raise ValueError("invalid_numilog_source")
        account = self._find_account(source_id)
        if getattr(account, "provider_id", None) != "numilog":
            raise ValueError("not_a_numilog_source")
        # No private library access, directory creation, acquisition or
        # profile resolution: a disconnected owner can inspect history.
        result = self.service.storage.numilog_materialization_run_status(source_id)
        from .core.numilog_startup_diagnostics import read
        detail = read(self.service.storage, source_id)
        if detail:
            result["diagnostic"] = detail
        return result

    def numilog_materialization_schedule(self, source_id: str) -> str:
        """Read an independently confirmed Numilog policy. Defaults manual."""
        from .core.numilog_materialization_consent import mode
        return mode(self, source_id)

    def prepare_numilog_materialization_consent(self, source_id: str) -> SyncResult:
        """Explicit offline preview for opt-in at the next opening."""
        from .core.numilog_materialization_consent import prepare
        return prepare(self, source_id)

    def confirm_numilog_materialization_consent(
        self, source_id: str, confirmation_token: str, *, mode: str,
    ) -> SyncResult:
        from .core.numilog_materialization_consent import confirm
        return confirm(self, source_id, confirmation_token, mode=mode)

    def revoke_numilog_materialization_consent(self, source_id: str) -> str:
        from .core.numilog_materialization_consent import revoke
        return revoke(self, source_id)

    def preflight_numilog_purchase(
        self, source_id: str, item_id: str,
    ) -> SyncResult:
        """Offline preflight for ONE Numilog edition; never an acquire call.

        The opaque scoped item ID is input only, never returned or logged.
        Explicit materialization consent, a fresh private manifest and ADEPT
        handling are separate future execution gates (#1905).
        """
        from .core.numilog_purchase_preflight import preflight, _response
        try:
            account = self._find_account(source_id)
        except (ValueError, TypeError):
            return _response("invalid_source")
        if getattr(account, "provider_id", None) != "numilog":
            return _response("not_supported")
        return preflight(self, account, source_id, item_id)

    def preview_local_materialization(
        self, source_id: str, *, verify_local_files: bool = False,
    ) -> SyncResult:
        """Offline source preview: Kobo manifest or Numilog purchase evidence.

        Numilog is read-only, never a confirmation or an auto-backup policy.
        No credentials, product IDs, local paths or provider messages escape.
        """
        def rejected(reason: str) -> SyncResult:
            return SyncResult(
                ok=False, changed=0, warnings=(reason,),
                facts={"phase": "local_materialization", "status": reason,
                       "read_only": True, "acquisition_authorized": False},
            )

        if type(verify_local_files) is not bool:
            return rejected("invalid_options")
        try:
            account = self._find_account(source_id)
        except (ValueError, TypeError):
            return rejected("unknown_source")
        if not self._enabled(source_id):
            return rejected("source_disabled")
        if getattr(account, "provider_id", None) == "numilog":
            from .core.numilog_local_preview import preview
            return preview(
                self, account, source_id,
                verify_local_files=verify_local_files,
            )
        if str(getattr(account, "provider_id", "")) != "kobo":
            return rejected("not_supported")
        credential = str(getattr(account, "credential_id", "") or "")
        identity = str(getattr(account, "identity_id", "") or "")
        if not credential or not identity:
            return rejected("invalid_source")
        try:
            if self.service.storage.identity_for_connection("kobo", credential) != identity:
                return rejected("invalid_source")
        except (AttributeError, KeyError, ValueError, TypeError):
            return rejected("invalid_source")
        # A session's presence and its declared backup capability are
        # checked; no automatic connect/reconnect is ever attempted here.
        session = getattr(self.service, "_sessions", {}).get(
            self.service._session_key("kobo", credential)
        )
        if not bool(getattr(getattr(session, "capabilities", None), "backup", False)):
            return rejected("not_supported")
        try:
            raw = self.service.kobo_backup_preview(
                credential, refresh_manifest=False,
                verify_local_files=verify_local_files,
            )
        except Exception:
            # Provider/local errors can include bearer URLs and private
            # paths. Never reflect the exception into UI/diagnostic output.
            return rejected("preview_unavailable")
        if (
            not isinstance(raw, dict)
            or raw.get("acquisition_authorized") is not False
            or raw.get("manifest_refreshed") is not False
            or raw.get("requires_manifest_validation") is not True
        ):
            return rejected("preview_unavailable")
        allowed = (
            "already_local_linked", "already_local_verified",
            "needs_reconciliation", "attention_required", "blocked_scope",
            "blocked_unlinked", "not_downloadable", "candidate_unverified",
            "potentially_missing", "invalid_manifest_items",
        )
        values = raw.get("counts")
        if not isinstance(values, dict):
            return rejected("preview_unavailable")
        counts = {}
        for field in allowed:
            value = values.get(field)
            if type(value) is not int or not 0 <= value <= 1_000_000:
                return rejected("preview_unavailable")
            counts[field] = value
        known = raw.get("known_item_count")
        if type(known) is not int or not 0 <= known <= 1_000_000:
            return rejected("preview_unavailable")
        return SyncResult(
            ok=True, changed=0,
            facts={
                "phase": "local_materialization",
                "status": "preview_only",
                "read_only": True, "acquisition_authorized": False,
                "manifest_refreshed": False,
                "requires_manifest_validation": True,
                "files_verified": verify_local_files,
                "known_item_count": known,
                "counts": counts,
            },
        )

    def audit_local_reconciliation(self, source_id: str) -> SyncResult:
        """Run the bounded offline Kobo SHA audit for exactly one source (#1345).

        Unlike a refresh/materialization request, this read-only operation is
        available even while the source is disabled or disconnected. No
        provider, session, manifest, network or acquisition API is called.
        Return only allowlisted totals: original audit cases/paths/identifiers
        must never become a public plugin or GTK payload.
        """
        def rejected(reason: str) -> SyncResult:
            return SyncResult(
                ok=False, changed=0, warnings=(reason,),
                facts={"phase": "reconcile", "status": reason,
                       "read_only": True, "acquisition_authorized": False},
            )

        try:
            account = self._find_account(source_id)
        except (ValueError, TypeError):
            return rejected("unknown_source")
        if getattr(account, "provider_id", None) != "kobo":
            return rejected("not_supported")
        credential = getattr(account, "credential_id", None)
        identity = getattr(account, "identity_id", None)
        if type(credential) is not str or not credential or type(identity) is not str or not identity:
            return rejected("invalid_source")
        try:
            if self.service.storage.identity_for_connection("kobo", credential) != identity:
                return rejected("invalid_source")
        except (AttributeError, KeyError, ValueError, TypeError):
            return rejected("invalid_source")

        try:
            from .core.kobo_local_audit import (
                kobo_local_reconciliation_audit, _TRUNCATION_REASONS,
            )
            raw = kobo_local_reconciliation_audit(
                self.service, credential_id=credential,
            )
        except ValueError as exc:
            # Ambiguous/stale account ownership must be a public fixed code,
            # never a credential or path echoed from a provider or database.
            if str(exc) == "ambiguous_kobo_audit_scope":
                return rejected("ambiguous_source")
            return rejected("audit_unavailable")
        except Exception:
            return rejected("audit_unavailable")

        allowed = (
            "backup_rows", "linked_files_verified", "unverified_placements",
            "missing_or_unlinked", "stored_hash_mismatch",
            "unproven_catalogue_links", "same_product_different_bytes",
            "binary_match_groups", "active_entitlements_unmaterialized",
            "unindexed_regular_file_candidates", "identity_flat_file_candidates",
        )
        counts = raw.get("counts") if isinstance(raw, dict) else None
        accounts = raw.get("accounts") if isinstance(raw, dict) else None
        # #2498: the new shared limits are benign only if the *complete*
        # Core-owned numeric tuple is validated. Older persisted/mock audits
        # have no budget fields and must remain backward compatible.
        budget_keys = (
            "files_hashed_limit", "directory_entries_limit",
            "total_hash_bytes_limit", "total_hash_bytes_read",
        )
        present = [key in raw for key in budget_keys] if isinstance(raw, dict) else []
        budget = {}
        if present and any(present):
            values = tuple(raw.get(key) for key in budget_keys)
            if (
                not all(present)
                or not all(type(value) is int for value in values)
                or not 1 <= values[0] <= 100_000
                or not 1 <= values[1] <= 1_000_000
                or not 1 <= values[2] <= (1 << 40)
                or not 0 <= values[3] <= values[2]
            ):
                return rejected("audit_unavailable")
            budget = dict(zip(budget_keys, values))
        # The new diagnostic is all-or-nothing: old v1 audit fixtures lacking
        # these fields remain valid, but an incomplete/hostile reason list is
        # never interpolated by a consumer. Causes are Core-owned fixed codes,
        # and measured attempts are NOT the count of successful SHA digests.
        reason_keys = (
            "truncation_reasons", "hash_attempts_used",
            "directory_entries_inspected",
        )
        reason_present = [key in raw for key in reason_keys] if isinstance(raw, dict) else []
        reason_facts = {}
        if reason_present and any(reason_present):
            codes, attempts, inspected = (raw.get(key) for key in reason_keys)
            if (
                not all(reason_present) or not budget
                or type(codes) is not list or len(codes) > len(_TRUNCATION_REASONS)
                or any(type(code) is not str or code not in _TRUNCATION_REASONS
                       for code in codes)
                or codes != sorted(set(codes))
                or type(raw.get("truncated")) is not bool
                or bool(codes) != raw["truncated"]
                or type(attempts) is not int
                or not 0 <= attempts <= budget["files_hashed_limit"]
                or type(inspected) is not int
                or not 0 <= inspected <= budget["directory_entries_limit"]
            ):
                return rejected("audit_unavailable")
            reason_facts = {
                "truncation_reasons": list(codes),
                "hash_attempts_used": attempts,
                "directory_entries_inspected": inspected,
            }
        if (
            not isinstance(raw, dict)
            or raw.get("schema") != "kobo.local.reconciliation.audit/1"
            or raw.get("scope") != "single_account"
            or raw.get("offline") is not True
            or raw.get("read_only") is not True
            or raw.get("dry_run") is not True
            or raw.get("downloads_authorized") is not False
            or raw.get("mutations_authorized") is not False
            or type(raw.get("truncated")) is not bool
            or not isinstance(accounts, list) or len(accounts) != 1
            or not isinstance(accounts[0], dict) or accounts[0].get("account") != "account-1"
            or not isinstance(counts, dict) or set(counts) != set(allowed)
            or any(type(counts[name]) is not int or not 0 <= counts[name] <= 1_000_000
                   for name in allowed)
        ):
            return rejected("audit_unavailable")
        return SyncResult(
            ok=True, changed=0,
            facts={
                "phase": "reconcile", "status": "audit_only",
                "read_only": True, "acquisition_authorized": False,
                "offline": True, "truncated": raw["truncated"],
                "counts": {name: counts[name] for name in allowed},
                **budget, **reason_facts,
            },
        )

    def synchronize_source(self, source_id: str, request: SyncRequest | None = None, control: Any = None) -> SyncResult:
        try:
            account = self._find_account(source_id)
        except (ValueError, TypeError):
            # Source IDs are opaque; never reflect an arbitrary caller-supplied
            # string (possibly a credential or URL) into broker error output.
            return SyncResult(ok=False, warnings=("unknown sync source",),
                              facts={"reason": "unknown_source"})
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
            or not isinstance(request.options, Mapping)
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
        # GTK rows are only a snapshot. A plugin, CLI client, stale callback
        # or a session that disconnected after the row was rendered must not
        # bypass the same connection/capability gates at the broker boundary.
        # Rechecking the account ref is local and never reconnects or probes
        # the remote provider. No attempted state is written on rejection.
        current = _account_ref(self.service, account)
        if not current.connected:
            return SyncResult(ok=False, changed=0, warnings=("source disconnected",),
                              facts={"reason": "source_disconnected"})
        if not (set(current.capabilities) & {"loans", "personal_library"}):
            return SyncResult(ok=False, changed=0, warnings=("source sync unsupported",),
                              facts={"reason": "not_supported"})
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state = self._state(source_id)
        # Keep the previous private-inventory evidence when a provider
        # rejects work after the broker's initial enabled/connection check.
        previous_inventory = {
            key: state[key] for key in ("inventory_last_attempt", "inventory_status")
            if key in state
        }
        state.update(last_attempt=now, status="running", error="", scope="manual",
                     identity_id=str(getattr(account, "identity_id", "") or ""))
        # Only a real private-store library operation starts this clock. A
        # generic opening/loan operation must not refresh or throttle it.
        refreshes_private_inventory = (
            getattr(account, "provider_id", None) in {"kobo", "numilog"}
            and "personal_library" in current.capabilities
        )
        if refreshes_private_inventory:
            state.update(inventory_last_attempt=now, inventory_status="running")
        self._set_state(source_id, state); self._notify()
        try:
            result = self.service.synchronize_account_source(
                account, deep=bool(request.deep), control=control,
            )
            # A provider result may include private account identifiers,
            # diagnostic details and free-text errors. Only publish bounded
            # counters and known status/reason codes to plugins and GTK.
            if not isinstance(result, dict):
                state.update(status="error", error=_SYNC_PUBLIC_FAILURE)
                if refreshes_private_inventory:
                    state["inventory_status"] = "failed"
                self._set_state(source_id, state); self._notify()
                return SyncResult(ok=False, warnings=("invalid sync response",),
                                  facts={"reason": "invalid_response"})
            skip_status = result.get("status")
            if type(skip_status) is str and skip_status.lower() == "skipped":
                # The source can be revoked after our local admission check
                # but before the underlying service starts remote inventory.
                # Skipped is not proof of a successful refresh and must not
                # mint last_success or advance the private inventory clock.
                state.update(status="skipped", error="")
                if refreshes_private_inventory:
                    for key in ("inventory_last_attempt", "inventory_status"):
                        if key in previous_inventory:
                            state[key] = previous_inventory[key]
                        else:
                            state.pop(key, None)
                self._set_state(source_id, state); self._notify()
                return SyncResult(ok=True, changed=0, facts={
                    "status": "skipped", "skipped": True,
                })
            facts = {
                name: result[name] for name in _SYNC_PUBLIC_COUNTS
                if type(result.get(name)) is int and 0 <= result[name] <= 1_000_000
            }
            changed = sum(facts.values())
            # Review-required books are NOT modifications and must not
            # inflate SyncResult.changed. Expose only a bounded aggregate,
            # never candidate paths, titles, loan IDs or personal metadata.
            review_count = result.get("files_reconciliation_required")
            if type(review_count) is int and 0 <= review_count <= 1_000_000:
                facts["files_reconciliation_required"] = review_count
            # A freshly revalidated user-declared local file is neither an
            # acquired file nor a changed entitlement. Show the observation
            # without adding it to SyncResult.changed or inventing a receipt.
            declared_count = result.get("files_declared_local")
            if type(declared_count) is int and 0 <= declared_count <= 1_000_000:
                facts["files_declared_local"] = declared_count
            raw_status = result.get("status")
            outcome = raw_status.lower() if type(raw_status) is str else ""
            invalid_status = raw_status not in (None, "") and outcome not in _SYNC_PUBLIC_STATUSES
            if outcome in _SYNC_PUBLIC_STATUSES:
                facts["status"] = outcome
            incomplete = (invalid_status
                          or outcome in {"partial", "failed", "error", "attention_required",
                                         "retryable_error"}
                          or bool(result.get("errors"))
                          or bool(result.get("attention_required"))
                          or bool(result.get("warnings")))
            if incomplete:
                facts["reason"] = "invalid_response" if invalid_status else "source_partial"
                state.update(status="partial", error="Synchronisation partielle")
                if refreshes_private_inventory:
                    state["inventory_status"] = "partial"
                self._set_state(source_id, state); self._notify()
                return SyncResult(ok=False, changed=changed,
                                  warnings=("synchronisation partielle",), facts=facts)
            success_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            state.update(last_success=success_at, status="ok", error="")
            if refreshes_private_inventory:
                state.update(
                    inventory_last_success=success_at, inventory_status="success",
                )
            self._set_state(source_id, state); self._notify()
            return SyncResult(ok=True, changed=changed, facts=facts)
        except Exception:
            state.update(status="error", error=_SYNC_PUBLIC_FAILURE)
            if refreshes_private_inventory:
                state["inventory_status"] = "failed"
            self._set_state(source_id, state); self._notify()
            return SyncResult(ok=False, warnings=(_SYNC_PUBLIC_FAILURE,),
                              facts={"reason": "source_unavailable"})

    def synchronize(self, request: SyncRequest, control: Any = None) -> SyncResult:
        # An omitted identity must never turn a nominal "Sync" into an
        # unpreviewed batch across all provider accounts.
        if not isinstance(request, SyncRequest) or (
            type(request.identity_id) is not str or not request.identity_id
        ):
            return SyncResult(ok=False, changed=0, warnings=("explicit identity required",),
                              facts={"reason": "identity_required"})
        if (
            type(request.deep) is not bool or type(request.scopes) is not tuple
            or request.scopes or not isinstance(request.options, Mapping)
            or bool(request.options)
        ):
            return SyncResult(ok=False, changed=0, warnings=("unsupported batch scope",),
                              facts={"reason": "unsupported_scope"})
        sources = [row for row in self.sources(request.identity_id) if row.enabled and row.connected and row.sync_capable]
        total = 0; warnings: list[str] = []; facts: dict[str, Any] = {"sources": {}}
        for index, source in enumerate(sources, 1):
            if control is not None and callable(getattr(control, "check_cancelled", None)):
                control.check_cancelled()
            if control is not None and callable(getattr(control, "report", None)):
                control.report(current=index - 1, total=len(sources), message="Synchronisation d’une source")
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
        """Recent structured events from the one persistent journal."""
        from .core.diagnostic_journal import recent_diagnostics_snapshot
        from .paths import log_file
        return recent_diagnostics_snapshot(log_file())

    def events(
        self, *, source: str = "journal", module: str = "", severity: str = "",
        provider_id: str = "", domain: str = "",
        since: str | None = None, until: str | None = None,
        limit: int = 50, offset: int = 0, reason_code: str = "",
        search: str = "", sort_order: str = "asc",
    ) -> dict[str, Any]:
        """Read only the canonical physical journal, never the memory cache.

        Historical source spellings are temporary aliases, not distinct feeds
        and cannot cause another read or duplicate physical occurrences.
        """
        from .core.diagnostic_journal import query_journal
        from .paths import log_file

        if source not in ("journal", "all", "core_events", "app_log"):
            raise ValueError("unknown diagnostic source")
        if domain or reason_code:
            raise ValueError(
                "legacy domain/reason filters unavailable on canonical journal"
            )
        return query_journal(
            log_file(), search=search, module=module, severity=severity,
            provider_id=provider_id, since=since, until=until,
            limit=limit, offset=offset, sort_order=sort_order,
        )

    def http_trace_status(self):
        from .http_trace import HTTP_TRACE
        return HTTP_TRACE.status()

    def export_http_trace(self, destination: str) -> str:
        from .http_trace import HTTP_TRACE
        return str(HTTP_TRACE.export(destination))

    def export_raw_journal(self) -> bytes:
        """Explicit local export; no provider interaction, no GUI masking."""
        from .core.diagnostic_journal import export_raw_journal
        from .paths import log_file
        return export_raw_journal(log_file())

    def system_status(self):
        from .system_status import system_status
        return system_status(getattr(self.service, "plugin_manager", None))

    def inventory(self) -> tuple[dict[str, Any], ...]:
        """Return the versions and states of the plugin manifests actually loaded."""
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
            rows.append(row)
        return tuple(rows)

    def runtime_diagnostic(self, plugin_id: str) -> dict[str, Any]:
        """Expose bounded process metadata without changing its original values."""
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
                bounded[key] = value[:240]
            elif isinstance(value, (int, float, bool)) or value is None:
                bounded[key] = value
        return bounded

    def active_checks(self) -> tuple[HealthCheckResult, ...]:
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
                    {
                        "provider_id": source.provider_id,
                        "connected": connected,
                        "sync_capable": capable,
                        "enabled": bool(source.enabled),
                        "last_success": source.last_success,
                        "error": error[:240] if error else "",
                    },
                    "core",
                ))
        except Exception:
            rows.append(HealthCheckResult(
                "core.sync.sources",
                "sync",
                "error",
                "Sources configurées",
                {"code": "sync_sources_unavailable"},
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
        except Exception:
            rows.append(HealthCheckResult(
                "core.providers.loaded",
                "provider.library",
                "error",
                "Providers chargés",
                {"code": "providers_unavailable"},
                "core",
            ))

        try:
            contributions = tuple(manager.services("diagnostics.check"))
        except Exception:
            rows.append(HealthCheckResult(
                "core.diagnostics.contributions",
                "diagnostics.check",
                "error",
                "Vérifications plugins",
                {"code": "diagnostic_contributions_unavailable"},
                "core",
            ))
            contributions = ()

        allowed_status = {"ok", "warning", "error", "unknown"}
        for resolved in contributions:
            try:
                checks = tuple(resolved.service.checks())
            except Exception:
                rows.append(HealthCheckResult(
                    f"{resolved.plugin_id}.check",
                    resolved.plugin_id,
                    "error",
                    resolved.plugin_id,
                    {"code": "diagnostic_check_failed"},
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
                        {"code": "invalid_diagnostic_result"},
                        "plugin",
                    ))
                    continue
                status = item.status if item.status in allowed_status else "unknown"
                rows.append(HealthCheckResult(
                    str(item.check_id or resolved.plugin_id)[:160],
                    str(item.component or resolved.plugin_id)[:160],
                    status,
                    str(item.message or item.component or resolved.plugin_id)[:300],
                    dict(item.detail or {}),
                    str(item.scope or "plugin")[:80],
                ))
        return tuple(rows)

    def export_text(self) -> str:
        """Legacy text endpoint, backed by the unmodified persistent journal."""
        from . import __version__
        from .core.diagnostic_journal import journal_text_export
        from .paths import log_file
        return journal_text_export(log_file(), version=__version__)

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
    from .http_trace import HTTPX_TRANSPORT
    from .core.release_update_read import ReleaseUpdateReadBrokerImpl
    from .core.library_audit_broker import LibraryReconciliationReadBrokerImpl
    artifact_execution = ArtifactExecutionBrokerImpl(service)
    return {
        # Deliberately read-only: the trusted updater transaction has no
        # public plugin entrypoint until byte provenance and locks are proven.
        "release.update.read": ReleaseUpdateReadBrokerImpl(),
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
        "httpx": HTTPX_TRANSPORT,
        "library.naming": LibraryNamingBrokerImpl(service),
        "artifact.execution": artifact_execution,
        "library.read": LibraryReadBrokerImpl(service),
        "library.reconciliation.read": LibraryReconciliationReadBrokerImpl(service),
        "library.maintenance": LibraryMaintenanceBrokerImpl(service),
        "artifact.routing": ArtifactRoutingBrokerImpl(service, artifact_execution),
        "cache": CacheBrokerImpl(service),
        "tasks": TaskBrokerImpl(service),
    }
