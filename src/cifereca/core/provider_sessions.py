"""Internal provider session/collection coordination over the existing facade.

The facade owns state, locks, registry and subscriptions. Functions receive
that reference explicitly; worker executor and trace logger are injected at
call time to preserve the facade patchpoints. No second scheduler or cache.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from ..models import Account
from ..providers import ProviderConfigurationError, ProviderSession
from .public_provider_diagnostics import (
    CONNECT_FAILURE_CODE, MISSING_PROVIDER_CODE,
    public_connection_failure, bounded_connection_failures,
    registered_public_provider_id, UNKNOWN_PROVIDER,
)

def attach_plugin_manager(self, manager: Any) -> list[str]:
    """Attach Plugin API services, preferring external providers/runtimes when ready."""
    with self._virtual_reconcile_lock:
        self._unbind_virtual_subscriptions()
        self._virtual_collection_closed = False
        self._plugin_manager = manager
    attached: list[str] = []
    try:
        statuses = manager.candidates("provider.library")
    except Exception:
        return attached
    from ..providers.base import ProviderPlugin
    for status in statuses:
        service = manager.service(status.descriptor.id)
        if isinstance(service, ProviderPlugin):
            self.registry.register(service, replace=True)
            self._provider_plugin_ids[service.descriptor.id] = status.descriptor.id
            attached.append(service.descriptor.id)
    attached.extend(self.refresh_virtual_collections())
    binder = getattr(manager, "subscribe_services", None)
    if callable(binder):
        try:
            unsub = binder("provider.collection", self._queue_virtual_collection_refresh)
            if callable(unsub):
                self._virtual_manager_unsubscribe = unsub
        except Exception:
            self.diagnostics.add(
                "PLUGIN", "virtual service subscription", "error",
                detail={"code": "virtual_service_subscription_failed"}, level="detailed",
            )
    if attached:
        self.diagnostics.add("PLUGIN", "external providers attached", "ok", detail={"providers": attached}, level="detailed")
    return attached


def _unbind_virtual_subscriptions(self) -> None:
    """Disconnect previous parent/manager callbacks on replacement or close."""
    unsubscribers = [
        *(unsub for _, unsub in self._virtual_parent_subscriptions.values()),
        self._virtual_manager_unsubscribe,
    ]
    self._virtual_parent_subscriptions.clear()
    self._virtual_manager_unsubscribe = None
    for unsubscribe in unsubscribers:
        if callable(unsubscribe):
            try:
                unsubscribe()
            except Exception:
                pass


def _sync_virtual_subscriptions(self, services: dict[str, object]) -> None:
    for parent, (old_service, unsubscribe) in list(self._virtual_parent_subscriptions.items()):
        if services.get(parent) is old_service:
            continue
        self._virtual_parent_subscriptions.pop(parent, None)
        try:
            unsubscribe()
        except Exception:
            pass
    for parent, service in services.items():
        if parent in self._virtual_parent_subscriptions:
            continue
        subscribe = getattr(service, "subscribe", None)
        if not callable(subscribe):
            continue
        try:
            unsubscribe = subscribe(self._queue_virtual_collection_refresh)
            if callable(unsubscribe):
                self._virtual_parent_subscriptions[parent] = (service, unsubscribe)
        except Exception:
            self.diagnostics.add(
                "PLUGIN", "virtual inventory subscription", "error",
                detail={"parent": parent, "code": "virtual_inventory_subscription_failed"},
                level="detailed",
            )


def _queue_virtual_collection_refresh(self, *, executor) -> None:
    """Coalesce bursts; re-read the latest cached snapshot on a Core worker."""
    with self._virtual_schedule_lock:
        if self._virtual_collection_closed:
            return
        if self._virtual_refresh_pending:
            self._virtual_refresh_again = True
            return
        self._virtual_refresh_pending = True
    try:
        executor.submit(self._drain_virtual_collection_events)
    except RuntimeError:
        with self._virtual_schedule_lock:
            self._virtual_refresh_pending = False


def _drain_virtual_collection_events(self) -> None:
    while True:
        with self._virtual_schedule_lock:
            if self._virtual_collection_closed:
                self._virtual_refresh_pending = False
                return
            self._virtual_refresh_again = False
        try:
            self.refresh_virtual_collections(connect_changed=True)
        except Exception:
            self.diagnostics.add(
                "PLUGIN", "virtual notification", "error",
                detail={"code": "virtual_notification_failed"}, level="detailed",
            )
        with self._virtual_schedule_lock:
            if not self._virtual_refresh_again or self._virtual_collection_closed:
                self._virtual_refresh_pending = False
                return


def subscribe_virtual_sources(self, callback: Callable[[], None]) -> Callable[[], None]:
    """Observe accepted inventory changes. Callback runs on the caller/worker.

    GTK consumers must marshal UI changes using GLib.idle_add.
    Invalid snapshots and duplicate notifications do not emit an update.
    """
    with self._virtual_reconcile_lock:
        if callback not in self._virtual_change_callbacks:
            self._virtual_change_callbacks.append(callback)

    def unsubscribe() -> None:
        with self._virtual_reconcile_lock:
            if callback in self._virtual_change_callbacks:
                self._virtual_change_callbacks.remove(callback)
    return unsubscribe


def _connect_changed_virtual_provider(self, provider_id: str) -> None:
    """Connect one published virtual adapter with one attested request lifetime.

    This activation path is independent of the configured startup sweep.
    Never publish a successful session from a non-affirmative connect().
    """
    from .diagnostic_provider_connection_attempt import new_attempt
    attempt = new_attempt(
        self.registry, provider_id, diagnostics=self.diagnostics,
        source_kind="virtual", caller_category="plugin_activation",
    )
    plugin = self.registry.maybe_get(provider_id)
    if plugin is None:
        attempt.emit(self.diagnostics, "terminal", "skipped", "plugin_missing")
        return
    key = self._session_key(provider_id, "__public__")
    existing = self._sessions.get(key)
    if (existing is not None
            and getattr(existing, "connected", None) is True
            and getattr(existing, "provider_id", None) == provider_id):
        attempt.emit(self.diagnostics, "session_reused", "success", "session_reused")
        attempt.emit(self.diagnostics, "terminal", "success", "connected")
        return
    # A stale entry must never count as reuse or remain selectable after a
    # failed replacement. Do not let its close() exception prevent recovery.
    if existing is not None:
        self._sessions.pop(key, None)
        try:
            existing.close()
        except Exception:
            pass
    session = None
    invoked = False
    try:
        session = plugin.create_session(
            credential_id="__public__", label=plugin.descriptor.display_name,
            credentials={}, options={}, config=self.config, storage=self.storage,
            diagnostics=self.diagnostics,
        )
        if getattr(session, "provider_id", None) != provider_id:
            raise ProviderConfigurationError("virtual session provider mismatch")
        invoked = True
        attempt.emit(self.diagnostics, "connect_invoked", "unknown", "connect_invoked")
        session.connect()
        if getattr(session, "connected", None) is not True:
            raise ProviderConfigurationError("virtual session not connected")
        self._sessions[key] = session
        self._session_errors.pop(key, None)
        attempt.emit(self.diagnostics, "terminal", "success", "connected")
    except Exception:
        attempt.emit(
            self.diagnostics, "terminal", "failed",
            "connect_failed" if invoked else "session_invalid",
        )
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        self._session_errors[key] = "virtual_provider_connect_failed"
        self.diagnostics.add(
            "PLUGIN", "virtual provider connection", "error",
            detail={"provider": provider_id, "code": "virtual_provider_connect_failed"},
            level="detailed",
        )


def refresh_virtual_collections(self, *, connect_changed: bool=False) -> list[str]:
    """Reconcile cached inventories without any implicit discovery HTTP.

    Notifications invoke this on a worker and connect only added/replaced
    children. An explicit call can defer connections to connect_configured.
    Unrelated provider sessions and local catalogue/history remain intact.
    """
    with self._virtual_reconcile_lock:
        if self._virtual_collection_closed:
            return []
        manager = self._plugin_manager
        if manager is None:
            return []
        try:
            statuses = manager.candidates("provider.collection")
        except Exception:
            return []
        before = dict(self._virtual_parent_by_provider)
        previous_plugins = {
            provider_id: self.registry.maybe_get(provider_id)
            for provider_id in before
        }
        previous_snapshots = {
            parent: self.registry.virtual_snapshot(parent)
            for parent in set(before.values())
        }
        live_parents: set[str] = set()
        active_services: dict[str, object] = {}
        attached: list[str] = []
        for status in statuses:
            parent_id = status.descriptor.id
            live_parents.add(parent_id)
            service = manager.service(parent_id)
            active_services[parent_id] = service
            persisted = None
            try:
                persisted = self._virtual_snapshot_store.load(parent_id)
            except Exception:
                # Corrupt/tampered disk data must never become providers.
                self.diagnostics.add(
                    "PLUGIN", "saved virtual inventory invalid", "error",
                    detail={"parent": parent_id, "code": "virtual_inventory_checkpoint_invalid"},
                    level="detailed",
                )
            try:
                children = self.registry.reconcile_collection(
                    parent_id, service, minimum_snapshot=persisted,
                )
            except Exception:
                self.diagnostics.add(
                    "PLUGIN", "virtual provider inventory rejected", "error",
                    detail={"parent": parent_id, "code": "virtual_inventory_rejected"},
                    level="detailed",
                )
                # Prefer the latest in-memory valid inventory; otherwise
                # recover the last validated SQLite checkpoint. Do not
                # replace it or erase books if discovery is unavailable.
                known = self.registry.virtual_snapshot(parent_id)
                if known is not None:
                    # No new inventory was accepted: keep existing adapters
                    # but do not report a publication or a false change.
                    continue
                if persisted is None:
                    continue
                try:
                    children = self.registry.reconcile_collection(
                        parent_id, service, snapshot_override=persisted,
                        minimum_snapshot=persisted,
                    )
                except Exception:
                    self.diagnostics.add(
                        "PLUGIN", "virtual inventory restore failed", "error",
                        detail={"parent": parent_id,
                                "code": "virtual_inventory_restore_failed"},
                        level="detailed",
                    )
                    continue
                self.diagnostics.add(
                    "PLUGIN", "virtual inventory restored from SQLite", "ok",
                    detail={"parent": parent_id,
                            "generation": persisted.discovery_generation},
                    level="detailed",
                )
            else:
                accepted = self.registry.virtual_snapshot(parent_id)
                # A failed disk write never rolls back a valid in-memory
                # publication. SQLite is the durable checkpoint, however,
                # so only report success after metadata_set returns.
                if accepted is not None and accepted != persisted:
                    try:
                        self._virtual_snapshot_store.save(accepted)
                    except Exception:
                        self.diagnostics.add(
                            "PLUGIN", "virtual inventory checkpoint failed", "error",
                            detail={"parent": parent_id,
                                    "code": "virtual_inventory_checkpoint_failed"},
                            level="detailed",
                        )
            attached.extend(children)
        for parent in set(before.values()) - live_parents:
            self.registry.retire_collection(parent)
        self._sync_virtual_subscriptions(active_services)
        new_mapping = {
            provider_id: parent
            for parent in live_parents
            for provider_id in self.registry.virtual_ids(parent)
        }
        stale_ids = {
            pid for pid, old in previous_plugins.items()
            if pid not in new_mapping or self.registry.maybe_get(pid) is not old
        }
        for old_id in stale_ids:
            for key, session in list(self._sessions.items()):
                if session.provider_id == old_id:
                    self._sessions.pop(key, None)
                    try:
                        session.close()
                    except Exception:
                        pass
            if old_id not in new_mapping:
                self._provider_plugin_ids.pop(old_id, None)
        for provider_id, parent in new_mapping.items():
            self._provider_plugin_ids[provider_id] = parent
        self._virtual_parent_by_provider = new_mapping
        if connect_changed:
            for provider_id in sorted(new_mapping):
                if (provider_id in stale_ids
                        or self._session_key(provider_id, "__public__") not in self._sessions):
                    self._connect_changed_virtual_provider(provider_id)
        # A catalog-generation increment invalidates search cache keys even
        # when the Python adapter itself has not changed.
        snapshots_changed = any(
            self.registry.virtual_snapshot(parent) != previous_snapshots.get(parent)
            for parent in live_parents | set(previous_snapshots)
        )
        changed = bool(stale_ids or set(new_mapping) != set(before) or snapshots_changed)
        listeners = tuple(self._virtual_change_callbacks) if changed else ()
    for callback in listeners:
        try:
            callback()
        except Exception:
            self.diagnostics.add(
                "PLUGIN", "virtual inventory observer", "error",
                detail={"code": "virtual_inventory_observer_failed"}, level="detailed",
            )
    return attached


def _provider_plugin_status(self, provider_id: str) -> Any | None:
    manager = self._plugin_manager
    plugin_id = self._provider_plugin_ids.get(str(provider_id))
    if manager is None or not plugin_id:
        return None
    try:
        return manager.status(plugin_id)
    except Exception:
        return None


def _provider_enabled_for_identity(self, provider_id: str, identity_id: str | None=None) -> bool:
    status = self._provider_plugin_status(provider_id)
    if status is None:
        return str(provider_id) not in self._provider_plugin_ids
    if not status.enabled or status.state != "ready":
        return False
    if (
        provider_id not in self._virtual_parent_by_provider
        and getattr(status.descriptor, "instantiation", "multiple") != "singleton_public"
    ):
        return True
    identity = identity_id or (self.current_identity.identity_id if self.current_identity else "default")
    try:
        if not self._plugin_manager.enabled_for_identity(status.descriptor.id, identity):
            return False
        if provider_id in self._virtual_parent_by_provider:
            state = getattr(self._plugin_manager, "state", None)
            if state is not None:
                override = state.identity_enabled_override(provider_id, identity)
                return True if override is None else bool(override)
        return True
    except Exception:
        return False


def provider_descriptors(self):
    return self.registry.descriptors()


def provider_diagnostic_state(self) -> dict[str, Any]:
    """Expose typed session state, never call an untrusted plugin's serializer.

    A plugin diagnostic_state() can contain arbitrary account identifiers,
    tokens, paths or URLs. Only Core-observed booleans and counts are needed
    for the overview. Fine-grained redacted logs belong to Diagnostics.
    """
    try:
        adept = self.kobo_adept_state()
    except Exception:
        adept = {"available": False, "error": "ADEPT indisponible"}
    sessions = []
    for session in self._sessions.values():
        try:
            if getattr(session, "credential_id", None) == "legacy":
                continue
            accounts = getattr(session, "_accounts", None)
            sessions.append({
                "connected": getattr(session, "connected", None) is True,
                "accounts": min(10000, len(accounts)) if type(accounts) in (list, tuple) else 0,
            })
        except Exception:
            sessions.append({"connected": False, "accounts": 0})
    return {
        "plugins": self.registry.diagnostic_state(),
        "sessions": sessions,
        "errors": {"connect_failed": min(10000, len(self._session_errors))},
        # Separate safe projection; legacy private errors remain internal.
        "connection_failures": bounded_connection_failures(
            getattr(self, "_connect_failures_public", ()),
        ),
        "kobo_adept": adept,
    }


def close(self) -> None:
    self._source_context_epoch = getattr(self, "_source_context_epoch", 0) + 1
    with self._virtual_schedule_lock:
        self._virtual_collection_closed = True
    with self._virtual_reconcile_lock:
        self._unbind_virtual_subscriptions()
        self._virtual_change_callbacks.clear()
    seen: set[int] = set()
    for session in list(self._sessions.values()):
        if id(session) in seen:
            continue
        seen.add(id(session))
        try:
            session.close()
        except Exception:
            pass
    try:
        self.storage.close()
    except Exception:
        pass


def disconnect_configured(self) -> None:
    self._source_context_epoch = getattr(self, "_source_context_epoch", 0) + 1
    for key, session in list(self._sessions.items()):
        try:
            session.close()
        except Exception:
            pass
        self._sessions.pop(key, None)
    self._session_errors = {}
    self._connect_failures_public = ()
    self._active_session_key = None
    self.primary = self._local_account()
    self.accounts = [self.primary]
    self.current_account = self.primary
    self.last_loans = []
    self.last_history = []
    self.last_bookings = []



def connect_kobo_preview(self, credential_id: str) -> ProviderSession:
    """Connect only the requested Kobo credential for an opt-in manifest preview.

    Unlike connect_configured(), this never touches other providers, account
    refreshes, or SQLite connection/backup records. Offline preview does not
    call this helper at all. The opened session is owned by LibraryService.
    """
    credential_id = str(credential_id or "").strip()
    bound_identity = (
        self.storage.identity_for_connection("kobo", credential_id)
        if credential_id else None
    )
    if not credential_id or not bound_identity:
        raise ProviderConfigurationError("Compte Kobo demandé non configuré")
    row = next(
        (
            row for row in self.config.provider_accounts(enabled_only=True)
            if str(row.get("provider") or "").strip().lower() == "kobo"
            and str(row.get("id") or "").strip() == credential_id
        ),
        None,
    )
    if row is None:
        raise ProviderConfigurationError("Compte Kobo désactivé ou non configuré")
    plugin = self.registry.maybe_get("kobo")
    if plugin is None or plugin.descriptor.state != "ready":
        raise ProviderConfigurationError("Provider Kobo indisponible")
    from .diagnostic_provider_connection_attempt import new_attempt
    attempt = new_attempt(
        self.registry, "kobo", diagnostics=self.diagnostics,
        identity_id=bound_identity, credential_id=credential_id,
        source_kind="preview", caller_category="explicit_refresh",
    )
    key = self._session_key("kobo", credential_id)
    existing = self._sessions.get(key)
    if existing is not None and existing.connected:
        if existing.provider_id != "kobo" or existing.credential_id != credential_id:
            attempt.emit(self.diagnostics, "terminal", "failed", "session_invalid")
            raise ProviderConfigurationError("Session Kobo différente du compte demandé")
        attempt.emit(self.diagnostics, "session_reused", "success", "session_reused")
        attempt.emit(self.diagnostics, "terminal", "success", "connected")
        return existing
    session = plugin.create_session(
        credential_id=credential_id,
        label=str(row.get("label") or plugin.descriptor.display_name),
        credentials=dict(row.get("credentials") or {}),
        options=dict(row.get("options") or {}),
        config=self.config, storage=self.storage, diagnostics=self.diagnostics,
    )
    try:
        attempt.emit(self.diagnostics, "connect_invoked", "unknown", "connect_invoked")
        session.connect()
        if (not session.connected or session.provider_id != "kobo"
                or session.credential_id != credential_id
                or not getattr(session.capabilities, "backup", False)):
            raise ProviderConfigurationError("Session Kobo sans capacité de sauvegarde")
    except Exception:
        attempt.emit(self.diagnostics, "terminal", "failed", "connect_failed")
        try:
            session.close()
        except Exception:
            pass
        raise
    if existing is not None:
        try:
            existing.close()
        except Exception:
            pass
    self._sessions[key] = session
    attempt.emit(self.diagnostics, "terminal", "success", "connected")
    return session


def connect_kobo_canonical(self, credential_id: str) -> ProviderSession:
    """Bind one *confirmed* Kobo CLI source to its exact persisted Core owner.

    Preview alone must never register an Account. Complete A→B execution
    requires one live Account matching an enabled configured credential and
    an exact SQLite borrower. No entitlement or provider-account row is
    created here; all later rights/offer/commit stages re-admit their scope.
    """
    from .canonical_delivery_abi import has_complete_delivery_abi

    # The existing connector authenticates only this selected credential.
    # For an incomplete ABI retain the old (non-canonical) routing unchanged.
    session = connect_kobo_preview(self, credential_id)
    if not has_complete_delivery_abi(session):
        return session
    key = self._session_key("kobo", credential_id)
    roster = session.accounts
    if (not isinstance(roster, (list, tuple))
            or len(roster) != 1 or type(roster[0]) is not Account
            or session.connected is not True
            or session.provider_id != "kobo"
            or session.credential_id != credential_id
            or self._sessions.get(key) is not session):
        raise ProviderConfigurationError("kobo_canonical_account_unavailable")
    account = roster[0]
    original_scope = (
        account.identity_id, account.provider_id,
        account.credential_id, account.unique_id,
    )
    provider_epoch = getattr(session, "_epoch", None)
    core_epoch = getattr(self, "_source_context_epoch", 0)
    registry = self.accounts
    if type(registry) is not list:
        raise ProviderConfigurationError("kobo_canonical_account_unavailable")
    registry_snapshot = tuple(registry)
    owner = self.storage.identity_for_connection("kobo", credential_id)
    # A credential rebound in Config but not in SQLite must not inherit the
    # old account's consent. Missing or multiple enabled rows are ambiguous.
    try:
        configured_rows = [
            row for row in self.config.provider_accounts(enabled_only=True)
            if type(row) is dict
            and str(row.get("provider") or "").strip().lower() == "kobo"
            and str(row.get("id") or "").strip() == credential_id
        ]
        configured_owner = (
            str(configured_rows[0].get("identity_id") or (
                self.current_identity.identity_id
                if self.current_identity else "default"
            )).strip() if len(configured_rows) == 1 else None
        )
    except Exception:
        configured_owner = None
    if (type(owner) is not str or not owner
            or configured_owner != owner
            or account.provider_id != "kobo"
            or account.credential_id != credential_id
            or type(account.unique_id) is not str
            or account.identity_id not in {owner, "default"}
            or self.identity_by_id(owner) is None
            or self.storage.provider_scope_account_bound(
                identity_id=owner, provider_id="kobo",
                credential_id=credential_id, account_id=account.unique_id,
            ) is not True):
        raise ProviderConfigurationError("kobo_canonical_account_unavailable")
    # In particular do not replace the local/Paris primary account merely
    # because this one confirmed Kobo command needs a scoped reader.
    existing = [
        row for row in registry
        if getattr(row, "provider_id", None) == "kobo"
        and getattr(row, "credential_id", None) == credential_id
    ]
    if (len(existing) > 1 or (
            existing and (
                type(existing[0]) is not Account
                or existing[0].identity_id != owner
                or existing[0].unique_id != account.unique_id
            ))):
        raise ProviderConfigurationError("kobo_canonical_account_ambiguous")
    # Recheck mutable objects after SQLite/config IO and before any Core
    # mutation. The later canonical executor separately rechecks owner,
    # session epoch, borrower and source-wide CAS consent before GET/commit.
    if (self._sessions.get(key) is not session
            or getattr(session, "_epoch", None) != provider_epoch
            or session.connected is not True
            or self.accounts is not registry
            or len(registry) != len(registry_snapshot)
            or any(row is not old for row, old in zip(registry, registry_snapshot))
            or getattr(self, "_source_context_epoch", 0) != core_epoch
            or len(session.accounts) != 1
            or session.accounts[0] is not account
            or (
                account.identity_id, account.provider_id,
                account.credential_id, account.unique_id,
            ) != original_scope
            or len([
                row for row in registry
                if getattr(row, "provider_id", None) == "kobo"
                and getattr(row, "credential_id", None) == credential_id
            ]) != len(existing)):
        raise ProviderConfigurationError("kobo_canonical_account_changed")
    account.identity_id = owner
    if not existing:
        registry.append(account)
    self._source_context_epoch = core_epoch + 1
    return session


def connect_kobo_selected_edition(self, credential_id: str) -> ProviderSession:
    """Compatibility API: selected-edition and resume share one safe bind."""
    return connect_kobo_canonical(self, credential_id)


# Closed reason vocabulary: provider exception text, types, URLs and tokens
# are NEVER forwarded into the raw journal. A recognized provider error.code
# can explain a failed authentication without exposing its response body.
_PUBLIC_CONNECTION_CAUSES = frozenset({
    "missing_token", "invalid_token", "insufficient_scope", "forbidden",
    "network_error", "server_error", "rate_limited", "redirect_denied",
    "invalid_response", "http_error", "conflict",
})


def _public_connection_cause(error: Exception) -> str:
    try:
        code = getattr(error, "code", None)
    except Exception:
        return "unclassified"
    return code if type(code) is str and code in _PUBLIC_CONNECTION_CAUSES else "unclassified"


def _publish_connection_failure(diagnostics: Any, failure: dict[str, str], *,
                                singleton: bool = False, duration_ms: int | None = None) -> None:
    """Prefer the Core-attested producer; legacy diagnostics mocks stay usable."""
    publish = getattr(diagnostics, "add_core_provider_connect_failure", None)
    if callable(publish):
        # The registry has already classified this outcome. Do not lose the
        # distinct provider_not_registered code before the shared emission.
        publish(failure["provider_id"], singleton=singleton,
                duration_ms=duration_ms, code=failure["code"])
    else:
        diagnostics.add(
            "PLUGIN", "public provider connect" if singleton else "provider connect",
            "error", detail=failure,
        )


def _publish_connection_success(
    diagnostics: Any, registry: Any, provider_id: object, *,
    singleton: bool, account_count: int, duration_ms: int,
) -> None:
    """Observe only a Core-registered, completed connection.

    Best effort: logging cannot change an actual successful provider result.
    Do not expose credential IDs, identity IDs or labels to diagnostics.
    """
    try:
        slug = registered_public_provider_id(registry, provider_id)
        publish = getattr(diagnostics, "add_core_provider_connect_success", None)
        if slug != UNKNOWN_PROVIDER and callable(publish):
            publish(slug, singleton=singleton, account_count=account_count,
                    duration_ms=duration_ms)
    except Exception:
        pass


def _configured_rows_for_source(rows, *, source_scope, default_identity):
    """Select one enabled configured credential without provider/network I/O."""
    if source_scope is None:
        return rows
    if (type(source_scope) is not tuple or len(source_scope) != 3
            or any(type(v) is not str or not v or len(v) > 256
                   or any(c in v for c in "\x00\r\n")
                   for v in source_scope)):
        raise ValueError("selected_source_invalid_scope")
    identity, provider, credential = source_scope
    matches = [
        row for row in rows if isinstance(row, dict)
        and str(row.get("identity_id") or default_identity).strip() == identity
        and str(row.get("provider") or "").strip().lower() == provider
        and str(row.get("id") or "").strip() == credential
    ]
    if len(matches) != 1:
        raise ValueError("selected_source_not_unique")
    return matches


def _opening_sync_covers_visible_accounts(self) -> bool:
    """True only if the next opening Sync will inventory every visible source.

    A source can be connected for consultation yet excluded from Sync. When
    the enabled-state check is unavailable or ambiguous, keep the established
    forced refresh rather than silently withholding its loans.
    """
    try:
        identity_id = self.current_identity.identity_id
        accounts = self.accounts_for_identity(identity_id)
        return all(self.sync_source_enabled(account) is True for account in accounts)
    except Exception:
        return False


def _record_configured_connect_phase(logger, attempt, phase, started, outcome):
    """Closed wall timing of one configured provider, not an HTTP timer.

    Connect may include several supplier requests or local work. An
    operation_ref links the phase to the existing diagnostic attempt without
    serializing the account, credential, request or exception payload.
    """
    if phase not in {"create_session", "session_connect", "account_registration"}:
        return
    if outcome not in {"returned", "raised"}:
        return
    try:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if not 0 <= elapsed_ms <= 600_000:
            return
        logger.info(
            "EVENT provider connect_phase operation_ref=%s provider_id=%s "
            "phase=%s outcome=%s duration_ms=%s",
            attempt.operation_ref, attempt.provider_id, phase, outcome,
            elapsed_ms,
        )
    except Exception:
        pass  # optional telemetry cannot change provider connection semantics


def _interactive_background_opted_in(descriptor: object, row: object) -> bool:
    """Exact per-account consent for a declared HTTP-capable interactive source.

    The option name comes from the provider's public descriptor; Core never
    recognizes an individual provider ID. Old interactive providers remain
    skipped. A truthy value, missing option, or undeclared field is NOT consent.
    """
    key = getattr(descriptor, "background_authentication_option_key", None)
    if (type(key) is not str or not key or len(key) > 128
            or type(row) is not dict):
        return False
    specs = getattr(descriptor, "account_options", ())
    if not (type(specs) is tuple and any(
        getattr(spec, "key", None) == key for spec in specs
    )):
        return False
    # Defense in depth, even if Config.provider_accounts(enabled_only=True)
    # was bypassed by an adapter: never connect a disabled/malformed source.
    if row.get("enabled", True) is not True:
        return False
    opts = row.get("options")
    return type(opts) is dict and opts.get(key) == "on"



def reconnect_expired_configured_source_once(
    self, account: Account, expired_session: ProviderSession,
) -> Account | None:
    """One fresh CAS authentication only after a typed expired-session event.

    The normal configured connection sweep is deliberately forbidden here:
    it disconnects *all* accounts. This helper swaps only one exact existing
    connection and never authorizes an interactive provider without its own
    persisted, exact background-authentication opt-in.
    """
    if type(account) is not Account or type(expired_session) is not ProviderSession:
        # A subclass of the genuine installed ProviderSession is expected.
        if (type(account) is not Account
                or not isinstance(expired_session, ProviderSession)):
            return None
    if (getattr(expired_session, "sync_reconnect_reason", None) != "session_expired"
            or getattr(expired_session, "connected", None) is not False):
        return None
    scope = (
        account.identity_id, account.provider_id, account.credential_id,
        account.unique_id, account.is_primary,
    )
    if (any(type(x) is not str or not x or len(x) > 256
            for x in scope[:3]) or type(scope[3]) is not str
            or type(scope[4]) is not bool or scope[4] is not True):
        return None
    key = self._session_key(scope[1], scope[2])
    if (self._sessions.get(key) is not expired_session
            or sum(a is account for a in self.accounts) != 1):
        return None
    try:
        from .source_context import source_context_epoch
        epoch = source_context_epoch(self)
        rows = self.config.provider_accounts(enabled_only=True)
        candidates = _configured_rows_for_source(
            rows, source_scope=scope[:3], default_identity=scope[0],
        )
        row = candidates[0]
        plugin = self.registry.maybe_get(scope[1])
        if (plugin is None or plugin.descriptor.state != "ready"
                or getattr(plugin.descriptor, "interactive_authentication", False) is not True
                or not _interactive_background_opted_in(plugin.descriptor, row)):
            return None
        installed = self._provider_plugin_status(scope[1])
        if installed is not None and (
            installed.enabled is not True or installed.state != "ready"
        ):
            return None
        bindings = self.storage.provider_connections(identity_id=scope[0])
        if (type(bindings) is not list
                or sum(type(r) is dict
                       and r.get("identity_id") == scope[0]
                       and r.get("provider_id") == scope[1]
                       and r.get("credential_id") == scope[2]
                       and r.get("enabled") is True
                       for r in bindings) != 1):
            return None
        # An old CAS jar / attempted login may never be revived: only a
        # newly constructed provider session may submit the one fresh POST.
        replacement = plugin.create_session(
            credential_id=scope[2],
            label=str(row.get("label") or plugin.descriptor.display_name),
            credentials=dict(row.get("credentials") or {}),
            options=dict(row.get("options") or {}),
            config=self.config, storage=self.storage,
            diagnostics=self.diagnostics,
        )
    except Exception:
        return None

    accepted = False
    try:
        accounts = replacement.connect()
        if (replacement is expired_session
                or replacement.connected is not True
                or replacement.provider_id != scope[1]
                or replacement.credential_id != scope[2]
                or type(accounts) not in (tuple, list)
                or len(accounts) != 1 or type(accounts[0]) is not Account):
            return None
        candidate = accounts[0]
        candidate.identity_id = scope[0]
        if ((candidate.identity_id, candidate.provider_id,
             candidate.credential_id, candidate.unique_id,
             candidate.is_primary) != scope):
            return None
        # No old-source resurrection, reconfiguration, A→B switch or consent
        # change during the CAS calls may authorize installing new authority.
        if (source_context_epoch(self) != epoch
                or self._sessions.get(key) is not expired_session
                or sum(a is account for a in self.accounts) != 1
                or (account.identity_id, account.provider_id,
                    account.credential_id, account.unique_id,
                    account.is_primary) != scope):
            return None
        latest_rows = self.config.provider_accounts(enabled_only=True)
        selected = _configured_rows_for_source(
            latest_rows, source_scope=scope[:3], default_identity=scope[0],
        )[0]
        if selected != row or not _interactive_background_opted_in(
            plugin.descriptor, selected,
        ):
            return None
        self._sessions[key] = replacement
        self.accounts = [
            candidate if existing is account else existing for existing in self.accounts
        ]
        if self.current_account is account:
            self.current_account = candidate
        if self.primary is account:
            self.primary = candidate
        self._source_context_epoch = epoch + 1
        self._account_context_epoch += 1
        self._session_errors.pop(key, None)
        accepted = True
        try:
            expired_session.close()
        except Exception:
            pass
        try:
            self.diagnostics.add(
                "PLUGIN", "provider source session renewed", "ok",
                detail={"code": "session_expired_reconnected"},
                level="detailed",
            )
        except Exception:
            pass
        return candidate
    except Exception:
        return None
    finally:
        if not accepted:
            try:
                replacement.close()
            except Exception:
                pass


def connect_configured(self, *, logger, caller_category: str = "other",
                       source_scope: tuple[str, str, str] | None = None) -> dict[str, Any]:
    """Connect all enabled provider identities from local preferences.

    Failures are isolated per provider account: one unavailable source does
    not prevent another source from starting. At least one logical account
    must be available for the current account-oriented GUI. Anonymous
    catalogue-only providers will be included once their plugin is ready.
    """
    from .diagnostic_provider_connection_attempt import new_attempt
    started_all = time.monotonic()
    logger.info("TRACE connect-configured begin")
    # Validate a targeted request BEFORE disconnecting any live sessions.
    # An absent/ambiguous source must have no connection-side effects.
    rows = _configured_rows_for_source(
        self.config.provider_accounts(enabled_only=True),
        source_scope=source_scope,
        default_identity=(self.current_identity.identity_id
                          if self.current_identity else "default"),
    )
    self.disconnect_configured()
    all_accounts: list[Account] = []
    connected: list[str] = []
    errors: dict[str, str] = {}
    failures: list[dict[str, str]] = []
    configured_provider_ids = {str(row.get("provider") or "").strip().lower() for row in rows if isinstance(row, dict)}
    # Plugin API v1: a ready provider with no credential fields is a public
    # anonymous source.  Do not require a provider-specific config migration
    # or a fake account row merely to make it searchable.
    for plugin in self.registry.all():
        if source_scope is not None:
            continue
        descriptor = plugin.descriptor
        provider_id = str(descriptor.id or "").strip().lower()
        plugin_status = self._provider_plugin_status(provider_id)
        instantiation = ("singleton_public" if provider_id in self._virtual_parent_by_provider else
                         getattr(getattr(plugin_status, "descriptor", None), "instantiation", "multiple"))
        if (
            provider_id
            and descriptor.state == "ready"
            and not descriptor.credential_fields
            and not getattr(descriptor, "interactive_authentication", False)
            and provider_id not in configured_provider_ids
            and instantiation != "singleton_public"
        ):
            rows.append({
                "id": f"{provider_id}-public",
                "provider": provider_id,
                "label": descriptor.display_name,
                "enabled": True,
                "anonymous": True,
                "credentials": {},
                "options": {},
            })
    # singleton/public providers are global plugin instances, not fake
    # credentials attached to the default Identity. One ephemeral session is
    # connected and per-Identity visibility is enforced at query time.
    for plugin in self.registry.all():
        if source_scope is not None:
            continue
        provider_id = str(plugin.descriptor.id or "").strip().lower()
        plugin_status = self._provider_plugin_status(provider_id)
        if (
            not provider_id or plugin.descriptor.state != "ready" or plugin.descriptor.credential_fields
            or getattr(plugin.descriptor, "interactive_authentication", False)
            or (provider_id not in self._virtual_parent_by_provider
                and getattr(getattr(plugin_status, "descriptor", None), "instantiation", "multiple") != "singleton_public")
        ):
            continue
        credential_id = "__public__"
        key = self._session_key(provider_id, credential_id)
        public_started = time.monotonic()
        attempt = new_attempt(
            self.registry, provider_id, diagnostics=self.diagnostics,
            source_kind="singleton_public",
            caller_category=caller_category,
        )
        session = None
        invoked = False
        try:
            session = plugin.create_session(
                credential_id=credential_id, label=plugin.descriptor.display_name,
                credentials={}, options={}, config=self.config, storage=self.storage,
                diagnostics=self.diagnostics,
            )
            invoked = True
            attempt.emit(self.diagnostics, "connect_invoked", "unknown", "connect_invoked")
            session.connect()
            # Only an affirmative session state attests a successful connection.
            # connect() returning normally is not proof (notably for SSO).
            if getattr(session, "connected", None) is not True:
                raise ProviderConfigurationError("provider_not_connected")
            self._sessions[key] = session
            connected.append(key)
            attempt.emit(self.diagnostics, "terminal", "success", "connected")
            _publish_connection_success(
                self.diagnostics, self.registry, provider_id,
                singleton=True, account_count=0,
                duration_ms=int((time.monotonic() - public_started) * 1000),
            )
            try:
                self.diagnostics.add(
                    "PLUGIN", "public provider connected", "ok",
                    detail={"provider": provider_id, "instantiation": "singleton_public"}, level="detailed",
                )
            except Exception:
                # Optional reporting must never invert a successful connection.
                pass
        except Exception:
            attempt.emit(
                self.diagnostics, "terminal", "failed",
                "connect_failed" if invoked else "session_invalid",
            )
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
            errors[key] = "Connexion à la source indisponible"
            public_failure = public_connection_failure(self.registry, provider_id)
            failures.append(public_failure)
            logger.warning(
                "TRACE provider connect error code=provider_connect_failed provider_id=%s",
                public_failure["provider_id"],
            )
            _publish_connection_failure(
                self.diagnostics, public_failure, singleton=True,
                duration_ms=int((time.monotonic() - public_started) * 1000),
            )

    logger.info("TRACE connect-configured enabled-rows=%s", len(rows))
    for row in rows:
        provider_id = str(row.get("provider") or "").strip().lower()
        credential_id = str(row.get("id") or "").strip()
        identity_id = str(row.get("identity_id") or (self.current_identity.identity_id if self.current_identity else "default")).strip()
        if not provider_id or not credential_id:
            continue
        item_started = time.monotonic()
        attempt = new_attempt(
            self.registry, provider_id, diagnostics=self.diagnostics,
            identity_id=identity_id, credential_id=credential_id,
            source_kind="configured",
            caller_category=caller_category,
        )
        logger.info("TRACE provider prepare kind=configured")
        # All enabled configured sources share the same session-error key,
        # including unavailable providers that never create a session.
        key = self._session_key(provider_id, credential_id)
        plugin = self.registry.maybe_get(provider_id)
        if plugin is None:
            attempt.emit(self.diagnostics, "terminal", "skipped", "plugin_missing")
            errors[key] = "Plugin inconnu"
            missing = public_connection_failure(
                self.registry, provider_id, code=MISSING_PROVIDER_CODE,
            )
            failures.append(missing)
            _publish_connection_failure(
                self.diagnostics, missing,
                duration_ms=int((time.monotonic() - item_started) * 1000),
            )
            continue
        if plugin.descriptor.state != "ready":
            # Skeletons must never perform I/O, but an enabled configured
            # connection is still unavailable to the opening-sync summary.
            attempt.emit(self.diagnostics, "terminal", "skipped", "plugin_not_ready")
            errors[key] = "Connexion à la source indisponible"
            failure = public_connection_failure(self.registry, provider_id)
            failures.append(failure)
            _publish_connection_failure(
                self.diagnostics, failure,
                duration_ms=int((time.monotonic() - item_started) * 1000),
            )
            continue
        if (getattr(plugin.descriptor, "interactive_authentication", False)
                and not _interactive_background_opted_in(plugin.descriptor, row)):
            # Legacy interactive sources MUST stay out of startup connection.
            # Only an exact, declared, persisted per-account opt-in admits
            # the provider's direct HTTP connect; never synthesize a browser.
            attempt.emit(self.diagnostics, "terminal", "skipped", "interactive_auth_required")
            continue
        session = None
        invoked = False
        try:
            create_started = time.monotonic()
            create_returned = False
            try:
                session = plugin.create_session(
                    credential_id=credential_id,
                    label=str(row.get("label") or plugin.descriptor.display_name),
                    credentials=dict(row.get("credentials") or {}),
                    options=dict(row.get("options") or {}),
                    config=self.config, storage=self.storage,
                    diagnostics=self.diagnostics,
                )
                create_returned = True
            finally:
                _record_configured_connect_phase(
                    logger, attempt, "create_session", create_started,
                    "returned" if create_returned else "raised",
                )
            logger.info("TRACE provider connect begin kind=configured")
            invoked = True
            attempt.emit(self.diagnostics, "connect_invoked", "unknown", "connect_invoked")
            connect_started = time.monotonic()
            connect_returned = False
            try:
                accounts = session.connect()
                connect_returned = True
            finally:
                _record_configured_connect_phase(
                    logger, attempt, "session_connect", connect_started,
                    "returned" if connect_returned else "raised",
                )
            if getattr(session, "connected", None) is not True:
                raise ProviderConfigurationError("provider_not_connected")
            logger.info("TRACE provider connect end account_count=%s dt=%.3fs", len(accounts), time.monotonic()-item_started)
            registration_started = time.monotonic()
            registration_returned = False
            try:
                self._sessions[key] = session
                connection_id = self.storage.provider_connection_put({
                    "identity_id": identity_id, "provider_id": provider_id,
                    "credential_id": credential_id,
                    "label": str(row.get("label") or plugin.descriptor.display_name),
                    "enabled": True,
                })
                for account in accounts:
                    account.identity_id = identity_id
                    self.storage.provider_account_put({
                        "connection_id": connection_id, "identity_id": identity_id,
                        "provider_id": provider_id,
                        "credential_id": credential_id,
                        "account_id": account.unique_id,
                        "label": account.display_name or (
                            "Compte principal" if account.is_primary else "Compte lié"
                        ),
                        "is_primary": account.is_primary,
                        "metadata": {"barcode": account.barcode},
                    })
                all_accounts.extend(accounts)
                connected.append(key)
                registration_returned = True
            finally:
                _record_configured_connect_phase(
                    logger, attempt, "account_registration",
                    registration_started,
                    "returned" if registration_returned else "raised",
                )
            attempt.emit(self.diagnostics, "terminal", "success", "connected")
            _publish_connection_success(
                self.diagnostics, self.registry, provider_id,
                singleton=False, account_count=len(accounts),
                duration_ms=int((time.monotonic() - item_started) * 1000),
            )
            try:
                self.diagnostics.add(
                    "PLUGIN", "provider connected", "ok",
                    detail={"code": "provider_connected", "accounts": len(accounts)},
                )
            except Exception:
                pass
        except Exception as exc:
            attempt.emit(
                self.diagnostics, "terminal", "failed",
                "connect_failed" if invoked else "session_invalid",
            )
            # Never invoke logger.exception: the traceback may include signed
            # endpoints, plugin credentials, response bodies and local paths.
            public_failure = public_connection_failure(self.registry, provider_id)
            logger.warning(
                "TRACE provider connect error code=provider_connect_failed provider_id=%s dt=%.3fs",
                public_failure["provider_id"], time.monotonic()-item_started,
            )
            logger.warning(
                "TRACE provider connect cause provider_id=%s reason_code=%s",
                public_failure["provider_id"], _public_connection_cause(exc),
            )
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
            errors[key] = "Connexion à la source indisponible"
            failures.append(public_failure)
            _publish_connection_failure(
                self.diagnostics, public_failure,
                duration_ms=int((time.monotonic() - item_started) * 1000),
            )

    self._session_errors = errors
    self._connect_failures_public = tuple(failures)
    public_failures = bounded_connection_failures(failures)
    if not all_accounts:
        # Local-first cifereca is usable without any provider. Plugin
        # installation/configuration and the local Library remain available.
        self.primary = self._local_account()
        self.accounts = [self.primary]
        self.current_account = self.primary
        self._active_session_key = None
        self.last_loans = []
        return {"accounts": self.accounts, "identities": self.identities, "loans": [], "connected": connected, "errors": errors, "connection_failures": public_failures}

    self.accounts = all_accounts
    self.current_account = all_accounts[0]
    self.current_identity = self.identity_by_id(self.current_account.identity_id) or self.current_identity
    self._active_session_key = self._session_key(self.current_account.provider_id, self.current_account.credential_id)
    self.primary = self.current_account
    if (caller_category == "startup/connect_configured"
            and source_scope is None
            and _opening_sync_covers_visible_accounts(self)):
        # GTK queues the opening Sync as soon as this first connection
        # completes. It makes its own fresh, validated loan/rights reads.
        # A forced loan refresh here would duplicate the two Paris ListLoans
        # branches (and the BnF activity read) before that canonical cycle.
        # disconnect_configured() already revoked the previous UI loans.
        # Preserve the initial read for a connected source excluded from Sync;
        # no later automatic inventory would otherwise populate its loans.
        # Keep explicit reconnects and targeted CLI connections unchanged.
        loans = []
    else:
        loans = self.refresh_identity_loans(force=True)
    return {"accounts": self.accounts, "identities": self.identities, "loans": loans, "connected": connected, "errors": errors, "connection_failures": public_failures}


def login(self, username: str, password: str) -> dict[str, Any]:
    raise ProviderConfigurationError(
        "La connexion legacy Paris a été retirée de cifereca 0.9.2 ; configurez le plugin provider.paris."
    )


def select_account(self, unique_id: str, provider_id: str | None=None, credential_id: str | None=None) -> Account:
    for account in self.accounts:
        if account.unique_id != unique_id:
            continue
        if provider_id and account.provider_id != provider_id:
            continue
        if credential_id and account.credential_id != credential_id:
            continue
        # Some callers select a borrower directly, without select_identity.
        # A cross-profile account change must revoke the prior loan list as
        # promptly as an explicit profile change does.
        with self._search_generation_lock:
            previous_identity_id = getattr(self.current_identity, "identity_id", None)
            self.current_account = account
            self._account_context_epoch += 1
            self.current_identity = self.identity_by_id(account.identity_id) or self.current_identity
            if getattr(self.current_identity, "identity_id", None) != previous_identity_id:
                self.last_loans = []
        key = self._session_key(account.provider_id, account.credential_id)
        if key in self._sessions:
            self._active_session_key = key
        return account
    return self.current_account


def _session_for_account(self, account: Account | None=None) -> ProviderSession:
    account = account or self.current_account
    key = self._session_key(account.provider_id, account.credential_id)
    session = self._sessions.get(key)
    if session is None:
        raise ProviderConfigurationError(f"Provider non connecté: {account.provider_id}")
    return session


def _session_for_source(self, provider_id: str, credential_id: str | None=None) -> ProviderSession:
    if not self._provider_enabled_for_identity(provider_id):
        raise ProviderConfigurationError(f"Provider désactivé ou indisponible: {provider_id}")
    if credential_id is not None:
        # A source item/loan carrying an explicit credential never belongs to
        # another connected session of that provider. In particular a removed
        # or disconnected B must not silently fall back to active A; an empty
        # credential is still an explicit coordinate, not a wildcard.
        key = self._session_key(provider_id, credential_id)
        session = self._sessions.get(key)
        if session is None:
            raise ProviderConfigurationError(f"Provider non connecté: {provider_id}")
        return session
    # Only legacy callers which provided NO credential may resolve a
    # provider-wide session. This is never used to remap an explicit source.
    for session in self._sessions.values():
        if session.provider_id == provider_id and session.connected:
            return session
    raise ProviderConfigurationError(f"Provider non connecté: {provider_id}")
