from __future__ import annotations

from .core import acsm_staging, loan_failure_evidence

import logging
import time
import json
import hashlib
import inspect
import os
import re
import shutil
import unicodedata
import uuid
from difflib import SequenceMatcher
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from queue import Queue, Empty, Full
from threading import Lock, RLock
from typing import Any, Callable
from urllib.parse import urlparse, urljoin
from . import acsm_cache
from .core import adept_borrower, loan_artifact_state, adept_acsm_plan
from .core import kobo_backup
from .core import sync_coordination, sync_projection
from .core import library_projection, library_mutation
from .core import provider_sessions, search_orchestration
from .acquisition import Acquisition, AcquisitionManager, AcquisitionResult
from .acquisition_engine import AcquisitionLedger, AcquisitionReconciler, Entitlement, SourceIdentity
from .catalog import LocalCatalog
from .epub_metadata import cached_cover_url
from .core.covers import candidate as cover_candidate, resolve_cover
from .config import Config
from .diagnostics import Diagnostics, redact
from .kobo import KoboAdeptError
from .kobo.acquisition import KoboOfferAdapter, KoboAcquisitionSelectionError
from .kobo.executor import execute_kobo_acquisition
from .kobo.vault import KoboVaultError
from .identities import Identity, IdentityPreferences, AdeptProfileResolver, AdeptProfileResolutionError
from .core import adept_profiles
from .locking import InterProcessLock, LockBusyError
from .library_files import safe_library_component, sha256_file, try_sha256_file
from .artifact_inspection import detect_acsm_format, inspect_artifact
from .core.acquisition_decision import decide_artifact_reuse
from .core.adept_retry_guard import blocked_automatic_adept_retry, adept_attention_code
from .paths import acquisition_lock_file, data_dir
from .models import Account, Booking, HistoryItem, Holding, ItemDetail, Loan, SearchResult
from .core import loan_accounts, loan_download_projection
from .providers import ProviderConfigurationError, ProviderRegistry, ProviderSearchCapabilities, ProviderSession
from .plugin_api import AttentionRequired, BundleError, DrmMutationRequest, UserFacingError, load_bundle_manifest, ProviderSearchEvent, ProviderSearchStream
from .storage import Storage
from .core.provider_stream import consume_provider_stream, SearchStreamFailure, SearchStreamCancelled
from .core.provider_collection_cache import ProviderCollectionSnapshotStore

SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._()\- àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ]+")

_TRACE_LOG = logging.getLogger("cifereca")
_SEARCH_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="cifereca-search")
# Serialize automatic loan check-and-record within this process. Opening sync
# additionally holds the existing cross-process acquisition lock.
_AUTOMATIC_LOAN_GUARD = Lock()

class LibraryService:
    """Application service orchestrating provider plugins.

    0.3.0 keeps a compatibility Paris session so existing UI/tests continue to
    work, but all account-scoped operations are routed through ProviderSession.
    Concrete providers must never be imported by the GUI.
    """

    def __init__(self, config: Config, storage: Storage, diagnostics: Diagnostics):
        self.config = config
        self.storage = storage
        self.diagnostics = diagnostics
        # Final 0.9.2 core: concrete providers are supplied exclusively by
        # Plugin API packages. The core registry starts empty and is populated
        # when the PluginManager is attached.
        self.registry = ProviderRegistry()
        self.acquisition = AcquisitionManager(config, diagnostics)
        self.acquisition_ledger = AcquisitionLedger(storage)
        self.acquisition_reconciler = AcquisitionReconciler(storage)
        self._sessions: dict[str, ProviderSession] = {}
        self._session_errors: dict[str, str] = {}
        self.identities: list[Identity] = []
        self.current_identity: Identity | None = None
        self._account_context_epoch = 0
        recovered = self._recover_identity_directory_migration()
        self._sync_identity_configuration()
        if recovered:
            self.catalog_scan()
            self._identity_directory_engine().finish()

        self._active_session_key: str | None = None
        self.primary = self._local_account()
        self.accounts: list[Account] = [self.primary]
        self.current_account = self.primary
        self.last_loans: list[Loan] = []
        self.last_history: list[HistoryItem] = []
        self.last_bookings: list[Booking] = []
        self.last_search: list[SearchResult] = []
        self.last_search_warnings: list[str] = []
        self._plugin_manager: Any | None = None
        self._provider_plugin_ids: dict[str, str] = {}
        self._virtual_parent_by_provider: dict[str, str] = {}
        self._virtual_snapshot_store = ProviderCollectionSnapshotStore(storage)
        # Collection events can originate on any plugin thread. Never read an
        # inventory or connect a provider from an arbitrary caller/GTK thread.
        self._virtual_reconcile_lock = RLock()
        self._virtual_schedule_lock = Lock()
        self._virtual_refresh_pending = False
        self._virtual_refresh_again = False
        self._virtual_collection_closed = False
        self._virtual_manager_unsubscribe: Callable[[], None] | None = None
        self._virtual_parent_subscriptions: dict[str, tuple[object, Callable[[], None]]] = {}
        self._virtual_change_callbacks: list[Callable[[], None]] = []
        self._kobo_adept_state_cache: dict[str, Any] | None = None
        self._kobo_adept_state_cache_at = 0.0
        # Search workers are shared across requests. Recreating an executor for
        # each query was measurable on short searches and forced cancellation to
        # wait for the executor context manager to drain running workers.
        self._search_generation_lock = __import__("threading").Lock()
        self._search_generation = 0

    def _identity_directory_engine(self):
        from .identities.directory_migration import DirectoryMigration
        return DirectoryMigration(self.config, self.storage, self.library_dir())

    def _recover_identity_directory_migration(self):
        from .identities.directory_migration import JOURNAL_KEY
        if not self.storage.metadata_get(JOURNAL_KEY):
            return None
        try:
            with InterProcessLock(acquisition_lock_file()):
                result = self._identity_directory_engine().run()
            self.diagnostics.add("IDENTITY", "directory migration recovery", "ok", detail=result)
            return result
        except Exception as exc:
            raise ProviderConfigurationError("Migration de répertoire à reprendre : " + str(exc)) from exc

    def save_diagnostic_preferences(self, *, enabled: bool, level: str) -> None:
        """Persist the immediate diagnostic controls, independently of drafts."""
        from copy import deepcopy
        from .logging_utils import set_diagnostics_level
        level = "verbose" if level == "detailed" else str(level)
        if level not in {"errors", "normal", "verbose", "trace"}:
            raise ValueError("Niveau de diagnostic inconnu")
        draft = deepcopy(self.config)
        draft.set("diagnostics.enabled", bool(enabled))
        draft.set("diagnostics.level", level)
        draft.save()
        self.config.data = draft.data
        self.diagnostics.enabled = bool(enabled)
        self.diagnostics.level = level
        set_diagnostics_level(level)

    def can_download_loan(self, loan: Loan) -> bool:
        if loan.medium != "digital":
            return False
        try:
            session = self._session_for_source(loan.source_provider, loan.source_credential)
            return bool(session.connected and session.capabilities.digital_download)
        except Exception:
            return False

    def save_configuration(self, data: dict[str, Any]) -> dict[str, Any]:
        """Apply staged settings and journal directory changes before publishing them."""
        from copy import deepcopy
        from .identities.directory_migration import DirectoryMigration, DirectoryMigrationError
        old_data = deepcopy(self.config.data)
        draft = deepcopy(self.config); draft.data = deepcopy(data)
        rows = draft.get("identities", [])
        DirectoryMigration.validate(rows)
        old_dirs = {i.identity_id: i.directory_name for i in self.identities}
        changes = any(row["id"] in old_dirs and str(row.get("directory_name") or row["id"]) != old_dirs[row["id"]] for row in rows)
        if changes and draft.get("library.directory") != self.config.get("library.directory"):
            raise DirectoryMigrationError("Modifiez d’abord le répertoire de l’identité, puis la racine de bibliothèque dans une opération distincte.")
        active = self.current_identity.identity_id if self.current_identity else None
        engine = self._identity_directory_engine()
        try:
            with InterProcessLock(acquisition_lock_file()):
                if engine.pending():
                    engine.run()
                    self._sync_identity_configuration()
                    self.catalog_scan(); engine.finish()
                plan = engine.plan(rows)
                self.config.data = deepcopy(data)
                self.config.set("identities", plan["new_identities"])
                if plan["changes"]:
                    engine._write(plan)
                    result = engine.run(plan)
                else:
                    self.config.save()
                    result = {"migrated": 0, "units": 0, "collisions": 0}
                self._sync_identity_configuration()
                if active and self.identity_by_id(active):
                    self.current_identity = self.identity_by_id(active)
                if plan["changes"]:
                    result["scan"] = self.catalog_scan()
                    engine.finish()
            self.diagnostics.add("IDENTITY", "directory migration", "ok", detail=result)
            return result
        except Exception:
            pending = engine.pending()
            if not pending or pending["state"] == "planned":
                self.config.data = old_data
            else:
                self.config.set("identities", pending["new_identities"])
                self._sync_identity_configuration()
            raise

    def change_identity_directory(self, identity_id: str, directory_name: str, *, dry_run: bool = False):
        from copy import deepcopy
        rows = self.config.identities()
        identity = next((row for row in rows if row["id"] == identity_id), None)
        if not identity:
            raise ProviderConfigurationError("Identité inconnue : " + identity_id)
        identity["directory_name"] = directory_name
        if dry_run:
            with InterProcessLock(acquisition_lock_file()):
                plan = self._identity_directory_engine().plan(rows)
            return {"dry_run": True, "migrated": len(plan["changes"]), "units": len(plan["entries"]), "collisions": plan["collisions"], "changes": plan["changes"], "entries": plan["entries"]}
        data = deepcopy(self.config.data); data["identities"] = rows
        return self.save_configuration(data)

    def _sync_identity_configuration(self) -> None:
        """Project local identity config into the durable v4 provenance model."""
        identities: list[Identity] = []
        identity_rows = self.config.identities() if hasattr(self.config, "identities") else self.config.get("identities", [])
        if not isinstance(identity_rows, list) or not identity_rows:
            identity_rows = [{"id": "default", "label": "Ma bibliothèque", "preferences": {"reading_languages": ["fr"]}}]
        for row in identity_rows:
            if not isinstance(row, dict):
                continue
            prefs = IdentityPreferences.from_value(row.get("preferences"))
            identity = Identity(
                identity_id=str(row.get("id") or "default"),
                label=str(row.get("label") or row.get("id") or "Identité"),
                directory_name=str(row.get("directory_name") or row.get("id") or "default"),
                preferences=prefs,
                metadata={"source": "config"},
            )
            self.storage.identity_put(identity)
            profile = str(row.get("adept_profile") or "").strip()
            if profile:
                self.storage.adept_profile_binding_set("identity", identity.identity_id, profile)
            identities.append(identity)
        if not identities:
            identity = Identity("default", "Ma bibliothèque")
            self.storage.identity_put(identity)
            identities = [identity]
        self.identities = identities
        self.current_identity = identities[0]

        known = {identity.identity_id for identity in identities}
        configured_accounts = self.config.provider_accounts() if hasattr(self.config, "provider_accounts") else self.config.get("providers.accounts", [])
        if not isinstance(configured_accounts, list):
            configured_accounts = []
        kobo_by_identity: dict[str, str] = {}
        for row in configured_accounts:
            if not isinstance(row, dict):
                continue
            provider_id = str(row.get("provider") or "").strip().lower()
            credential_id = str(row.get("id") or "").strip()
            identity_id = str(row.get("identity_id") or identities[0].identity_id).strip()
            if identity_id not in known:
                identity_id = identities[0].identity_id
            if provider_id == "kobo" and credential_id:
                previous = kobo_by_identity.get(identity_id)
                if previous and previous != credential_id:
                    raise ProviderConfigurationError(
                        f"Identité {identity_id}: un seul compte Kobo Vault est autorisé ({previous}, {credential_id})"
                    )
                kobo_by_identity[identity_id] = credential_id
            if provider_id and credential_id:
                self.storage.provider_connection_put({
                    "identity_id": identity_id,
                    "provider_id": provider_id,
                    "credential_id": credential_id,
                    "label": str(row.get("label") or provider_id),
                    "enabled": bool(row.get("enabled", True)),
                    "metadata": {"anonymous": bool(row.get("anonymous", False))},
                })


    def identity_by_id(self, identity_id: str | None) -> Identity | None:
        if identity_id is None:
            return None
        return next((identity for identity in self.identities if identity.identity_id == identity_id), None)

    def identity_for_connection(self, provider_id: str, credential_id: str) -> Identity:
        identity_id = self.storage.identity_for_connection(provider_id, credential_id)
        identity = self.identity_by_id(identity_id)
        return identity or self.current_identity or self.identities[0]

    def normal_paris_adept_status(self, identity_id: str) -> dict[str, Any]:
        """Read-only status of the normal Paris-primary/ADEPT configuration.

        This is *not* an Adobe fulfillment authorization, and it does not
        reinterpret historical loan provenance or exceptional borrower grants.
        """
        from .core.paris_adept_normal import normal_paris_adept_status

        if self.identity_by_id(identity_id) is None:
            return {
                "identity_id": str(identity_id),
                "status": "to_configure",
                "reason": "unknown_identity",
                "paris_label": None,
                "adept_profile_id": None,
            }
        return normal_paris_adept_status(
            identity_id=identity_id,
            connections=self.storage.provider_connections(identity_id=identity_id),
            accounts=self.accounts,
            profile_id=self.storage.adept_profile_binding_get("identity", identity_id),
        )

    def accounts_for_identity(self, identity_id: str | None = None) -> list[Account]:
        target = identity_id or (self.current_identity.identity_id if self.current_identity else None)
        return [account for account in self.accounts if target is None or account.identity_id == target]

    def sync_source_id(self, account: Account) -> str:
        account_id = str(account.unique_id or "").strip() or f"{account.provider_id}:{account.credential_id}:primary"
        raw = "\0".join((str(account.identity_id or ""), str(account.provider_id or ""), str(account.credential_id or ""), account_id))
        return "sync:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def sync_source_enabled(self, account: Account) -> bool:
        """Same fail-closed toggle as the public Sync broker.

        Missing metadata retains the legacy inventory default; malformed
        values cannot opt a source back into startup refresh (#1346).
        """
        from .core.source_enablement import source_enabled
        raw = self.storage.metadata_get("sync.source.enabled." + self.sync_source_id(account))
        return source_enabled(raw)

    def select_identity(self, identity_id: str) -> Identity:
        identity = self.identity_by_id(identity_id)
        if identity is None:
            raise ProviderConfigurationError(f"Identité inconnue: {identity_id}")
        # Revoke in-flight progressive searches, including an Alice→Bob→Alice
        # round trip. Serialize snapshot publication with this transition.
        with self._search_generation_lock:
            self.current_identity = identity
            self._account_context_epoch += 1
            self._search_generation += 1
            self.last_search = []
            self.last_search_warnings = []
        # History and bookings are identity-scoped snapshots. Invalidate them
        # at the service boundary so non-UI callers cannot observe data that
        # belonged to the previously selected identity.
        self.last_history = []
        self.last_bookings = []
        accounts = self.accounts_for_identity(identity.identity_id)
        if accounts:
            self.select_account(accounts[0].unique_id, accounts[0].provider_id, accounts[0].credential_id)
        return identity

    def library_identity_provider_dir(self, identity_id: str, provider_id: str) -> Path:
        from .identities.directory_migration import JOURNAL_KEY
        if self.storage.metadata_get(JOURNAL_KEY):
            raise ProviderConfigurationError("Une migration de répertoire attend sa reprise ; relancez l’application avant une acquisition.")
        identity = self.identity_by_id(identity_id)
        directory_name = identity.directory_name if identity and identity.directory_name else identity_id
        identity_part = safe_library_component(directory_name, "default")
        provider_part = safe_library_component(provider_id, "local")
        path = self.library_dir() / identity_part / provider_part
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _migrate_legacy_flat_kobo_files(self, default_identity: Identity) -> None:
        default_credentials = {
            str(row.get("credential_id") or "")
            for row in self.storage.provider_connections(identity_id=default_identity.identity_id)
            if str(row.get("provider_id") or "") == "kobo"
        }
        if not default_credentials:
            return
        root = self.library_dir().resolve()
        target_dir = self.library_identity_provider_dir(default_identity.identity_id, "kobo").resolve()
        if target_dir == root:
            return
        for record in self.storage.backup_list("kobo"):
            if str(record.get("credential_id") or "") not in default_credentials:
                continue
            raw_path = str(record.get("local_path") or "").strip()
            if not raw_path:
                continue
            source = Path(raw_path).expanduser()
            try:
                source_resolved = source.resolve()
            except OSError:
                continue
            if not source_resolved.is_file() or source_resolved.parent != root:
                continue
            target = target_dir / source_resolved.name
            if target.resolve() == source_resolved:
                continue
            if target.exists():
                try:
                    if sha256_file(target) != sha256_file(source_resolved):
                        suffix = sha256_file(source_resolved)[:8]
                        target = target.with_name(f"{target.stem}-{suffix}{target.suffix}")
                except OSError:
                    continue
            if not target.exists():
                tmp = target.with_name(target.name + ".migrating")
                try:
                    shutil.copy2(source_resolved, tmp)
                    if sha256_file(tmp) != sha256_file(source_resolved):
                        tmp.unlink(missing_ok=True)
                        raise OSError("hash mismatch while reclassifying legacy Kobo file")
                    tmp.replace(target)
                finally:
                    tmp.unlink(missing_ok=True)
            self.storage.relocate_local_path(
                source_resolved, target, identity_id=default_identity.identity_id, provider_id="kobo"
            )
            try:
                source_resolved.unlink()
            except FileNotFoundError:
                pass
            self.diagnostics.add(
                "CATALOG", "legacy Kobo placement reclassified",
                detail={"identity": default_identity.identity_id, "target": str(target)},
            )

    def adept_profile_choices(self) -> list[dict[str, str]]:
        """Fetch only usable profile references; choosing/persisting stays explicit."""
        choices = []
        seen = set()
        for row in self.kobo_profiles():
            if not isinstance(row, dict) or not AdeptProfileResolver._fetch_capable(row):
                continue
            reference = self._adept_profile_ref(row)
            if reference and reference not in seen:
                seen.add(reference)
                choices.append({"reference": reference, "name": self._adept_profile_name(row) or reference})
        return choices

    def resolve_adept_profile_for_identity(
        self,
        identity_id: str,
        *,
        explicit_profile: str | None = None,
    ) -> tuple[str, str]:
        """Resolve the persisted ADE profile reference to the CLI profile name.

        Paris persists the stable ``profile_id`` introduced by Kobo-Adept 0.13.3,
        but Adept operations and post-download hooks address a profile by its
        human/CLI ``name``.  Resolution must therefore happen at every execution
        boundary and must use the exact Paris identity, not the current/default
        identity as an accidental fallback.
        """
        identity = self.identity_by_id(identity_id)
        if identity is None:
            raise AdeptProfileResolutionError(
                f"Identité inconnue pour la résolution ADE: {identity_id}",
                code="adept_profile_required",
            )
        identity_profile = self.storage.adept_profile_binding_get("identity", identity.identity_id)
        try:
            profiles = self.kobo_profiles()
        except Exception:
            profiles = []
        resolution = AdeptProfileResolver().resolve(
            explicit_profile=explicit_profile,
            identity_profile=identity_profile,
            profiles=profiles,
        )
        resolved_row = self._adept_profile_row(resolution.profile_id, profiles)
        if resolution.source == "identity" and identity_profile and resolved_row:
            stable_id = self._adept_profile_ref(resolved_row)
            if stable_id and stable_id != identity_profile:
                self.storage.adept_profile_binding_set("identity", identity.identity_id, stable_id)
        profile_name = self._adept_profile_name(resolved_row) if resolved_row else resolution.profile_id
        return profile_name, resolution.source

    def resolve_adept_profile(
        self,
        *,
        provider_id: str,
        credential_id: str,
        account_id: str | None = None,
        explicit_profile: str | None = None,
    ) -> tuple[str, str]:
        """Resolve ADE at Paris-identity scope.

        ``account_id`` remains in the call contract for compatibility, but 0.8.1
        deliberately no longer binds ADE profiles to provider sub-accounts.
        """
        identity = self.identity_for_connection(provider_id, credential_id)
        return self.resolve_adept_profile_for_identity(
            identity.identity_id, explicit_profile=explicit_profile
        )

    def _local_account(self) -> Account:
        return Account(
            display_name="Bibliothèque locale", unique_id="local", is_primary=True,
            provider_id="local", credential_id="local", provider_label="cifereca",
            identity_id=self.current_identity.identity_id if self.current_identity else "default",
        )

    @staticmethod
    def _session_key(provider_id: str, credential_id: str) -> str:
        return f"{provider_id.lower()}::{credential_id}"

    @property
    def active_session(self) -> ProviderSession:
        if self._active_session_key is None or self._active_session_key not in self._sessions:
            raise ProviderConfigurationError("Aucun provider n'est connecté")
        return self._sessions[self._active_session_key]

    def attach_plugin_manager(self, manager: Any) -> list[str]:
        """Attach Plugin API services, preferring external providers/runtimes when ready."""
        return provider_sessions.attach_plugin_manager(self, manager)

    def _unbind_virtual_subscriptions(self) -> None:
        """Disconnect previous parent/manager callbacks on replacement or close."""
        return provider_sessions._unbind_virtual_subscriptions(self)

    def _sync_virtual_subscriptions(self, services: dict[str, object]) -> None:
        return provider_sessions._sync_virtual_subscriptions(self, services)

    def _queue_virtual_collection_refresh(self) -> None:
        """Coalesce bursts; re-read the latest cached snapshot on a Core worker."""
        return provider_sessions._queue_virtual_collection_refresh(self, executor=_SEARCH_EXECUTOR)

    def _drain_virtual_collection_events(self) -> None:
        return provider_sessions._drain_virtual_collection_events(self)

    def subscribe_virtual_sources(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Observe accepted inventory changes. Callback runs on the caller/worker.

        GTK consumers must marshal UI changes using GLib.idle_add.
        Invalid snapshots and duplicate notifications do not emit an update.
        """
        return provider_sessions.subscribe_virtual_sources(self, callback)

    def _connect_changed_virtual_provider(self, provider_id: str) -> None:
        """Connect only one newly published public adapter; keep other sessions."""
        return provider_sessions._connect_changed_virtual_provider(self, provider_id)

    def refresh_virtual_collections(self, *, connect_changed: bool = False) -> list[str]:
        """Reconcile cached inventories without any implicit discovery HTTP.

        Notifications invoke this on a worker and connect only added/replaced
        children. An explicit call can defer connections to connect_configured.
        Unrelated provider sessions and local catalogue/history remain intact.
        """
        return provider_sessions.refresh_virtual_collections(self, connect_changed=connect_changed)

    @property
    def plugin_manager(self) -> Any | None:
        """Public-ish bridge used by the shell until the UI owns PluginContext directly."""
        return self._plugin_manager

    def _provider_plugin_status(self, provider_id: str) -> Any | None:
        return provider_sessions._provider_plugin_status(self, provider_id)

    def _provider_enabled_for_identity(self, provider_id: str, identity_id: str | None = None) -> bool:
        return provider_sessions._provider_enabled_for_identity(self, provider_id, identity_id)

    def provider_descriptors(self):
        return provider_sessions.provider_descriptors(self)

    def provider_diagnostic_state(self) -> dict[str, Any]:
        return provider_sessions.provider_diagnostic_state(self)

    def close(self) -> None:
        return provider_sessions.close(self)

    def disconnect_configured(self) -> None:
        return provider_sessions.disconnect_configured(self)

    def connect_kobo_preview(self, credential_id: str) -> ProviderSession:
        """Connect only one configured Kobo session for explicit manifest preview.

        Never connect Paris/other accounts or start any acquisition. Offline
        previews deliberately do not call this method.
        """
        return provider_sessions.connect_kobo_preview(self, credential_id)

    def connect_configured(self) -> dict[str, Any]:
        """Connect all enabled provider identities from local preferences.

        Failures are isolated per provider account: one unavailable source does
        not prevent another source from starting. At least one logical account
        must be available for the current account-oriented GUI. Anonymous
        catalogue-only providers will be included once their plugin is ready.
        """
        return provider_sessions.connect_configured(self, logger=_TRACE_LOG)

    def login(self, username: str, password: str) -> dict[str, Any]:
        return provider_sessions.login(self, username, password)

    def select_account(self, unique_id: str, provider_id: str | None = None, credential_id: str | None = None) -> Account:
        return provider_sessions.select_account(self, unique_id, provider_id, credential_id)

    def _session_for_account(self, account: Account | None = None) -> ProviderSession:
        return provider_sessions._session_for_account(self, account)

    def _session_for_source(self, provider_id: str, credential_id: str | None = None) -> ProviderSession:
        return provider_sessions._session_for_source(self, provider_id, credential_id)

    def _key(self, kind: str, suffix: str = "") -> str:
        account = self.current_account
        uid = account.unique_id or "primary"
        prefix = f"{account.provider_id}:{account.credential_id}:{uid}"
        return f"{prefix}:{kind}:{suffix}" if suffix else f"{prefix}:{kind}"

    def _loans_for_account(self, account: Account, *, force: bool = False) -> list[Loan]:
        uid = account.unique_id or "primary"
        key = f"{account.provider_id}:{account.credential_id}:{uid}:loans"
        ttl = int(self.config.get("cache.dashboard_seconds", 300))
        if not force:
            cached = self.storage.cache_get("account", key)
            if isinstance(cached, list):
                rows = [Loan(**x) for x in cached]
                for row in rows:
                    row.identity_id = account.identity_id
                    if row.source_account_id is None:
                        row.source_account_id = account.unique_id
                self._record_loan_statistics(account, rows, from_cache=True)
                return rows
        session = self._session_for_account(account)
        if not session.capabilities.loans:
            return []
        rows = session.loans(account)
        for row in rows:
            row.identity_id = account.identity_id
            if row.source_account_id is None:
                row.source_account_id = account.unique_id
        serial = []
        for row in rows:
            d = asdict(row); d["raw"] = {}
            serial.append(d)
        self.storage.cache_put("account", key, serial, ttl)
        self._record_loan_statistics(account, rows)
        return rows

    def refresh_loans(self, force: bool = False) -> list[Loan]:
        self.last_loans = self._loans_for_account(self.current_account, force=force)
        self.diagnostics.add("CACHE", "loans", detail={"count": len(self.last_loans), "identity": self.current_account.identity_id})
        if force:
            self.refresh_adept_inventory(self.current_account.identity_id)
        return self.last_loans

    def refresh_identity_loans(self, force: bool = False, identity_id: str | None = None) -> list[Loan]:
        identity_id = identity_id or (self.current_identity.identity_id if self.current_identity else None)
        rows: list[Loan] = []
        seen: set[tuple[str, str, str, str]] = set()
        accounts = self.accounts_for_identity(identity_id)
        # 0.8.2 opening policy: Paris library accounts first, then the other
        # providers in stable order. A failure remains isolated by the caller.
        accounts = sorted(accounts, key=lambda a: (0 if a.provider_id == "paris" else 1, a.provider_id, a.credential_id, a.unique_id))
        for account in accounts:
            session = self._session_for_account(account)
            if not session.capabilities.loans:
                continue
            for loan in self._loans_for_account(account, force=force):
                key = (loan.source_provider, loan.source_credential, str(loan.source_account_id or ""), loan.id)
                if key not in seen:
                    seen.add(key); rows.append(loan)
        rows.sort(key=lambda row: (str(row.due_at or "9999"), row.title.casefold()))
        self.last_loans = rows
        if force and identity_id:
            self.refresh_adept_inventory(identity_id)
        return rows

    def public_catalogue_target(self, row):
        """Return ('notice'|'catalogue', URL) without mislabeling a generic page."""
        from .catalogue_link import catalogue_notice_url, catalogue_url

        provider = str(row.get('provider_id') or row.get('source_provider') or '')
        descriptor = next((d for d in self.provider_descriptors() if d.id == provider), None)
        base = descriptor.homepage if descriptor else None
        if provider == 'paris':
            base = self.config.paris_base_url(row.get('source_credential'))
        elif provider == 'kobo' and not base:
            base = self.config.provider_option(
                'kobo', 'catalogue_url', 'https://www.kobo.com',
                credential_id=row.get('source_credential'),
            )

        notice = catalogue_notice_url(row, base=base, provider=provider)
        if notice:
            return ('notice', notice)

        catalogue = catalogue_url({'friendly_url': base})
        if not catalogue:
            # This is a generic catalogue homepage, not a fabricated book notice.
            fallback = self.config.get('library.public_catalogue_fallback', 'https://catalogue.bnf.fr')
            catalogue = catalogue_url({'friendly_url': fallback})
        return ('catalogue', catalogue) if catalogue else None

    def public_catalogue_url(self, row):
        """Compatibility URL broker; use public_catalogue_target for UI labels."""
        target = LibraryService.public_catalogue_target(self, row)
        return target[1] if target else None

    def _provider_loan_active_for_acquisition(self, row):
        """Only the observed provider loan of this exact account/item can conflict."""
        metadata = row.get('metadata') or {}
        ids = {str(metadata.get('provider_loan_id') or '')}
        for entitlement in self.storage.entitlements(provider_id=row.get('provider_id')):
            if entitlement.get('entitlement_id') == row.get('entitlement_id'):
                ids.add(str(entitlement.get('loan_id') or ''))
        return any(str(loan.id) in ids and bool(str(loan.id))
                   and loan.source_provider == row.get('provider_id')
                   and loan.source_credential == row.get('credential_id')
                   and str(loan.source_account_id or '') == str(row.get('account_id') or '')
                   and loan.identity_id == row.get('identity_id')
                   for loan in self.last_loans)

    def refresh_adept_inventory(self, identity_id):
        # A refresh reads states only, never acquires or returns a book.
        try:
            with InterProcessLock(acquisition_lock_file()):
                summary = self._reconcile_identity_adept_inventory(identity_id)
        except LockBusyError:
            summary = {"resolved": 0, "unresolved": 0, "errors": 1, "busy": True}
        self.diagnostics.add("ADEPT", "inventory refresh", "warning" if summary["errors"] else "ok", detail=summary, level="normal")
        return summary

    def refresh_history(self, force: bool = False) -> list[HistoryItem]:
        return library_projection.refresh_history(self, force)

    @staticmethod
    def _history_fold(value: str | None) -> str:
        return library_projection._history_fold(value)

    @classmethod
    def _history_cover_score(
        cls,
        item_title: str,
        item_author: str | None,
        item_year: str | None,
        record_id: str | None,
        candidate: SearchResult,
    ) -> float:
        return library_projection._history_cover_score(cls, item_title, item_author, item_year, record_id, candidate)

    @classmethod
    def _history_search_queries(cls, title: str) -> list[str]:
        """Construit au plus trois requêtes, du plus précis au plus tolérant.

        Syracuse peut indexer un titre différemment de celui renvoyé par
        ``ListHandings`` (sous-titre, ponctuation, mention de volume). On ne
        journalise jamais le texte de ces requêtes dans les diagnostics.
        """
        return library_projection._history_search_queries(cls, title)

    def resolve_history_thumbnail(
        self,
        provider: str,
        record_id: str | None,
        title: str,
        author: str | None = None,
        year: str | None = None,
        default_url: str | None = None,
    ) -> tuple[str | None, str]:
        """Résout une couverture manquante de l'historique via le catalogue.

        0.2.6 corrige deux défauts de 0.2.5 : les résultats négatifs ne sont
        plus figés pendant sept jours, et le matching ne jette plus les
        notices qui n'exposent pas directement une image dans le résultat de
        recherche. Une notice fiable sans image est enrichie via ``holdings``
        *avec l'identifiant issu de Search.svc*, qui est exploitable.
        """
        return library_projection.resolve_history_thumbnail(self, provider, record_id, title, author, year, default_url)

    def refresh_bookings(self, force: bool = False) -> list[Booking]:
        account = self.current_account
        epoch = self._account_context_epoch
        key = self._key("bookings")
        if not force:
            cached = self.storage.cache_get("account", key)
            if isinstance(cached, list):
                rows = [Booking(**x) for x in cached]
                if self._account_context_epoch == epoch and self.current_account is account:
                    self.last_bookings = rows
                return rows
        session = self._session_for_account(account)
        if not session.capabilities.bookings:
            if self._account_context_epoch == epoch and self.current_account is account:
                self.last_bookings = []
            return []
        rows = session.bookings(account)
        serial = []
        for row in rows:
            d = asdict(row); d["raw"] = {}
            serial.append(d)
        self.storage.cache_put("account", key, serial, 300)
        if self._account_context_epoch == epoch and self.current_account is account:
            self.last_bookings = rows
        return rows

    def provider_display_name(self, provider_id: str) -> str:
        plugin = self.registry.maybe_get(str(provider_id or "").strip().lower())
        if plugin is not None:
            return plugin.descriptor.display_name
        return str(provider_id or "Source inconnue")

    @classmethod
    def _search_query_variants(cls, query: str) -> list[str]:
        """Return a small set of progressively looser catalogue queries.

        Several upstream catalogues treat ``"Zola Germinal"`` as a phrase or
        an AND expression and may return nothing although title and author are
        indexed separately.  Keep the exact query first, then try meaningful
        halves/tokens.  The set is deliberately bounded to avoid turning one UI
        search into an unbounded burst of provider calls.
        """
        return search_orchestration._search_query_variants(cls, query)

    @classmethod
    def _search_relevance(cls, query: str, row: SearchResult) -> int:
        return search_orchestration._search_relevance(cls, query, row)

    @staticmethod
    def _normalized_language_values(value: Any) -> list[str]:
        return search_orchestration._normalized_language_values(value)

    def _search_preference_score(self, row: SearchResult) -> int:
        return search_orchestration._search_preference_score(self, row)

    def availability_display_label(self, item: SearchResult, detail: ItemDetail) -> str:
        """Translate provider availability into a user-actionable label."""
        return search_orchestration.availability_display_label(self, item, detail)

    def searchable_providers(self) -> list[tuple[str, str]]:
        """Connected provider ids/display names usable by the global search UI."""
        return search_orchestration.searchable_providers(self)

    @staticmethod
    def _session_search_capabilities(session: ProviderSession) -> ProviderSearchCapabilities:
        """Resolve search capabilities while tolerating legacy/provider test doubles."""
        return search_orchestration._session_search_capabilities(session)

    def provider_search_capabilities(self) -> dict[str, ProviderSearchCapabilities]:
        """Return declared search limits for connected providers, keyed by provider id."""
        return search_orchestration.provider_search_capabilities(self)

    @staticmethod
    def _search_contract_warning(session: ProviderSession, field: str, scope: str) -> str:
        return search_orchestration._search_contract_warning(session, field, scope, facade_type=LibraryService)

    @classmethod
    def _field_match(cls, field: str, query: str, row: SearchResult) -> bool:
        return search_orchestration._field_match(cls, field, query, row)

    def _without_redundant_merged(
        self, sessions: list[ProviderSession], *, field: str, scope: str,
        include_redundant_merged: bool = False,
    ) -> list[ProviderSession]:
        """Prefer original sources over synthetic.merged in a federated query.

        Suppression is scoped to one virtual collection parent: unrelated Core
        providers are never hidden. A merged provider remains selectable alone
        (or together via an explicit opt-in). Only origins able to satisfy the
        requested field/scope suppress their sibling aggregate.
        """
        return search_orchestration._without_redundant_merged(self, sessions, field=field, scope=scope, include_redundant_merged=include_redundant_merged)

    def search(self, query: str, scope: str = "all", force: bool = False, *, provider_id: str = "all", field: str = "all", content_types: tuple[str, ...] | list[str] | None = None, include_redundant_merged: bool = False) -> list[SearchResult]:
        return search_orchestration.search(self, query, scope, force, provider_id=provider_id, field=field, content_types=content_types, include_redundant_merged=include_redundant_merged, logger=_TRACE_LOG, facade_type=LibraryService)

    def _search_provider_session(self, session: ProviderSession, query: str, scope: str, *, field: str = "all", content_types: tuple[str, ...] = ()) -> tuple[list[SearchResult], list[str], str, str]:
        """Search one provider without mutating shared result state.

        This is deliberately safe to run in parallel with *other* provider sessions;
        a given session is still called sequentially for its own query variants.
        """
        return search_orchestration._search_provider_session(self, session, query, scope, field=field, content_types=content_types, facade_type=LibraryService)

    def _search_virtual_stream_session(
        self, session: ProviderSession, query: str, scope: str, *,
        field: str, content_types: tuple[str, ...], generation: int,
        publish: Callable[[ProviderSearchEvent], None], cancelled: Callable[[], bool],
        control: Any,
    ) -> tuple[list[SearchResult], list[str], str, str]:
        """Worker-only optional stream. Gaps/errors do not poison the cache."""
        return search_orchestration._search_virtual_stream_session(self, session, query, scope, field=field, content_types=content_types, generation=generation, publish=publish, cancelled=cancelled, control=control)

    def search_progressive(
        self, query: str, scope: str = "all", force: bool = False, *,
        provider_id: str = "all", provider_ids: tuple[str, ...] | list[str] | None = None,
        field: str = "all", content_types: tuple[str, ...] | list[str] | None = None,
        on_batch: Callable[[Any], None] | None = None, control: Any = None,
        on_event: Callable[[ProviderSearchEvent], None] | None = None,
        include_redundant_merged: bool = False,
    ):
        """Parallel federated search with provider-scoped progressive batches.

        Providers keep their simple synchronous ``search`` contract.  Core owns
        concurrency, partial-failure isolation, cancellation checks, mirroring and
        final caching. Optional virtual providers emit validated stream events
        through ``on_event``; callbacks run on the search caller thread, never
        on provider threads. Existing ``on_batch`` remains compatible.
        By default synthetic.merged is skipped when searchable sibling origins
        are already selected, preventing duplicated remote work. Explicit
        ``include_redundant_merged=True`` opts in to both.
        """
        return search_orchestration.search_progressive(self, query, scope, force, provider_id=provider_id, provider_ids=provider_ids, field=field, content_types=content_types, on_batch=on_batch, control=control, on_event=on_event, include_redundant_merged=include_redundant_merged, executor=_SEARCH_EXECUTOR, logger=_TRACE_LOG)

    def search_local_library(
        self, query: str, scope: str = "all", *, field: str = "all", identity_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return search_orchestration.search_local_library(self, query, scope, field=field, identity_id=identity_id, logger=_TRACE_LOG)

    def search_known_catalog(
        self, query: str, scope: str = "all", *, field: str = "all", identity_id: str | None = None,
        provider_ids: list[str] | tuple[str, ...] | None = None,
        content_types: list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Search the already-known SQLite universe without provider I/O.

        This is the first stage of the Search screen: local artifacts, current
        rights and progressively mirrored catalogue facts. ``search()`` remains
        the explicit online extension operation.
        """
        return search_orchestration.search_known_catalog(self, query, scope, field=field, identity_id=identity_id, provider_ids=provider_ids, content_types=content_types, logger=_TRACE_LOG)

    @staticmethod
    def filter_loaded_search_results(
        rows: list[dict[str, Any]], query: str = "", *, field: str = "all", scope: str = "all",
        provider_ids: list[str] | tuple[str, ...] | None = None,
        content_types: list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Apply current Search controls without network or cache mutation."""
        return search_orchestration.filter_loaded_search_results(rows, query, field=field, scope=scope, provider_ids=provider_ids, content_types=content_types)

    def _mirror_remote_results(self, rows: list[SearchResult], *, identity_id: str | None = None) -> int:
        """Persist progressively discovered remote catalog metadata (0.8.3)."""
        return search_orchestration._mirror_remote_results(self, rows, identity_id=identity_id)

    def kobo_personal_right_states(self, identity_id: str) -> dict[tuple[str, str], str]:
        """Read only confirmed Kobo possession for the selected identity."""
        from .core.kobo_manifest_rights import kobo_personal_right_states
        return kobo_personal_right_states(self, identity_id)

    def mirror_personal_libraries(self) -> dict[str, int]:
        """Refresh owned-store inventory without initiating local acquisition.

        personal_library and direct_download describe different provider
        contracts. The latter authorizes explicit direct acquisition of a
        selected SearchResult, not bulk download_library_item backups.
        In particular Numilog does not implement that backup contract (#1862).
        """
        from .core.personal_library_mirror import PersonalLibraryMirrorCounts
        result = PersonalLibraryMirrorCounts()
        for session in self._sessions.values():
            if not session.connected or not session.capabilities.personal_library:
                continue
            provider_id = str(session.provider_id)
            credential_id = str(session.credential_id)
            account = next((
                a for a in self.accounts
                if a.provider_id == provider_id and a.credential_id == credential_id
            ), None)
            if account is not None and not self.sync_source_enabled(account):
                continue
            if provider_id == "numilog":
                # The connected session alone is NOT ownership. Verify one
                # enabled durable account/identity before reading private
                # pages; no current_identity fallback (#1905).
                from .core.numilog_manifest_rights import numilog_inventory_binding_valid
                if account is None or not numilog_inventory_binding_valid(
                    self, account, session=session,
                ):
                    result.blocked_sources += 1
                    _TRACE_LOG.warning(
                        "TRACE catalog personal-library inventory skipped "
                        "provider=numilog reason=source_binding_unavailable"
                    )
                    continue
                identity = self.identity_by_id(account.identity_id)
            elif provider_id == "kobo":
                # An authenticated session alone is not an authorized source.
                # The opening mirror must not fetch a disconnected/rebound or
                # orphan Kobo account, or project its purchases into whichever
                # identity happens to be selected (#1346).
                accounts = [
                    a for a in self.accounts
                    if a.provider_id == "kobo" and a.credential_id == credential_id
                ]
                try:
                    bound_identity = self.storage.identity_for_connection(
                        "kobo", credential_id,
                    )
                    connections = (
                        self.storage.provider_connections(identity_id=bound_identity)
                        if bound_identity else []
                    )
                except Exception:
                    bound_identity = None
                    connections = []
                bound_connections = [
                    row for row in connections
                    if row.get("provider_id") == "kobo"
                    and row.get("credential_id") == credential_id
                    and row.get("enabled") is True
                ]
                identity = self.identity_by_id(bound_identity) if bound_identity else None
                if (
                    len(accounts) != 1
                    or identity is None
                    or accounts[0].identity_id != bound_identity
                    or len(bound_connections) != 1
                ):
                    result.blocked_sources += 1
                    _TRACE_LOG.warning(
                        "TRACE catalog personal-library inventory skipped "
                        "provider=kobo reason=source_binding_unavailable"
                    )
                    continue
            else:
                # Preserve the behavior of other providers; their contracts
                # require separate binding qualification before stricter rules.
                identity = self.identity_for_connection(provider_id, credential_id)
            try:
                # One-shot iterators feed the owned-rights projection and
                # remote catalog exactly once. Neither phase downloads files.
                rows = list(session.library())
                if provider_id == "numilog" and not numilog_inventory_binding_valid(
                    self, account, session=session,
                ):
                    # A connection can be rebound while private pages are
                    # fetched. Discard the entire stale response, not just its
                    # rights: otherwise another identity may see its metadata.
                    result.blocked_sources += 1
                    _TRACE_LOG.warning(
                        "TRACE catalog personal-library inventory skipped "
                        "provider=numilog reason=source_binding_changed"
                    )
                    continue
                if provider_id == "numilog":
                    from .core.numilog_private_rows import private_library_rows_valid
                    if not private_library_rows_valid(rows, credential_id):
                        # Reject a mixed public/foreign-account response in its
                        # entirety; none may be persisted under this identity.
                        result.blocked_sources += 1
                        _TRACE_LOG.warning(
                            "TRACE catalog personal-library inventory skipped "
                            "provider=numilog reason=invalid_private_rows"
                        )
                        continue
                if provider_id == "kobo":
                    # A source may be disabled/rebound or replaced while
                    # library() fetches remote pages. Never project the stale
                    # response into entitlements or the catalog (#1346).
                    current_accounts = [
                        a for a in self.accounts
                        if a.provider_id == "kobo"
                        and a.credential_id == credential_id
                    ]
                    current_identity = self.storage.identity_for_connection(
                        "kobo", credential_id,
                    )
                    current_connections = (
                        self.storage.provider_connections(identity_id=current_identity)
                        if current_identity else []
                    )
                    if (
                        len(current_accounts) != 1
                        or account is None
                        or current_accounts[0].identity_id != identity.identity_id
                        or current_accounts[0].unique_id != account.unique_id
                        or current_identity != identity.identity_id
                        or sum(
                            row.get("provider_id") == "kobo"
                            and row.get("credential_id") == credential_id
                            and row.get("enabled") is True
                            for row in current_connections
                        ) != 1
                        or not self.sync_source_enabled(current_accounts[0])
                        or self._sessions.get(
                            self._session_key("kobo", credential_id)
                        ) is not session
                        or session.connected is not True
                    ):
                        result.blocked_sources += 1
                        _TRACE_LOG.warning(
                            "TRACE catalog personal-library inventory skipped "
                            "provider=kobo reason=source_binding_changed"
                        )
                        continue
                if provider_id == "kobo" and account is not None:
                    from .core.kobo_manifest_rights import mirror_kobo_manifest_rights
                    mirror_kobo_manifest_rights(
                        self, rows, identity_id=identity.identity_id,
                        credential_id=credential_id,
                        account_id=account.unique_id or None,
                    )
                elif provider_id == "numilog" and account is not None:
                    from .core.numilog_manifest_rights import mirror_numilog_manifest_rights
                    mirror_numilog_manifest_rights(
                        self, rows, identity_id=identity.identity_id,
                        credential_id=credential_id,
                        account_id=account.unique_id or None,
                    )
                if provider_id == "numilog" and not numilog_inventory_binding_valid(
                    self, account, session=session,
                ):
                    # Rights projection can itself take time; do not publish
                    # its stale catalogue snapshot after source revocation.
                    result.blocked_sources += 1
                    _TRACE_LOG.warning(
                        "TRACE catalog personal-library inventory skipped "
                        "provider=numilog reason=source_binding_changed"
                    )
                    continue
                result[f"{provider_id}:{credential_id}"] = self._mirror_remote_results(
                    rows, identity_id=identity.identity_id,
                )
            except Exception as exc:
                result.failed_sources += 1
                # Do not print a provider exception: it can contain a signed
                # ACSM URL, cookie, private title or credential.
                _TRACE_LOG.warning(
                    "TRACE catalog personal-library inventory error "
                    "provider=%s reason=%s",
                    provider_id, type(exc).__name__,
                )
        return result

    def _acquire_personal_library_item(self, session, row, identity_id: str) -> dict[str, Any]:
        """Deprecated fail-closed backup shim; never called by mirroring.

        The old method passed Numilog SearchResult dictionaries to the
        unsupported ProviderSession.download_library_item contract, then
        swallowed errors as if opening acquisition had succeeded. A per-book
        user action must instead use the existing direct acquisition broker,
        which validates the provider, account and ACSM/ADEPT path.
        """
        return {"status": "blocked", "reason": "explicit_acquisition_required"}

    def recent_acquisitions(self, *, identity_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent local materializations, newest first.

        The indexes below deliberately avoid the historical N+1 pattern where
        every visible placement re-read the full Kobo backup and entitlement
        tables solely to resolve a cover.
        """
        return library_projection.recent_acquisitions(self, identity_id=identity_id, limit=limit)

    def local_book_path(self, placement_id: str, *, identity_id: str) -> Path:
        """Resolve an inventoried file in the explicit identity scope, no acquisition."""
        return library_projection.local_book_path(self, placement_id, identity_id=identity_id)

    def local_placement_for_source(self, *, provider_id, credential_id, item_id, identity_id):
        """Find only an acquisition-attested unique placement, never by title."""
        return library_projection.local_placement_for_source(self, provider_id=provider_id, credential_id=credential_id, item_id=item_id, identity_id=identity_id)

    def local_placements_for_sources(self, identity_id):
        """One snapshot for search; only unique acquisition-attested placements."""
        return library_projection.local_placements_for_sources(self, identity_id)

    def materialize_to_identity(self, placement_id: str, target_identity_id: str) -> dict[str, Any]:
        """Materialize an existing Artifact for another Identity without reacquisition.

        0.9.0.1 deliberately performs targeted catalog registration here. A full
        catalog scan after a one-file hardlink/copy made this visible user action
        proportional to the entire library size.
        """
        return library_mutation.materialize_to_identity(self, placement_id, target_identity_id)

    def holdings(self, item: SearchResult, search_terms: str = "", force: bool = False) -> ItemDetail:
        key = f"{item.source_provider}:{item.source_credential}:{item.scenario}:{item.docbase}:{item.record_id}"
        ttl = int(self.config.get("cache.holdings_seconds", 300))
        if not force:
            cached = self.storage.cache_get("holdings", key)
            if isinstance(cached, dict):
                cached["holdings"] = [Holding(**x) for x in cached.get("holdings", [])]
                return ItemDetail(**cached)
        detail = self._session_for_source(item.source_provider, item.source_credential).holdings(item, search_terms)
        self.storage.cache_put("holdings", key, asdict(detail), ttl)
        return detail

    def renew(self, loan: Loan) -> dict[str, Any]:
        # Mutations must follow the borrower carried by the loan, not whichever
        # account happens to be selected in the GUI when the user clicks.
        account = self._account_for_loan(loan)
        session = self._session_for_source(loan.source_provider, loan.source_credential)
        # Les objets bruts Syracuse ne sont volontairement pas persistés dans le cache.
        # Recharge donc le prêt depuis le bon compte si nécessaire.
        if not loan.raw and session.capabilities.loans:
            fresh = session.loans(account)
            loan = next((x for x in fresh if x.id == loan.id), loan)
        result = session.renew(loan, account)
        self.storage.cache_delete_namespace("account")
        return result

    def _return_loan_identity(self, loan: Loan) -> tuple[Account, Any, str]:
        """Require *exact* Paris borrower provenance before mutating anything."""
        return loan_accounts.return_loan_identity(
            loan, resolve_account=self._account_for_loan, session_key=self._session_key,
            lookup_session=lambda key: self._sessions.get(key), attention_error=AttentionRequired,
        )

    def loan_return_state(self, loan: Loan) -> dict[str, Any] | None:
        """Persistent status for UI; no verification or provider side effect."""
        from .core.loan_return_guard import return_key, public_return_state
        if not loan.id or loan.source_account_id is None:
            return None
        key = return_key(
            loan.source_provider, loan.source_credential,
            str(loan.source_account_id or "primary"), str(loan.id),
        )
        row = self.storage.loan_return_state(key)
        return public_return_state(row) if row is not None else None

    def verify_return_digital(self, loan: Loan) -> dict[str, Any]:
        """Re-read the exact borrower with NO cache and NO second POST."""
        from .core.loan_return_guard import public_return_state, return_ref
        account, session, key = self._return_loan_identity(loan)
        checkpoint = self.storage.loan_return_state(key)
        if checkpoint is None:
            return {"status": "not_requested", "scope": "provider_pnb_only"}
        if checkpoint.get("status") == "provider_return_confirmed":
            return public_return_state(checkpoint)
        operation_id = str(checkpoint.get("operation_id") or "")
        if checkpoint.get("status") == "request_sent":
            # A just-dispatched POST may still be in-flight. Avoid racing its
            # response with a second verification. After a crash/long stall,
            # however, permit inventory-only recovery without re-sending POST.
            from datetime import datetime, timezone
            try:
                started = datetime.fromisoformat(str(checkpoint["requested_at"]))
                age = (datetime.now(timezone.utc) - started).total_seconds()
            except (KeyError, TypeError, ValueError):
                age = 0
            if age < 180:
                return public_return_state(checkpoint)
        phase = "inventory_checked"
        verification = "unavailable"
        status = "verification_unavailable"
        error_type = None
        try:
            loans = session.loans(account)  # ALWAYS direct provider; bypass cached account reads
            if not isinstance(loans, list):
                raise ValueError("provider inventory has no complete list")
            # Paris can recover from an unrelated SYRACUSE failure; a failed
            # DILICOM leg NEVER proves absence of a digital loan.
            upstream_errors = getattr(session, "last_loan_errors", None)
            if loan.source_provider == "paris" and upstream_errors is None:
                raise ValueError("Paris session lacks inventory-verification capability")
            if upstream_errors and any(
                str(e.get("upstream") or "") == "DILICOM" for e in upstream_errors
                if isinstance(e, dict)
            ):
                raise ValueError("digital-loan inventory was incomplete")
            match = any(
                candidate.id == loan.id and candidate.provider == loan.provider
                for candidate in loans
            )
            verification = "still_present" if match else "provider_absent"
            status = "still_active" if match else "provider_return_confirmed"
        except Exception as exc:
            error_type = type(exc).__name__
        row = self.storage.loan_return_transition(
            key, operation_id, status=status, verification=verification,
            error_type=error_type,
        )
        _TRACE_LOG.info(
            "TRACE return core phase=%s outcome=%s ref=%s checked_at=%s",
            phase, verification, return_ref(key),
            (row or {}).get("checked_at", ""),
        )
        return public_return_state(row or checkpoint)

    def verify_paris_return_inventory(self, loan: Loan) -> dict[str, str]:
        """Fresh exact-account read after ADEPT; never send ReturnDigitalLoan."""
        from .core.loan_return_guard import return_ref
        try:
            account, session, key = self._return_loan_identity(loan)
        except AttentionRequired:
            return {"status": "verification_unavailable", "verification": "unavailable"}
        try:
            rows = session.loans(account)
            if not isinstance(rows, list):
                raise ValueError("Paris inventory incomplete")
            errors = getattr(session, "last_loan_errors", None)
            if errors is None or any(
                isinstance(error, dict) and error.get("upstream") == "DILICOM"
                for error in errors
            ):
                raise ValueError("Paris digital inventory unverified")
            present = any(
                row.id == loan.id and row.provider == loan.provider for row in rows
            )
            verification = "still_present" if present else "provider_absent"
        except Exception:
            verification = "unavailable"
        _TRACE_LOG.info(
            "TRACE return core phase=paris_inventory outcome=%s ref=%s",
            verification, return_ref(key),
        )
        return {
            "verification": verification,
            "status": {
                "provider_absent": "provider_return_confirmed",
                "still_present": "still_active",
                "unavailable": "verification_unavailable",
            }[verification],
        }

    def _adobe_return_inventory_confirmed(self, acquisition: dict[str, Any]) -> bool:
        """Crash recovery: ADEPT inventory check ONLY, never replay mutation."""
        binding = (acquisition.get("metadata") or {}).get("adept_binding") or {}
        profile_id = binding.get("adept_profile_id")
        loan_id = binding.get("adept_loan_id")
        if not profile_id or not loan_id:
            return False
        try:
            profile = self._adept_profile_row(profile_id, self.kobo_profiles())
            if not profile:
                return False
            loans = self._kobo_adapter().loans(self._adept_profile_name(profile))
            matches = [r for r in loans["loans"] if r.get("id") == loan_id]
            return (
                len(matches) == 1
                and matches[0].get("returned") is True
                and matches[0].get("token_present") is False
            )
        except Exception:
            return False

    @staticmethod
    def _return_operation_lock_file(loan: Loan) -> Path:
        """Coordinate Adobe and PNB mutations across threads and processes.

        The lock is scoped by provider + upstream loan ID, not UI identity:
        the same Paris loan may be exposed by two different connections.
        No account identifiers or credentials are written in the lock path.
        """
        digest = hashlib.sha256(
            (str(loan.source_provider).casefold() + "\\0" + str(loan.id or "")).encode("utf-8")
        ).hexdigest()[:32]
        return acquisition_lock_file().with_name(
            "cifereca-return-" + digest + ".lock"
        )

    def return_loan_prefer_adept(self, loan: Loan) -> dict[str, Any]:
        if str(loan.source_provider).lower() != "paris" or loan.medium != "digital":
            return self.return_digital(loan)
        try:
            with InterProcessLock(self._return_operation_lock_file(loan)):
                return self._return_loan_prefer_adept_guarded(loan)
        except LockBusyError:
            return {
                "status": "attention_required", "scope": "adobe_first_paris",
                "code": "loan_return_busy", "retry": "verification_only",
            }

    def _return_loan_prefer_adept_guarded(self, loan: Loan) -> dict[str, Any]:
        """Adobe-first, then Paris inventory; never automatically send PNB POST.

        Durable claim is written *before* ADEPT. A retry, double-click or
        restart only reads ADEPT/Paris inventories: Adobe is never re-mutated.
        """
        import uuid
        from .core.loan_return_guard import return_key, return_ref
        from .core.return_adept_orchestrator import adept_return_key, public_state

        if str(loan.source_provider).lower() != "paris" or loan.medium != "digital":
            return self.return_digital(loan)
        try:
            account, _session, pnb_key = self._return_loan_identity(loan)
        except AttentionRequired as exc:
            return {
                "status": "failed", "scope": "adobe_first_paris",
                "code": exc.code, "retry": "resolve_identity",
            }
        borrower = str(account.unique_id or "primary")
        key = adept_return_key(
            loan.source_provider, loan.source_credential, borrower, str(loan.id)
        )
        source = self.adept_return_status_for_loan(loan)
        acquisition_id = source.get("acquisition_id")
        if not acquisition_id:
            reason = str(source.get("status") or "adept_return_binding_required")
            return {
                "status": "provider_action_available" if reason == "no_acquisition" else reason,
                "scope": "adobe_first_paris",
                "retry": "provider_action_explicit" if reason == "no_acquisition"
                         else "resolve_identity",
            }
        acquisition = self.storage.acquisition_get(acquisition_id)
        if acquisition is None:
            return {
                "status": "adept_return_binding_required",
                "scope": "adobe_first_paris", "retry": "resolve_identity",
            }

        # A previous PNB mutation must be settled separately; never send both.
        if self.storage.loan_return_state(pnb_key) is not None:
            return {
                "status": "partial", "scope": "adobe_first_paris",
                "reason": "provider_return_already_requested",
                "retry": "verification_only",
            }
        previous = self.storage.loan_return_state(key)
        if previous is None:
            if source.get("status") != "ready" or source.get("return_available") is not True:
                return {
                    "status": str(source.get("status") or "adept_return_state_required"),
                    "scope": "adobe_first_paris", "retry": "verification_only",
                }
            claimed, record = self.storage.loan_return_claim(key, uuid.uuid4().hex)
            if not claimed:
                previous = record
        if previous is not None:
            if previous.get("status") == "paris_return_confirmed":
                return public_state(previous)
            op_id = str(previous.get("operation_id") or "")
            if previous.get("adobe_verified") is not True:
                if not self._adobe_return_inventory_confirmed(acquisition):
                    return public_state(previous)
                record = self.storage.loan_return_transition(
                    key, op_id, status="adobe_return_confirmed",
                    verification="adobe_confirmed", adobe_verified=True,
                ) or previous
            else:
                record = previous
        else:
            op_id = str(record.get("operation_id") or "")
            if not op_id:
                return {"status": "failed", "scope": "adobe_first_paris",
                        "retry": "verification_only"}
            self.storage.loan_return_transition(
                key, op_id, status="adobe_return_requested",
                verification="adobe_pending", adobe_verified=False,
            )
            _TRACE_LOG.info(
                "TRACE return core phase=adobe_return_requested ref=%s",
                return_ref(key),
            )
            try:
                result = self.return_adept_acquisition(acquisition_id)
                if result.get("ok") is not True or result.get("verification") != "confirmed":
                    raise ValueError("ADEPT return not confirmed")
            except Exception as exc:
                record = self.storage.loan_return_transition(
                    key, op_id, status="failed", verification="adobe_unavailable",
                    adobe_verified=False, error_type=type(exc).__name__,
                )
                return public_state(record or {"status": "failed"})
            record = self.storage.loan_return_transition(
                key, op_id, status="adobe_return_confirmed",
                verification="adobe_confirmed", adobe_verified=True,
            )
            _TRACE_LOG.info(
                "TRACE return core phase=adobe_return_confirmed ref=%s",
                return_ref(key),
            )

        if not record or record.get("adobe_verified") is not True:
            return public_state(record or {"status": "failed"})
        op_id = str(record.get("operation_id") or "")
        self.storage.loan_return_transition(
            key, op_id, status="paris_check_pending",
            verification="not_checked", adobe_verified=True,
        )
        paris = self.verify_paris_return_inventory(loan)
        status = (
            "paris_return_confirmed"
            if paris["verification"] == "provider_absent" else "partial"
        )
        finished = self.storage.loan_return_transition(
            key, op_id, status=status,
            verification=paris["verification"], adobe_verified=True,
        )
        return public_state(finished or {
            "status": status, "adobe_verified": True,
            "verification": paris["verification"],
        })

    def verify_loan_return(self, loan: Loan) -> dict[str, Any]:
        """Read-only first inspection; restart recovery never mutates ADEPT."""
        from .core.return_adept_orchestrator import adept_return_key
        if str(loan.source_provider).lower() != "paris" or loan.medium != "digital":
            return {"status": "not_requested", "scope": "adobe_first_paris"}
        try:
            account, _session, _key = self._return_loan_identity(loan)
        except AttentionRequired as exc:
            return {"status": "failed", "scope": "adobe_first_paris", "code": exc.code}
        key = adept_return_key(
            loan.source_provider, loan.source_credential,
            str(account.unique_id or "primary"), str(loan.id),
        )
        if self.storage.loan_return_state(key) is None:
            return {"status": "not_requested", "scope": "adobe_first_paris"}
        return self.return_loan_prefer_adept(loan)

    def return_digital(self, loan: Loan, *, explicit_retry: bool = False) -> dict[str, Any]:
        if str(loan.source_provider).lower() != "paris":
            return self._return_digital_guarded(loan, explicit_retry=explicit_retry)
        try:
            with InterProcessLock(self._return_operation_lock_file(loan)):
                return self._return_digital_guarded(loan, explicit_retry=explicit_retry)
        except LockBusyError:
            return {
                "status": "attention_required", "scope": "provider_pnb_only",
                "code": "loan_return_busy", "retry": "verification_only",
            }

    def _return_digital_guarded(self, loan: Loan, *, explicit_retry: bool = False) -> dict[str, Any]:
        """PNB provider return with an at-most-once POST until verified.

        An HTTP success only means REQUEST ACCEPTED. No Adobe return is made.
        If a prior request exists, normal repeated clicks never send POST.
        An explicit retry may POST only after a fresh read proves still active.
        """
        import uuid
        from .core.loan_return_guard import public_return_state, return_ref
        if loan.source_provider != "paris":
            # Preserve legacy non-Paris providers; this guard is Paris-only.
            account = self._account_for_loan(loan)
            session = self._session_for_source(loan.source_provider, loan.source_credential)
            return session.return_digital(loan, account)
        account, session, key = self._return_loan_identity(loan)
        if self._cross_identity_loan_conflict(
            self.storage.acquisitions(provider_id=loan.source_provider),
            provider_loan_id=loan.id, identity_id=account.identity_id,
            entitlements=self.storage.entitlements(
                provider_id=loan.source_provider, state="active"
            ),
        ):
            return {
                "status": "attention_required", "scope": "provider_pnb_only",
                "code": "loan_return_identity_conflict",
                "retry": "resolve_identity",
            }
        from .core.return_adept_orchestrator import adept_return_key, public_state
        adobe_key = adept_return_key(
            loan.source_provider, loan.source_credential,
            str(account.unique_id or "primary"), str(loan.id),
        )
        adobe = self.storage.loan_return_state(adobe_key)
        if adobe is not None:
            if not explicit_retry or adobe.get("adobe_verified") is not True:
                return {
                    **public_state(adobe), "reason": "adobe_return_takes_precedence",
                }
            fresh = self.verify_paris_return_inventory(loan)
            if fresh.get("status") != "still_active":
                return fresh
        prior = self.storage.loan_return_state(key)
        if prior is not None:
            if not explicit_retry:
                return public_return_state(prior)
            verified = self.verify_return_digital(loan)
            if verified.get("status") != "still_active":
                return verified
        operation_id = uuid.uuid4().hex
        claimed, state = self.storage.loan_return_claim(
            key, operation_id, reopen=bool(prior and explicit_retry),
        )
        if not claimed:
            return public_return_state(state)
        _TRACE_LOG.info("TRACE return core phase=request_sent ref=%s", return_ref(key))
        try:
            response = session.return_digital(loan, account)
            if not isinstance(response, dict) or response.get("success") is not True:
                raise ValueError("Paris did not attest request acceptance")
        except Exception as exc:
            # Network failures are indeterminate: POST may already have been
            # accepted upstream. Never retry automatically on uncertainty.
            row = self.storage.loan_return_transition(
                key, operation_id, status="verification_unavailable",
                verification="unavailable", error_type=type(exc).__name__,
            )
            _TRACE_LOG.warning(
                "TRACE return core phase=request_uncertain ref=%s error_type=%s",
                return_ref(key), type(exc).__name__,
            )
            return public_return_state(row or state)
        row = self.storage.loan_return_transition(
            key, operation_id, status="pending_verification",
            verification="not_checked", accepted=True,
        )
        _TRACE_LOG.info(
            "TRACE return core phase=request_accepted ref=%s provider_inventory_verified=false",
            return_ref(key),
        )
        # Stale cached loans must not override verification after accepted POST.
        self.storage.cache_delete_namespace("account")
        return self.verify_return_digital(loan)



    def pnb_preferences(self) -> dict[str, Any]:
        return self._session_for_account().digital_preferences(self.current_account)

    def content_type_for(self, item: SearchResult) -> str:
        return self.acquisition.content_type_for(item)

    def default_downloads_dir(self) -> Path:
        return self.acquisition.target_dir("", "ebook")

    def _kobo_adapter(self):
        if self._plugin_manager is not None:
            try:
                status = self._plugin_manager.status("runtime.kobo-adept")
                service = self._plugin_manager.service("runtime.kobo-adept")
                if status.enabled and status.state == "ready" and service is not None:
                    return service
            except Exception:
                pass
        raise ProviderConfigurationError(
            "Le runtime Kobo-Adept n'est pas disponible. Installez/activez le plugin runtime.kobo-adept."
        )

    def _required_plugin_service(self, plugin_id: str) -> Any:
        manager = self._plugin_manager
        if manager is None:
            raise AttentionRequired(
                f"Le plugin {plugin_id} est requis pour cette opération.",
                code="plugin_required", technical_detail=f"missing manager/service {plugin_id}",
            )
        try:
            status = manager.status(plugin_id)
            service = manager.service(plugin_id)
        except Exception as exc:
            raise AttentionRequired(
                f"Le plugin {plugin_id} n'est pas disponible.",
                code="plugin_required", technical_detail=str(exc),
            ) from exc
        if not status.enabled or status.state != "ready" or service is None:
            detail = status.error or status.reason or status.state
            raise AttentionRequired(
                f"Le plugin {status.descriptor.display_name or plugin_id} doit être activé et prêt.",
                code="plugin_not_ready", technical_detail=f"{plugin_id}: {detail}",
            )
        return service

    @staticmethod
    def _http_plain_transport_proof(request: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize the Core proof required for direct HTTP dispatch."""
        handling_values: list[str] = []
        drm_labels: list[str] = []
        protected_attested = False

        for owner in (request, source):
            if "handling" in owner:
                handling = str(owner.get("handling") or "").strip().casefold()
                if handling:
                    handling_values.append(handling)
            if "drm" not in owner:
                continue
            drm = owner.get("drm")
            if isinstance(drm, dict):
                if "protected" in drm:
                    if drm.get("protected") is not False:
                        raise AttentionRequired(
                            "Cette source HTTP est protégée ou sa protection n’est pas explicitement levée.",
                            code="http_drm_not_plain",
                        )
                    protected_attested = True
                for key in ("raw_type", "family", "type"):
                    value = str(drm.get(key) or "").strip()
                    if value:
                        drm_labels.append(value)
            elif drm is None:
                raise AttentionRequired(
                    "Cette source HTTP ne fournit pas de preuve DRM exploitable.",
                    code="http_drm_attestation_required",
                )
            else:
                value = str(drm).strip()
                if value:
                    drm_labels.append(value)

        if not handling_values:
            raise AttentionRequired(
                "Cette source HTTP n’atteste pas le handling http_plain.",
                code="http_handling_attestation_required",
            )
        if any(value != "http_plain" for value in handling_values):
            raise AttentionRequired(
                "Cette source HTTP nécessite un traitement dédié.",
                code="http_handling_not_plain",
            )

        normalized_labels = [
            re.sub(r"[^a-z0-9]", "", value.casefold())
            for value in drm_labels
        ]
        allowed = {"none", "nodrm", "signednodrm"}
        if any(value not in allowed for value in normalized_labels):
            raise AttentionRequired(
                "Cette source HTTP nécessite un traitement DRM explicite.",
                code="http_drm_not_plain",
            )
        if not normalized_labels and not protected_attested:
            raise AttentionRequired(
                "Cette source HTTP n’atteste pas explicitement l’absence de DRM.",
                code="http_drm_attestation_required",
            )

        label = drm_labels[0] if drm_labels else "none"
        return {
            "handling": "http_plain",
            "drm": label,
            "protected": False,
            "attested": True,
        }

    def execute_acquisition_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Execute a normalized acquisition recipe through Plugin API services.

        The core owns policy/destination/ledger, never an external DRM binary.
        """
        # A direct LibraryService unit-test may inject a fake runtime without a
        # PluginManager. Production CiferecaCore always attaches the manager, so
        # this seam never revives the removed internal adapter fallback.
        if self._plugin_manager is None:
            try:
                injected = self._kobo_adapter()
            except Exception:
                injected = None
            if injected is not None and callable(getattr(injected, "download_request", None)):
                return dict(injected.download_request(request))

        source = request.get("source") if isinstance(request, dict) else None
        if not isinstance(source, dict):
            raise ProviderConfigurationError("Recette d'acquisition sans source")
        source_type = str(source.get("type") or "").lower()
        if source_type == "http":
            transport = self._required_plugin_service("transport.http")
            destination = Path(str(request.get("destination") or ""))
            if not destination.name:
                raise ProviderConfigurationError("Recette HTTP sans destination")
            # Core owns the DRM/handling boundary. The transport receives the
            # original source plus a normalized proof of the policy decision.
            proof = self._http_plain_transport_proof(request, source)
            fetch_source = dict(source)
            fetch_source["protection"] = proof
            fetch_source["headers"] = dict(source.get("headers") or {})
            if request.get("format") and not fetch_source.get("expected_format"):
                fetch_source["expected_format"] = str(request.get("format") or "").casefold()
            matches = getattr(transport, "matches", None)
            if callable(matches) and not matches(fetch_source):
                raise AttentionRequired("Le transport HTTP refuse cette source.", code="http_source_not_supported")
            path = Path(transport.fetch(fetch_source, destination))
            inspected = inspect_artifact(path)
            if not inspected.valid:
                raise ProviderConfigurationError(f"Artefact HTTP invalide: {inspected.reason or 'format inconnu'}")
            if inspected.protected is True:
                path.unlink(missing_ok=True)
                raise AttentionRequired(
                    "Le téléchargement HTTP contient encore une protection DRM.",
                    code="http_artifact_protected",
                )
            return {
                "ok": True, "operation": "download",
                "artifact": {
                    "path": str(path), "format": inspected.media_format or str(request.get("format") or ""),
                    "drm": "none", "size": path.stat().st_size, "sha256": sha256_file(path),
                },
                "source": {"type": "http"},
            }
        if source_type == "acsm":
            return dict(self._required_plugin_service("drm.ade").execute(request))
        if source_type == "kobo":
            return dict(self._required_plugin_service("drm.kobo-kdrm").execute(request))
        raise ProviderConfigurationError(f"Type de source d'acquisition non supporté: {source_type or 'unknown'}")

    def _originating_adept_profile_id(self, identity_id: str, profile_name: str) -> str | None:
        return adept_profiles.originating_profile_id(
            identity_id, profile_name,
            binding_get=self.storage.adept_profile_binding_get,
            list_profiles=self.kobo_profiles, name_of=self._adept_profile_name,
            reference_of=self._adept_profile_ref,
        )

    def _adept_return_capability(self) -> bool:
        try:
            payload = self._kobo_adapter().capabilities()
            return (payload.get("capabilities") or {}).get("adept", {}).get("loan_return") is True
        except Exception:
            return False

    def _adept_acquisition_path(self, row):
        paths = {str(r.get("metadata", {}).get("path")) for r in self.storage.catalog_entries()
                 if r.get("artifact_id") == row.get("artifact_id") and row.get("artifact_id")
                 and r.get("identity_id") == row.get("identity_id") and r.get("local_state") == "local"
                 and r.get("metadata", {}).get("path")}
        if len(paths) == 1:
            return Path(next(iter(paths)))
        if paths:
            return None
        raw = (row.get("metadata") or {}).get("path")
        return Path(raw) if raw else None

    def _adept_inventory_candidates(self, row, profiles, profile_references=None):
        binding = (row.get("metadata") or {}).get("adept_binding") or {}
        origin = binding.get("adept_profile_id")
        refs = [origin] if origin else (profile_references if profile_references is not None else
                    [self.storage.adept_profile_binding_get("identity", str(row.get("identity_id") or ""))])
        if not isinstance(refs, (list, tuple)) or len(refs) > 8:
            raise AttentionRequired("Huit profils explicitement désignés au maximum.", code="adept_profile_candidates_invalid")
        resolved = []
        for ref in dict.fromkeys(refs):
            if not ref:
                continue
            matches = [p for p in profiles if str(ref) in {str(p.get(k) or "") for k in ("profile_id", "id", "name", "profile")}]
            if len(matches) != 1:
                raise AttentionRequired("Profil ADEPT absent ou ambigu.", code="adept_origin_profile_missing")
            resolved.append(matches[0])
        return resolved

    def _reconcile_adept_acquisition(self, row, *, profile_references=None, cache=None):
        from .adept_inventory import match_local_book
        metadata = dict(row.get("metadata") or {})
        binding = dict(metadata.get("adept_binding") or {})
        path = self._adept_acquisition_path(row)
        if not path or not path.is_file():
            return {"status": "artifact_missing", "acquisition_id": row["acquisition_id"]}
        runtime = self._kobo_adapter()
        inventory_fn = getattr(runtime, "loan_inventories", None)
        if not callable(inventory_fn):
            raise AttentionRequired("Inventaire indisponible : runtime Kobo-Adept 1.2.2 requis.", code="adept_inventory_unavailable")
        cache = cache if cache is not None else {}
        if "profiles" not in cache:
            cache["profiles"] = self.kobo_profiles()
        profiles = cache["profiles"]
        candidates = self._adept_inventory_candidates(row, profiles, profile_references)
        if not candidates:
            return {"status": "profile_missing", "acquisition_id": row["acquisition_id"]}
        names = tuple(self._adept_profile_name(p) for p in candidates)
        if names not in cache:
            try:
                cache[names] = inventory_fn(list(names))
            except Exception:
                binding.update(inventory_status="inventory_error", return_available=False, return_verification="verification_failed")
                metadata["adept_binding"] = binding
                self.storage.acquisition_put({**row, "metadata": metadata})
                raise
        inventory = cache[names]
        matches = match_local_book(path, inventory)
        if binding.get("adept_loan_id"):
            exact = [{"profile": group["profile"], "loan": loan} for group in inventory.get("profiles", [])
                     for loan in group.get("loans", []) if loan["id"] == binding["adept_loan_id"]]
            if exact:
                matches = exact
        # A partial inventory cannot establish a globally unique binding.
        status = "inventory_error" if not inventory.get("complete") else ("unresolved" if not matches else ("ambiguous" if len(matches) != 1 else "resolved"))
        if status == "resolved":
            match = matches[0]; loan = match["loan"]
            profile = next(p for p in candidates if self._adept_profile_name(p) == match["profile"])
            # A prior authoritative ID is never replaced by a different candidate.
            if binding.get("adept_loan_id") and binding["adept_loan_id"] != loan["id"]:
                status = "binding_conflict"
            else:
                returned = loan["returned"]
                state = "returned" if returned else ("expired" if loan["expired"] else "active")
                binding.update(adept_profile_id=self._adept_profile_ref(profile), adept_profile_name=match["profile"],
                    adept_loan_id=loan["id"], loan_state=state, token_present=loan["token_present"],
                    expired=loan["expired"], validity=loan.get("validity"), inventory_status="resolved",
                    inventory_checked_at=int(time.time()), return_verification=("verification_failed" if loan["token_present"] else "confirmed") if returned else "active_confirmed", binding_source=binding.get("binding_source") if binding.get("binding_source") == "manual_selection" else "public_inventory",
                    return_available=bool(not returned and not loan["expired"] and loan["token_present"] and self._adept_return_capability()))
        if status != "resolved":
            # Keep historical IDs and files, record absence/ambiguity separately.
            binding.update(inventory_status=status, inventory_checked_at=int(time.time()), return_available=False, return_verification="verification_failed")
        metadata["adept_binding"] = binding
        self.storage.acquisition_put({**row, "metadata": metadata})
        self.diagnostics.add("ADEPT", "inventory reconciliation", "ok" if status == "resolved" else "warning",
                             detail={"status": status, "profiles": len(names), "matches": len(matches)}, level="normal")
        return {"status": status, "acquisition_id": row["acquisition_id"], "matches": len(matches), "return_available": binding.get("return_available", False)}

    def adept_loan_choices(self, acquisition_id, *, profile_references=None):
        row = self.storage.acquisition_get(acquisition_id)
        if not row or row.get("status") != "complete":
            raise AttentionRequired("Acquisition locale inconnue.", code="adept_acquisition_unknown")
        profiles = self.kobo_profiles()
        candidates = self._adept_inventory_candidates(row, profiles, profile_references)
        if not candidates:
            raise AttentionRequired("Associez un profil ADEPT à l’identité.", code="adept_profile_required")
        runtime = self._kobo_adapter()
        if not callable(getattr(runtime, "loan_inventories", None)):
            raise AttentionRequired("Runtime Kobo-Adept 1.2.2 requis.", code="adept_inventory_unavailable")
        inventory = runtime.loan_inventories([self._adept_profile_name(p) for p in candidates])
        provider_active = self._provider_loan_active_for_acquisition(row)
        options = []
        for group in inventory["profiles"]:
            profile = next(p for p in candidates if self._adept_profile_name(p) == group["profile"])
            for loan in group["loans"]:
                conflict = bool(loan['returned'] and (loan['token_present'] or provider_active))
                state = ("Retour à vérifier · jeton présent" if loan['token_present'] else "Restitué côté ADEPT · prêt fournisseur encore actif") if conflict else ("Restitué" if loan["returned"] else ("Expiré" if loan["expired"] else ("Actif" if loan["token_present"] else "Sans jeton")))
                options.append({"profile_id": self._adept_profile_ref(profile), "profile_name": group["profile"],
                    "loan_id": loan["id"], "title": loan.get("title") or "Titre non disponible",
                    "state": state, "conflict": conflict, "validity": loan.get("validity"), "local_book": loan.get("local_book")})
        return {"options": options, "complete": inventory["complete"]}

    def attach_adept_loan(self, acquisition_id, profile_id, loan_id):
        """Explicit user selection, revalidated from the selected public profile.

        A title is display information only; no automatic title/ISBN matching.
        No exact path match required for this manually confirmed relationship.
        """
        try:
            with InterProcessLock(acquisition_lock_file()):
                row = self.storage.acquisition_get(acquisition_id)
                if not row or row.get("status") != "complete":
                    raise AttentionRequired("Acquisition locale inconnue.", code="adept_acquisition_unknown")
                path = self._adept_acquisition_path(row)
                if not path or not path.is_file():
                    raise AttentionRequired("Le fichier local est absent ou ambigu.", code="adept_artifact_required")
                profiles = self.kobo_profiles()
                candidates = self._adept_inventory_candidates(row, profiles, [profile_id])
                selected = [p for p in candidates if self._adept_profile_ref(p) == profile_id]
                if len(selected) != 1:
                    raise AttentionRequired("Profil sélectionné incompatible avec la liaison d’origine.", code="adept_origin_profile_missing")
                profile = selected[0]; name = self._adept_profile_name(profile)
                loans = self._kobo_adapter().loans(name)["loans"]
                matches = [loan for loan in loans if loan["id"] == loan_id]
                if len(matches) != 1:
                    raise AttentionRequired("Le prêt sélectionné a disparu ou est ambigu. Actualisez la liste.", code="adept_loan_selection_stale")
                loan = matches[0]
                metadata = dict(row.get("metadata") or {}); previous = dict(metadata.get("adept_binding") or {})
                provider_active = self._provider_loan_active_for_acquisition(row)
                returned = loan["returned"]
                binding = {"adept_profile_id": profile_id, "adept_profile_name": name, "adept_loan_id": loan_id,
                    "loan_state": "returned" if returned else ("expired" if loan["expired"] else "active"),
                    "token_present": loan["token_present"], "expired": loan["expired"], "validity": loan.get("validity"),
                    "binding_source": "manual_selection", "manually_confirmed_at": int(time.time()),
                    "inventory_status": "resolved", "inventory_checked_at": int(time.time()), "return_verification": "verification_failed" if returned else "active_confirmed",
                    "provider_loan_active": provider_active,
                    "return_available": bool(not returned and not loan["expired"] and loan["token_present"] and self._adept_return_capability())}
                metadata["previous_adept_binding"] = previous
                metadata["adept_binding"] = binding
                self.storage.acquisition_put({**row, "metadata": metadata})
                return {"status": "resolved", "acquisition_id": acquisition_id, "return_available": binding["return_available"]}
        except LockBusyError as exc:
            raise AttentionRequired("Une acquisition est déjà en cours.", code="acquisition_busy") from exc

    def reconcile_adept_acquisition(self, acquisition_id, *, profile_references=None):
        row = self.storage.acquisition_get(acquisition_id)
        if not row or row.get("status") != "complete":
            raise AttentionRequired("Acquisition inconnue ou incomplète.", code="adept_acquisition_unknown")
        try:
            with InterProcessLock(acquisition_lock_file()):
                return self._reconcile_adept_acquisition(row, profile_references=profile_references)
        except LockBusyError as exc:
            raise AttentionRequired("Une acquisition est déjà en cours.", code="acquisition_busy") from exc

    def _reconcile_identity_adept_inventory(self, identity_id):
        cache = {}; summary = {"resolved": 0, "unresolved": 0, "errors": 0}
        for row in self.storage.acquisitions(status="complete"):
            if row.get("identity_id") != identity_id or not (row.get("metadata") or {}).get("adept_binding"):
                continue
            try:
                result = self._reconcile_adept_acquisition(row, cache=cache)
                summary["resolved" if result["status"] == "resolved" else "unresolved"] += 1
            except Exception:
                summary["errors"] += 1
        return summary

    def adept_return_for_artifact(self, artifact_id: str, identity_id: str) -> dict[str, Any] | None:
        if not artifact_id or not identity_id:
            return None
        for row in self.storage.acquisitions(status="complete"):
            if row.get("artifact_id") != artifact_id or row.get("identity_id") != identity_id:
                continue
            binding = (row.get("metadata") or {}).get("adept_binding") or {}
            if binding.get("return_available") and binding.get("loan_state") == "active" and self._adept_return_capability():
                return {"acquisition_id": row["acquisition_id"], "profile_name": binding["adept_profile_name"]}
        return None

    def return_adept_acquisition(self, acquisition_id: str) -> dict[str, Any]:
        try:
            with InterProcessLock(acquisition_lock_file()):
                return self._return_adept_acquisition(acquisition_id)
        except LockBusyError as exc:
            raise AttentionRequired("Une acquisition est déjà en cours.", code="acquisition_busy") from exc

    def _return_adept_acquisition(self, acquisition_id: str) -> dict[str, Any]:
        """Explicit return, exact origin/ID, fresh registry before and after CLI."""
        row = self.storage.acquisition_get(acquisition_id)
        if not row:
            raise AttentionRequired("Acquisition inconnue.", code="adept_acquisition_unknown")
        metadata = dict(row.get("metadata") or {})
        binding = dict(metadata.get("adept_binding") or {})
        if not binding.get("adept_loan_id") or not binding.get("adept_profile_id"):
            raise AttentionRequired("Liaison ADEPT et profil d’origine requis.", code="adept_return_binding_required")
        profile_row = self._adept_profile_row(binding["adept_profile_id"], self.kobo_profiles())
        if not profile_row:
            raise AttentionRequired("Le profil ADEPT d’origine est indisponible.", code="adept_origin_profile_missing")
        profile_name = self._adept_profile_name(profile_row)
        runtime = self._kobo_adapter()
        def persist():
            metadata["adept_binding"] = dict(binding)
            self.storage.acquisition_put({**row, "metadata": dict(metadata)})
        def read_exact():
            try:
                inventory = runtime.loans(profile_name)
                matches = [r for r in inventory["loans"] if r["id"] == binding["adept_loan_id"]]
                if len(matches) != 1 or any(type(matches[0].get(k)) is not bool for k in ("returned", "expired", "token_present")):
                    raise ValueError("loan missing or ambiguous")
                return matches[0]
            except Exception as exc:
                raise AttentionRequired("État ADEPT non confirmé. Vérifiez le prêt ; le fichier est conservé.", code="adept_return_verification_required") from exc
        try:
            fresh = read_exact()
        except Exception:
            binding.update(return_available=False, return_verification="verification_failed")
            persist(); raise
        binding.update(loan_state="returned" if fresh["returned"] else ("expired" if fresh["expired"] else "active"),
                       token_present=fresh["token_present"], expired=fresh["expired"],
                       inventory_status="resolved", inventory_checked_at=int(time.time()),
                       return_verification="confirmed" if fresh["returned"] else "active_confirmed")
        if fresh["returned"] and fresh["token_present"]:
            binding.update(return_available=False, return_verification="verification_failed")
            persist()
            self.diagnostics.add("ADEPT", "return verification", "warning", detail={"reason":"returned_with_token", "mutation":"skipped"}, level="normal")
            raise AttentionRequired("Registre marqué restitué, mais jeton ADEPT toujours présent. Restitution non confirmée ; vérifiez avec libgourou. Fichier conservé.", code="adept_return_token_conflict")
        if fresh["returned"]:
            binding["return_available"] = False; persist()
            return {"ok": True, "already_returned": True, "verification": "confirmed", "artifact_preserved": True}
        binding.pop("returned_at", None)
        binding["return_available"] = bool(not fresh["expired"] and fresh["token_present"] and self._adept_return_capability())
        persist()
        if not binding["return_available"]:
            raise AttentionRequired("Restitution indisponible pour l’état ADEPT courant.", code="adept_return_state_required")
        request = DrmMutationRequest(operation="return", identity_id=str(row.get("identity_id") or ""),
            provider_id=row["provider_id"], entitlement_id=str(row.get("entitlement_id") or ""),
            metadata={"adept_loan_id": binding["adept_loan_id"], "adept_profile_name": profile_name})
        binding.update(return_verification="pending", return_available=False); persist()
        try:
            result = self.execute_drm_mutation(request)
            returned = dict(result.get("metadata") or {}).get("runtime_result") or {}
            if (result.get("ok") is not True or returned.get("ok") is not True
                    or returned.get("operation") != "loan_return"
                    or returned.get("loan_id") != binding["adept_loan_id"] or returned.get("profile") != profile_name):
                raise ProviderConfigurationError("Restitution ADEPT non confirmée")
            after = read_exact()
            if not after["returned"] or after["token_present"]:
                raise AttentionRequired("Commande exécutée, mais restitution non confirmée dans ADEPT. Vérifiez le prêt ; fichiers conservés.", code="adept_return_verification_required")
        except Exception:
            binding.update(return_verification="verification_failed", return_available=False)
            persist(); raise
        binding.update(loan_state="returned", token_present=False, return_available=False, returned_at=int(time.time()), return_verification="confirmed")
        persist()
        return {"ok": True, "operation": "loan_return", "artifact_preserved": True, "verification": "confirmed"}

    def execute_drm_mutation(self, request: DrmMutationRequest) -> dict[str, Any]:
        """Dispatch a DRM mutation to a runtime plugin without provider coupling.

        0.9.3 establishes the contract; runtime.kobo-adept will implement renew
        and return in the following plugin release. Existing acquisition handlers
        remain source-compatible.
        """
        manager = self._plugin_manager
        if manager is None:
            raise AttentionRequired(
                "Aucun runtime DRM n'est disponible.", code="drm_runtime_required",
                technical_detail=f"operation={request.operation}",
            )
        for status in manager.candidates("runtime.adapter"):
            service = manager.service(status.descriptor.id)
            supports = getattr(service, "supports", None)
            execute = getattr(service, "execute", None)
            if not callable(supports) or not callable(execute):
                continue
            try:
                if not supports(request):
                    continue
                result = execute(request)
                if isinstance(result, dict):
                    return dict(result)
                if hasattr(result, "metadata"):
                    return {
                        "ok": bool(getattr(result, "ok", True)),
                        "operation": str(getattr(result, "operation", request.operation)),
                        "artifact": getattr(result, "artifact", None),
                        "metadata": dict(getattr(result, "metadata", {}) or {}),
                    }
                raise ProviderConfigurationError("Réponse runtime DRM invalide")
            except UserFacingError:
                raise
            except Exception as exc:
                self.diagnostics.add(
                    "PLUGIN", "drm mutation", "error",
                    detail={"plugin": status.descriptor.id, "operation": request.operation, "error": type(exc).__name__},
                )
                raise
        raise AttentionRequired(
            "Aucun runtime DRM compatible avec cette opération n'est disponible.",
            code="drm_operation_not_supported", technical_detail=f"operation={request.operation}",
        )

    def _import_acsm_impl(
        self, source: Path, *, identity_id: str | None = None, provider_id: str = "manual", title: str | None = None,
    ) -> dict[str, Any]:
        """Import a user ACSM without deleting the original file.

        The ACSM is copied into resumable staging, fulfilled through the DRM
        plugin layer, validated, then indexed as a normal local Artifact.
        """
        source = Path(source).expanduser().resolve()
        if not source.is_file() or source.suffix.casefold() != ".acsm":
            raise ProviderConfigurationError("Sélectionnez un fichier ACSM valide")
        identity = self.identity_by_id(identity_id) if identity_id else self.current_identity
        if identity is None:
            raise ProviderConfigurationError("Aucune Identity active pour l'import ACSM")
        source_sha = sha256_file(source)
        acquisition_id = f"manual-acsm:{hashlib.sha256((identity.identity_id + ':' + source_sha).encode()).hexdigest()[:32]}"
        existing = self.storage.acquisition_get(acquisition_id)
        if existing and existing.get("status") == "complete":
            path = Path(str(dict(existing.get("metadata") or {}).get("path") or ""))
            if path.is_file():
                return {"status": "unchanged", "path": str(path), "acquisition_id": acquisition_id}

        work = data_dir() / "acquisition-work" / identity.identity_id / "manual" / source_sha[:16]
        work.mkdir(parents=True, exist_ok=True)
        staged = work / "source.acsm"
        if work.is_symlink() or staged.is_symlink():
            raise ProviderConfigurationError("Zone de reprise ACSM ambiguë")
        if not staged.exists() or sha256_file(staged) != source_sha:
            shutil.copy2(source, staged)
        if sha256_file(staged) != source_sha:
            raise ProviderConfigurationError("Le fichier ACSM a changé pendant l’import")
        self.storage.acquisition_put({
            "acquisition_id": acquisition_id, "provider_id": provider_id, "credential_id": "manual",
            "identity_id": identity.identity_id, "item_id": source_sha, "title": title or source.stem,
            "status": "acquiring", "strategy": "manual_acsm", "executor": "drm.ade",
            "metadata": {"source_acsm_sha256": source_sha, "staging": str(staged)},
        })
        try:
            media_format = detect_acsm_format(staged) or "epub"
            suffix = ".pdf" if media_format == "pdf" else ".epub"
            # A title is presentation, never an artifact identity. Keep each
            # ACSM's outputs in its own namespace and refuse unindexed residue.
            root = self.library_identity_provider_dir(identity.identity_id, provider_id)
            destination_dir = root / ("acsm-" + source_sha)
            if destination_dir.is_symlink() or (destination_dir.exists() and not destination_dir.is_dir()):
                raise ProviderConfigurationError("Dossier d’import ACSM ambigu")
            destination_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            filename = self._loan_destination_name(title or source.stem, suffix)
            destination = destination_dir / filename
            if any(destination_dir.iterdir()):
                raise ProviderConfigurationError("Un artefact ACSM non attesté existe déjà ; réconciliation nécessaire")
            try:
                profile_name, _profile_source = self.resolve_adept_profile_for_identity(identity.identity_id)
            except AdeptProfileResolutionError as exc:
                self.storage.acquisition_update_state(
                    acquisition_id, "attention_required", error_code=exc.code, error_message=str(exc),
                    attention_code=exc.code, metadata={"staging": str(staged)},
                )
                return {"status": "attention_required", "code": exc.code, "staging": str(staged)}
            request = {
                "schema_version": 2,
                "source": {"type": "acsm", "provider": provider_id, "path": str(staged), "headers": {}},
                "destination": str(destination), "format": media_format, "profile": profile_name,
                "metadata": {"title": title or source.stem, "manual_import": True, "identity_id": identity.identity_id},
                "options": {"force": False},
            }
            originating_profile_id = self._originating_adept_profile_id(identity.identity_id, profile_name)
            result = self.execute_acquisition_request(request)
            from .adept_binding import capture_binding
            adept_binding = capture_binding(result, profile_name=profile_name, profile_id=originating_profile_id)
            artifact = result.get("artifact") if isinstance(result, dict) else None
            produced = Path(str((artifact or {}).get("path") or destination))
            if produced.is_symlink() or produced.resolve() != destination.resolve():
                raise ProviderConfigurationError("Chemin d’artefact ADEPT inattendu")
            if not produced.is_file():
                raise ProviderConfigurationError("Le runtime DRM n'a produit aucun Artifact")
            inspected = inspect_artifact(produced)
            if not inspected.valid or inspected.media_format not in {"epub", "pdf"}:
                raise ProviderConfigurationError(f"Artifact ACSM invalide: {inspected.reason or 'format'}")
            expected_suffix = ".pdf" if inspected.media_format == "pdf" else ".epub"
            if produced.suffix.casefold() != expected_suffix:
                corrected = produced.with_suffix(expected_suffix)
                # link() atomically refuses any existing destination, including
                # a dangling symlink. Never unlink another local document.
                os.link(produced, corrected)
                produced.unlink()
                produced = corrected
            indexed = self.catalog_index_path(
                produced, title=title or source.stem, item_id=source_sha,
                identity_id=identity.identity_id, provider_id=provider_id,
            )
            if indexed.get("status") != "indexed":
                raise ProviderConfigurationError(f"Indexation ACSM impossible: {indexed.get('reason') or indexed.get('status')}")
            self.storage.acquisition_update_state(
                acquisition_id, "complete", artifact_id=indexed.get("artifact_id"),
                metadata={"path": str(produced), "catalog_index": indexed, "source_acsm_sha256": source_sha, "adept_binding": adept_binding},
            )
            try:
                staged.unlink(missing_ok=True)
                if not any(work.iterdir()):
                    work.rmdir()
            except OSError:
                pass
            return {"status": "complete", "path": str(produced), "artifact_id": indexed.get("artifact_id"), "acquisition_id": acquisition_id}
        except Exception as exc:
            current = self.storage.acquisition_get(acquisition_id)
            if not current or current.get("status") != "attention_required":
                self.storage.acquisition_update_state(
                    acquisition_id, "retryable_error", error_code=type(exc).__name__, error_message=str(exc),
                    metadata={"staging": str(staged), "source_acsm_sha256": source_sha},
                )
            raise

    def import_acsm(
        self, source: Path, *, identity_id: str | None = None, provider_id: str = "manual", title: str | None = None,
    ) -> dict[str, Any]:
        identity = self.identity_by_id(identity_id) if identity_id else self.current_identity
        iid = identity.identity_id if identity else identity_id
        try:
            # One dedicated import lock also covers two paths containing the
            # same ACSM, and avoids re-entering the global acquisition lock
            # when a provider delegates fulfillment to this public broker.
            if identity is None:
                raise ProviderConfigurationError("Aucune Identity active pour l'import ACSM")
            try:
                with InterProcessLock(acquisition_lock_file().with_name("manual-acsm.lock")):
                    result = self._import_acsm_impl(source, identity_id=iid, provider_id=provider_id, title=title)
            except LockBusyError:
                raise ProviderConfigurationError("Un import ACSM est déjà en cours") from None
            outcome = "success" if result.get("status") in {"complete", "unchanged"} else "failed"
            self._record_action(iid, "import", outcome, provider_id=provider_id, facts={"status": result.get("status", "")})
            return result
        except Exception as exc:
            self._record_action(iid, "import", "failed", provider_id=provider_id, facts={"error": type(exc).__name__})
            raise

    def runtime_diagnostic(self, plugin_id: str) -> dict[str, Any]:
        try:
            service = self._required_plugin_service(plugin_id)
        except Exception:
            return {}
        return dict(getattr(service, "last_run_diagnostic", {}) or {})

    def kobo_adept_state(self, *, force: bool = False, full: bool = False) -> dict[str, Any]:
        """Discover Kobo-Adept without putting heavy device probes on UI paths.

        Contract v5+ makes ``capabilities --json`` a cheap environment probe
        that does not inspect devices. Normal UI/backup paths therefore use only the
        core contract+capabilities probe.  ``full=True`` is reserved for explicit
        diagnostics and may additionally inspect status/config/update state.
        """
        cache_seconds = 60
        now = time.monotonic()
        if (
            not force
            and not full
            and cache_seconds > 0
            and self._kobo_adept_state_cache is not None
            and now - self._kobo_adept_state_cache_at < cache_seconds
        ):
            return dict(self._kobo_adept_state_cache)

        try:
            adapter = self._kobo_adapter()
        except ProviderConfigurationError as exc:
            state = {
                "available": False,
                "compatible": False,
                "operational": False,
                "download_available": False,
                "acsm_available": False,
                "kobo_delegated_available": False,
                "kobo_pinned_delivery_candidate": False,
                "kobo_separate_content_keys_replay": False,
                "kobo_kdrm_available": False,
                "kobo_formats": [],
                "contract_version": None,
                "capabilities": {},
                "configured_command": None,
                "resolved_executable": None,
                "minimum_supported_contract": 8,
                "preferred_contract": 8,
                "error": {"code": "runtime_plugin_unavailable", "message": str(exc)},
            }
            self._kobo_adept_state_cache = dict(state)
            self._kobo_adept_state_cache_at = now
            return state
        available = getattr(adapter, "available", None)
        if callable(available) and not available():
            state = {
                "available": False, "compatible": False, "operational": False,
                "download_available": False, "acsm_available": False,
                "kobo_delegated_available": False, "kobo_pinned_delivery_candidate": False,
                "kobo_separate_content_keys_replay": False, "kobo_kdrm_available": False,
                "kobo_formats": [], "contract_version": None, "capabilities": {},
                "configured_command": getattr(adapter, "configured_command", lambda: None)(),
                "resolved_executable": None, "minimum_supported_contract": 8, "preferred_contract": 8,
            }
            self._kobo_adept_state_cache = dict(state)
            self._kobo_adept_state_cache_at = now
            return state
        core_probe = getattr(adapter, "probe_core", None)
        probe = adapter.probe() if full or core_probe is None else core_probe()
        contract = probe.get("contract") if isinstance(probe, dict) else {}
        runtime_caps = probe.get("capabilities") if isinstance(probe, dict) else {}
        runtime_status = probe.get("status") if isinstance(probe, dict) else {}
        contract = contract if isinstance(contract, dict) else {}
        runtime_caps = runtime_caps if isinstance(runtime_caps, dict) else {}
        runtime_status = runtime_status if isinstance(runtime_status, dict) else {}
        try:
            contract_version = int(contract.get("contract_version") or runtime_caps.get("contract_version") or runtime_status.get("contract_version") or 0)
        except (TypeError, ValueError):
            contract_version = 0
        compatible = contract_version >= int(getattr(adapter, "required_contract", 8))
        capabilities = runtime_caps.get("capabilities") if isinstance(runtime_caps.get("capabilities"), dict) else {}
        download = capabilities.get("download") if isinstance(capabilities.get("download"), dict) else {}
        http_download = download.get("http") if isinstance(download.get("http"), dict) else {}
        acsm_download = download.get("acsm") if isinstance(download.get("acsm"), dict) else {}
        kobo_download = download.get("kobo") if isinstance(download.get("kobo"), dict) else {}
        declared = contract.get("capabilities") if isinstance(contract.get("capabilities"), list) else []
        declared_names = {str(x) for x in declared}
        acsm_available = bool(
            bool(acsm_download.get("available")) or "download_acsm_adept" in declared_names
        )
        kobo_delegated_available = bool(
            contract_version >= 8
            and (bool(kobo_download.get("available")) or "download_kobo_delegated" in declared_names)
        )
        kobo_formats = [
            str(value).strip().lower()
            for value in (kobo_download.get("formats") or [])
            if str(value).strip()
        ] if isinstance(kobo_download.get("formats"), list) else []
        kobo_pinned_delivery_candidate = bool(
            kobo_delegated_available and kobo_download.get("pinned_delivery_candidate") is True
        )
        kobo_separate_content_keys_replay = bool(
            kobo_delegated_available and kobo_download.get("separate_content_keys_replay") is True
        )
        kobo_drm = kobo_download.get("drm") if isinstance(kobo_download.get("drm"), dict) else {}
        kdrm_cap = kobo_drm.get("KDRM") if isinstance(kobo_drm.get("KDRM"), dict) else kobo_drm.get("kdrm")
        if isinstance(kdrm_cap, dict):
            kobo_kdrm_available = bool(kdrm_cap.get("available"))
        else:
            # 0.13.2's additive capability example makes pinned delivery +
            # separate key replay sufficient evidence of the new KDRM bridge,
            # while older v8 runtimes normally expose drm.KDRM explicitly.
            kobo_kdrm_available = bool(
                kobo_pinned_delivery_candidate and kobo_separate_content_keys_replay
            )
        download_available = (
            bool(http_download.get("available"))
            or "download_http_epub_pdf" in declared_names
            or acsm_available
            or kobo_delegated_available
        )
        strict_json_stdout = bool(contract_version >= 7 or "strict_json_stdout" in declared_names)
        contract_error = probe.get("contract_error")
        capabilities_error = probe.get("capabilities_error")
        status_error = probe.get("status_error")
        status_error_code = str(status_error.get("code") or "") if isinstance(status_error, dict) else ""
        # Contract v5 makes contract+capabilities authoritative for the normal
        # integration path.  Full status/config probes are diagnostic only; an
        # actual download will still return a structured config_error if needed.
        blocking_status_error = status_error if full and status_error_code == "config_error" else None
        core_error = contract_error or capabilities_error or blocking_status_error
        state = {
            "available": True,
            "compatible": compatible,
            "operational": bool(compatible and download_available and not blocking_status_error),
            "download_available": download_available,
            "acsm_available": acsm_available,
            "kobo_delegated_available": kobo_delegated_available,
            "kobo_pinned_delivery_candidate": kobo_pinned_delivery_candidate,
            "kobo_separate_content_keys_replay": kobo_separate_content_keys_replay,
            "kobo_kdrm_available": kobo_kdrm_available,
            "kobo_formats": kobo_formats,
            "minimum_supported_contract": int(getattr(adapter, "required_contract", 8)),
            "preferred_contract": int(getattr(adapter, "required_contract", 8)),
            "software_version": str(runtime_caps.get("software_version") or contract.get("version") or runtime_status.get("software_version") or ""),
            "contract_version": contract_version or None,
            "capabilities": capabilities,
            "declared_capabilities": list(declared),
            "strict_json_stdout": strict_json_stdout,
            "resolved_executable": probe.get("resolved_executable"),
            "resolved_update_executable": probe.get("resolved_update_executable"),
            "probe_timings_ms": probe.get("probe_timings_ms") or {},
            "contract_error": contract_error,
            "capabilities_error": capabilities_error,
            "status_error": status_error,
            "config_paths": probe.get("config_paths"),
            "config_paths_error": probe.get("config_paths_error"),
            "update_status": probe.get("update_status"),
            "update_status_error": probe.get("update_status_error"),
            "status": runtime_status,
            "error": core_error,
        }
        self._kobo_adept_state_cache = dict(state)
        self._kobo_adept_state_cache_at = now
        return state

    def kobo_profiles(self) -> list[dict[str, Any]]:
        payload = self._kobo_adapter().profiles()
        rows = payload.get("profiles") if isinstance(payload, dict) else None
        return [dict(x) for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []

    @staticmethod
    def _adept_profile_ref(row: dict[str, Any]) -> str:
        return adept_profiles.profile_reference(row)

    @staticmethod
    def _adept_profile_name(row: dict[str, Any]) -> str:
        return adept_profiles.profile_name(row)

    def _adept_profile_row(self, reference: str | None, profiles: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        return adept_profiles.profile_row(reference, profiles, list_profiles=self.kobo_profiles)

    def _adept_profile_cli_name(self, reference: str | None, profiles: list[dict[str, Any]] | None = None) -> str | None:
        return adept_profiles.profile_cli_name(
            reference, profiles, resolve_row=self._adept_profile_row,
            name_of=self._adept_profile_name,
        )

    def can_direct_download(self, item: SearchResult) -> bool:
        """Whether Paris can execute an acquisition with the published contracts."""
        if isinstance(item.metadata, dict) and item.metadata.get("search_provenance") == "known_catalog_stale":
            return False
        try:
            session = self._session_for_source(item.source_provider, item.source_credential)
            if item.source_provider == "kobo":
                # Kobo catalogue entries that are not owned must never expose a
                # local download action.  Execution is possible only through the
                # published Vault acquisition + Adept v8+ interface.
                if not item.owned or item.downloadable is False:
                    return False
                if not hasattr(session, "resolve_acquisition"):
                    return False
                state = self.kobo_adept_state()
                return bool(state.get("available") and state.get("compatible") and state.get("operational"))
            if session.capabilities.direct_download:
                return True
            return bool(self.acquisition.override_command(item.source_provider) and self.acquisition.acquisition_url(item))
        except Exception:
            return False

    @staticmethod
    def _kobo_adept_format(vault_format: str | None) -> str | None:
        return KoboOfferAdapter.adept_format(vault_format)

    @staticmethod
    def _kobo_candidate_media_format(candidate: dict[str, Any]) -> str:
        return KoboOfferAdapter.candidate_media_format(candidate)

    @classmethod
    def _kobo_candidate_drm(cls, candidate: dict[str, Any]) -> str:
        return KoboOfferAdapter.candidate_drm(candidate)

    @classmethod
    def _kobo_candidate_handling(cls, candidate: dict[str, Any]) -> str:
        return KoboOfferAdapter.candidate_handling(candidate)

    @staticmethod
    def _kobo_candidate_profile(candidate: dict[str, Any]) -> str | None:
        return KoboOfferAdapter.candidate_profile(candidate)

    @staticmethod
    def _kobo_candidate_source(candidate: dict[str, Any]) -> str | None:
        return KoboOfferAdapter.candidate_source(candidate)

    @staticmethod
    def _kobo_candidate_requires_authorization(candidate: dict[str, Any]) -> bool:
        return KoboOfferAdapter.candidate_requires_authorization(candidate)

    @classmethod
    def _kobo_acsm_candidate(cls, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return KoboOfferAdapter.acsm_candidate(payload)
        except KoboAcquisitionSelectionError:
            return None

    @classmethod
    def _kobo_candidate(cls, payload: dict[str, Any], preferred_format: str = "epub") -> dict[str, Any]:
        try:
            return KoboOfferAdapter.direct_candidate(payload, preferred_format)
        except KoboAcquisitionSelectionError as exc:
            raise ProviderConfigurationError(str(exc)) from exc

    @classmethod
    def _kobo_delegated_candidate(cls, payload: dict[str, Any], preferred_format: str = "epub") -> dict[str, Any]:
        try:
            return KoboOfferAdapter.delegated_candidate(payload, preferred_format)
        except KoboAcquisitionSelectionError as exc:
            raise ProviderConfigurationError(str(exc)) from exc

    @staticmethod
    def _kobo_canonical_drm(value: Any) -> str:
        raw = str(value or "").strip()
        key = re.sub(r"[^a-z0-9]", "", raw.lower())
        if key in {"signednodrm", "nodrm", "none"}:
            return "SignedNoDrm"
        if key in {"kdrm", "kobo"}:
            return "KDRM"
        if key in {"adobedrm", "adept", "adobeadept"}:
            return "AdobeDrm"
        if key == "kobokdrm":
            return "KDRM"
        return raw or "unknown"

    @staticmethod
    def _kobo_url_format(candidate: dict[str, Any]) -> str | None:
        value = (
            candidate.get("url_format")
            or candidate.get("source_format")
            or candidate.get("raw_format")
            or candidate.get("format_raw")
            or candidate.get("media_format")
            or candidate.get("format")
        )
        raw = str(value or "").strip()
        if not raw:
            return None
        key = raw.lower()
        known = {"kepub": "KEPUB", "epub3": "EPUB3", "epub": "EPUB", "flepub": "FLEPUB", "pdf": "PDF"}
        return known.get(key, raw.upper())

    @staticmethod
    def _kobo_delegated_request(payload: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any] | None:
        """Extract the provider-owned content-access recipe without logging secrets.

        Vault owns the response schema. v2.3 publishes the normative recipe under
        ``acquisition.delegation.content_access_request``. Paris keeps the older
        aliases only for backward compatibility, then projects the request fields
        contracted by Adept v8.
        """
        acquisition = payload.get("acquisition") if isinstance(payload.get("acquisition"), dict) else {}

        def valid_request(value: Any) -> dict[str, Any] | None:
            if not isinstance(value, dict):
                return None
            url = str(value.get("url") or "").strip()
            method = str(value.get("method") or "GET").upper()
            if not url.startswith("https://") or method != "GET":
                return None
            headers = value.get("headers")
            query = value.get("query")
            request: dict[str, Any] = {"url": url, "method": "GET"}
            if isinstance(headers, dict):
                request["headers"] = {str(k): str(v) for k, v in headers.items()}
            if isinstance(query, dict):
                request["query"] = {
                    str(k): v for k, v in query.items()
                    if isinstance(v, (str, int, float, bool))
                }
            return request

        delegation = acquisition.get("delegation") if isinstance(acquisition.get("delegation"), dict) else {}
        found = valid_request(delegation.get("content_access_request"))
        if found is not None:
            return found

        request_keys = ("request", "delegated_request", "content_access_request", "content_access_book")
        for container in (candidate, acquisition, payload):
            if not isinstance(container, dict):
                continue
            for key in request_keys:
                found = valid_request(container.get(key))
                if found is not None:
                    return found
            for wrapper_key in ("delegated", "recipe", "content_access"):
                wrapper = container.get(wrapper_key)
                if not isinstance(wrapper, dict):
                    continue
                found = valid_request(wrapper)
                if found is not None:
                    return found
                for key in request_keys:
                    found = valid_request(wrapper.get(key))
                    if found is not None:
                        return found
        return None

    @staticmethod
    def _kobo_kdrm_context(payload: dict[str, Any], candidate: dict[str, Any]) -> dict[str, str] | None:
        acquisition = payload.get("acquisition") if isinstance(payload.get("acquisition"), dict) else {}

        def context(value: Any) -> dict[str, str] | None:
            if not isinstance(value, dict):
                return None
            device_id = str(value.get("device_id") or "").strip()
            user_id = str(value.get("user_id") or "").strip()
            if device_id and user_id:
                return {"device_id": device_id, "user_id": user_id}
            return None

        delegation = acquisition.get("delegation") if isinstance(acquisition.get("delegation"), dict) else {}
        direct = context(delegation.get("session_context"))
        if direct is not None:
            return direct

        for container in (candidate, acquisition, payload):
            if not isinstance(container, dict):
                continue
            direct = context(container.get("kdrm")) or context(container.get("kdrm_context"))
            if direct is not None:
                return direct
            for wrapper_key in ("delegated", "recipe", "content_access", "context"):
                wrapper = container.get(wrapper_key)
                if isinstance(wrapper, dict):
                    direct = context(wrapper.get("kdrm")) or context(wrapper.get("kdrm_context")) or context(wrapper)
                    if direct is not None:
                        return direct
        return None

    @staticmethod
    def _observed_backup_artifact(path: Path) -> dict[str, Any]:
        return kobo_backup._observed_backup_artifact(path)

    @staticmethod
    def _kobo_artifact_plausible(path: Path, fmt: str) -> bool:
        """Perform a conservative local sanity check without interpreting DRM."""
        return kobo_backup._kobo_artifact_plausible(path, fmt)

    def _kobo_record_candidate_paths(self, record: dict[str, Any], target_dir: Path) -> list[Path]:
        return kobo_backup._kobo_record_candidate_paths(self, record, target_dir)

    def _kobo_destination(self, item: SearchResult, target_dir: Path, fmt: str) -> Path:
        return kobo_backup._kobo_destination(self, item, target_dir, fmt)

    def _execute_kobo_acquisition(self, item: SearchResult, target_dir: Path) -> Acquisition:
        """Compatibility façade; Kobo execution lives in the provider adapter."""
        return kobo_backup._execute_kobo_acquisition(self, item, target_dir)

    def _acquire_direct_item_impl(self, item: SearchResult, *, _admission=None) -> Acquisition:
        if isinstance(item.metadata, dict) and item.metadata.get("search_provenance") == "known_catalog_stale":
            raise ProviderConfigurationError(
                "Résultat connu hors ligne : acquisition indisponible"
            )
        # An admission callback is part of the security contract: silently
        # ignoring it would turn an attested per-account Numilog Library action
        # into an unrestricted direct download. Only Kobo currently rechecks
        # that callback INSIDE its acquisition lock. Fail closed for every
        # other provider until an equally scoped, locked executor is ready.
        if _admission is not None and item.source_provider != "kobo":
            raise ProviderConfigurationError(
                "Acquisition ciblée indisponible : autorisation du compte non vérifiable"
            )
        session = self._session_for_source(item.source_provider, item.source_credential)
        content_type = self.acquisition.content_type_for(item)

        if item.source_provider == "kobo":
            if not item.owned:
                raise ProviderConfigurationError("Kobo : ce titre n'est pas possédé par le compte")
            if item.downloadable is False:
                raise ProviderConfigurationError("Kobo : ce titre n'est pas marqué téléchargeable")
            identity = self.identity_for_connection(item.source_provider, item.source_credential)
            # Direct downloads must serialize with opening-sync and Kobo backup.
            # Do not enter this path from sync_kobo_backup (it already holds
            # the lock and calls _execute_kobo_acquisition directly).
            try:
                with InterProcessLock(acquisition_lock_file()):
                    if _admission is not None:
                        item = _admission()
                        identity = self.identity_for_connection(item.source_provider, item.source_credential)
                    target = self._kobo_backup_target_dir(identity.identity_id)
                    candidates = self._kobo_record_candidate_paths(
                        {"item_id": item.record_id, "title": item.title,
                         "author": item.author, "format": item.format}, target,
                    )
                    legacy_dir = self.library_dir()
                    if legacy_dir.resolve() != target.resolve():
                        candidates.extend(p for p in self._kobo_record_candidate_paths(
                            {"item_id": item.record_id, "title": item.title,
                             "author": item.author, "format": item.format}, legacy_dir,
                        ) if p not in candidates)
                    # Explicit provider/product links in the local catalogue
                    # are evidence of a candidate even after arbitrary rename.
                    # Keep strict identity isolation and never infer by title.
                    default_id = self.identities[0].identity_id if self.identities else None
                    for row in self.storage.catalog_entries():
                        if (row.get("local_state") != "local"
                                or row.get("provider_id") != "kobo"
                                or str(row.get("identity_id") or default_id or "") != identity.identity_id
                                or str(row.get("item_id") or "") != item.record_id):
                            continue
                        meta = row.get("metadata")
                        path_text = meta.get("path") if isinstance(meta, dict) else None
                        if isinstance(path_text, str) and path_text.strip():
                            linked = Path(path_text)
                            if (linked.is_file() or linked.is_symlink()) and linked not in candidates:
                                candidates.append(linked)
                    existing = self.storage.backup_get("kobo", item.source_credential, item.record_id)
                    if existing and existing.get("local_path"):
                        local = Path(str(existing["local_path"]))
                        if local.is_file() or local.is_symlink():
                            candidates.append(local)
                    if candidates:
                        raise ProviderConfigurationError(
                            "Kobo : fichier local candidat déjà présent ; rapprochement requis avant téléchargement"
                        )
                    return self._execute_kobo_acquisition(item, target)
            except LockBusyError as exc:
                raise ProviderConfigurationError(
                    "Une synchronisation locale est déjà en cours dans une autre instance de cifereca"
                ) from exc

        if item.source_provider == "numilog":
            # Even an explicit UI click is not permission to download from a
            # detached account or to race an existing manual/materialize run.
            # Numilog resolves delivery URLs afresh *inside* download_direct.
            from .core.numilog_manual_acquisition import require_source
            try:
                with InterProcessLock(acquisition_lock_file()):
                    identity, pinned_session = require_source(
                        self, item, session=session,
                    )
                    if self.storage.numilog_purchase_has_local_conflict(
                        credential_id=item.source_credential,
                        item_id=item.record_id,
                    ):
                        raise ProviderConfigurationError(
                            "Numilog : acquisition existante ; rapprochement local requis"
                        )
                    target_dir = self.library_identity_provider_dir(
                        identity.identity_id, "numilog",
                    )
                    try:
                        path, _private_url = pinned_session.download_direct(
                            item, target_dir,
                        )
                    except Exception as exc:
                        from .plugin_api import Cancelled
                        if isinstance(exc, Cancelled):
                            raise
                        # A provider exception can contain a signed ACSM link
                        # or order/cookie data. Never relay its raw message.
                        raise ProviderConfigurationError(
                            "Numilog : livraison indisponible ; réessayer manuellement"
                        ) from None
                    # The provider may release its per-session lock before
                    # returning. A reconnect/rebind during HTTP or ADEPT must
                    # not be committed under a different identity.
                    require_source(self, item, session=pinned_session)
                    from dataclasses import replace
                    # Never pass unverified private cover/link metadata to
                    # post-download hooks or the persisted Acquisition object.
                    # The original item is used only by the provider's
                    # exact owned-library resolver, inside the source lock.
                    clean_item = replace(
                        item, thumbnail_url=None, friendly_url=None,
                        download_url=None, metadata={},
                    )
                    self.acquisition.run_post_download(
                        clean_item, path, "", target_dir,
                    )
                    require_source(self, item, session=pinned_session)
                    # A provider-returned URL may contain single-use ACSM
                    # signatures. Never persist it as Acquisition.source_url.
                    return self.acquisition.acquisition_from_direct(
                        clean_item, path=path, url="", target_dir=target_dir,
                        action="download",
                    )
            except LockBusyError:
                raise ProviderConfigurationError(
                    "Numilog : acquisition locale déjà en cours"
                ) from None

        identity = self.identity_for_connection(item.source_provider, item.source_credential)
        target_dir = (
            self.library_identity_provider_dir(identity.identity_id, item.source_provider)
            if content_type == "ebook" else self.acquisition.target_dir(item.source_provider, content_type)
        )
        if self.acquisition.override_command(item.source_provider):
            return self.acquisition.run_override(item, target_dir)
        path, url = session.download_direct(item, target_dir)
        self.acquisition.run_post_download(item, path, url, target_dir)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        self.diagnostics.add(
            "DIGITAL", "direct download artifact",
            detail={
                "provider": session.provider_id, "content_type": content_type,
                "file": path.name, "bytes": size,
            },
        )
        return self.acquisition.acquisition_from_direct(
            item, path=path, url=url, target_dir=target_dir, action="download",
        )

    def acquire_direct_item(self, item: SearchResult, *, _admission=None) -> Acquisition:
        identity_id = None
        try:
            identity_id = self.identity_for_connection(item.source_provider, item.source_credential).identity_id
        except Exception:
            identity_id = self.current_identity.identity_id if self.current_identity else None
        try:
            result = (self._acquire_direct_item_impl(item, _admission=_admission) if _admission is not None
                      else self._acquire_direct_item_impl(item))
            safe_facts = ({"private_item": True} if item.source_provider == "numilog"
                          else {"record_id": item.record_id})
            self._record_action(identity_id, "acquisition", "success",
                                provider_id=item.source_provider, facts=safe_facts)
            return result
        except Exception as exc:
            safe_facts = ({"private_item": True, "error": type(exc).__name__}
                          if item.source_provider == "numilog"
                          else {"record_id": item.record_id, "error": type(exc).__name__})
            self._record_action(identity_id, "acquisition", "failed",
                                provider_id=item.source_provider, facts=safe_facts)
            raise

    def obtain_search_item(self, item: SearchResult):
        from .core.materialization import MaterializationCoordinator

        return MaterializationCoordinator(
            acquire=self.acquire_direct_item,
            index_path=self.catalog_index_path,
            resolve_identity=self.identity_for_connection,
        ).obtain(item)

    def owned_library_download_target(self, entry):
        from .core.owned_library_download import owned_library_download_target
        return owned_library_download_target(self, entry)

    def obtain_owned_library_item(self, target):
        from .core.owned_library_download import obtain_owned_library_item
        return obtain_owned_library_item(self, target)

    def prepare_direct_download(self, item: SearchResult) -> tuple[Path, str]:
        """Compatibility wrapper around the normalized direct acquisition."""
        result = self.acquire_direct_item(item)
        return result.path or result.target_dir or self.default_downloads_dir(), result.url

    def library_dir(self) -> Path:
        """Functional local library owned by cifereca.

        The global architecture blueprint assigns ~/EBooks to Paris.  The path
        remains configurable but defaults there from 0.5.0 onward and remains the acquisition target.
        """
        configured = os.path.expandvars(os.path.expanduser(str(self.config.get("library.directory", "~/EBooks") or "~/EBooks").strip()))
        path = Path(configured)
        if not path.is_absolute():
            path = Path.home() / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    def catalog_scan(self, *, repair_misnamed: bool = False, force_covers: bool = False) -> dict[str, Any]:
        """Reconcile the local-first catalog from the Paris-owned library tree."""
        return library_mutation.catalog_scan(self, repair_misnamed=repair_misnamed, force_covers=force_covers)

    def catalog_entries(self, *, state: str | None = None) -> list[dict[str, Any]]:
        return library_projection.catalog_entries(self, state=state)

    def catalog_index_path(
        self, path: Path, *, title: str | None = None, item_id: str | None = None,
        identity_id: str | None = None, provider_id: str | None = None,
    ) -> dict[str, Any]:
        return library_mutation.catalog_index_path(self, path, title=title, item_id=item_id, identity_id=identity_id, provider_id=provider_id)

    def library_entries(self, *, identity_id: str | None = None, scan: bool = False) -> list[dict[str, Any]]:
        """Canonical Library projection.

        Local rows are grouped by content identity (SHA-256/Artifact) rather than
        by provider/item. Remote-only rights remain distinct until reconciliation
        can bind them to a local Artifact.
        """
        return library_projection.library_entries(self, identity_id=identity_id, scan=scan, logger=_TRACE_LOG)

    def _persist_active_loans(self, identity_id: str, loans: list[Loan], *, reconcile_missing: bool = True) -> dict[str, int]:
        """Persist current digital loans and realize rights no longer reported."""
        return library_mutation._persist_active_loans(self, identity_id, loans, reconcile_missing=reconcile_missing)

    @staticmethod
    def _loan_destination_name(title: str, suffix: str = ".epub") -> str:
        cleaned = SAFE_FILENAME.sub("_", str(title or "Livre")).strip(" ._") or "Livre"
        return f"{cleaned[:160]}{suffix}"

    @staticmethod
    def _provider_attention_code(exc: Exception) -> str | None:
        """Map provider business-rule failures to stable action-required codes.

        These are not transport failures and must not be retried on every
        application opening. Keep this deliberately narrow: unknown provider
        errors remain retryable.
        """
        text = str(exc or "").casefold()
        normalized = text.replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a")
        if "quota" in normalized and ("depasse" in normalized or "exceed" in normalized):
            return "provider_quota_exceeded"
        return None

    def _diagnose_adept_binding(self, binding, *, provider_loan_id):
        detail = {"provider_loan_id": provider_loan_id,
                  "adept_loan_id_present": bool(binding.get("adept_loan_id")),
                  "origin_profile_id_present": bool(binding.get("adept_profile_id")),
                  "return_available": bool(binding.get("return_available"))}
        _TRACE_LOG.info("TRACE adept binding provider_loan_id=%s adept_loan_id_present=%s origin_profile_id_present=%s return_available=%s", *detail.values())
        self.diagnostics.add("ADEPT", "loan binding", "ok" if detail["return_available"] else "warning", detail=detail, level="normal")

    def adept_return_status_for_loan(self, loan: Loan) -> dict[str, Any]:
        """Read-only ADEPT return status from the exact Paris borrower provenance.

        Never infer the Adobe owner from the selected UI identity or the Paris
        connection owner for a linked account. Keep return actions unavailable
        when the source, loan transaction or origin profile is ambiguous.
        """
        from .core.adept_return_origin import inventory_attests_return_origin

        borrower_code = None
        is_paris = str(loan.source_provider).lower() == "paris"
        if is_paris:
            connection_identity_id = self.storage.identity_for_connection(
                loan.source_provider, loan.source_credential
            )
            identity = self.identity_by_id(connection_identity_id)
            if identity is None:
                return {"status": "adept_identity_unproven", "return_available": False}
            borrower_identity_id, borrower_code = self._paris_adept_identity_for_loan(
                loan, connection_identity_id=identity.identity_id,
            )
        else:
            identity = self.identity_for_connection(
                loan.source_provider, loan.source_credential
            )
            borrower_identity_id = identity.identity_id

        all_rights = self.storage.entitlements(provider_id=loan.source_provider)
        all_acquisitions = self.storage.acquisitions(provider_id=loan.source_provider)
        if is_paris and self._cross_identity_loan_conflict(
            all_acquisitions, provider_loan_id=loan.id,
            identity_id=identity.identity_id,
            entitlements=[r for r in all_rights if r.get("state") == "active"],
        ):
            return {"status": "adept_loan_identity_conflict", "return_available": False}

        entitlements = {
            row["entitlement_id"] for row in all_rights
            if row.get("identity_id") == identity.identity_id
            and row.get("loan_id") == loan.id
            and row.get("credential_id") == loan.source_credential
            and str(row.get("account_id") or "") == str(loan.source_account_id or "")
            and str(row.get("item_id") or "") == str(loan.record_id or loan.id)
        }
        candidates = []
        for row in all_acquisitions:
            if (row.get("status") != "complete"
                    or row.get("executor") != "kobo-adept"
                    # #989: a manual/imported ADEPT artifact is not proof of
                    # this automatic Paris loan's fulfillment transaction.
                    or row.get("strategy") != "loan_auto"
                    or row.get("identity_id") != identity.identity_id
                    or row.get("credential_id") != loan.source_credential
                    or str(row.get("account_id") or "") != str(loan.source_account_id or "")
                    or str(row.get("item_id") or "") != str(loan.record_id or loan.id)):
                continue
            metadata = row.get("metadata") or {}
            recorded_loan_id = metadata.get("provider_loan_id")
            if recorded_loan_id is not None and str(recorded_loan_id) != str(loan.id):
                continue
            if recorded_loan_id is None and row.get("entitlement_id") not in entitlements:
                continue
            candidates.append(row)

        if not candidates:
            return {"status": borrower_code or "no_acquisition", "return_available": False}
        if len(candidates) != 1:
            return {"status": "adept_return_acquisition_ambiguous", "return_available": False}

        row = candidates[0]
        binding = (row.get("metadata") or {}).get("adept_binding") or {}
        origin_attested = inventory_attests_return_origin(binding)
        if borrower_code and not origin_attested:
            return {"status": borrower_code, "return_available": False}
        # Grants/current identity profile govern NEW fulfillment. Historical
        # inventory attestation preserves a completed license's own origin.
        # Exact Paris provenance and cross-identity/duplicate guards above stay.
        if is_paris and not origin_attested and binding.get("adept_profile_id") and (
            self.storage.adept_profile_binding_get(
                "identity", borrower_identity_id
            ) != binding["adept_profile_id"]
        ):
            return {
                "status": "adept_return_profile_conflict",
                "acquisition_id": row["acquisition_id"],
                "return_available": False,
            }

        state = "returned" if binding.get("loan_state") == "returned" else (
            "loan_id_missing" if not binding.get("adept_loan_id") else (
                "profile_missing" if not binding.get("adept_profile_id") else "ready"
            )
        )
        if state == "returned" and (
            binding.get("return_verification") != "confirmed"
            or binding.get("token_present") is not False
        ):
            state = "returned_unverified"
        if binding.get("return_verification") in {"pending", "verification_failed"}:
            state = "verification_failed"
        elif binding.get("loan_state") == "expired":
            state = "expired"
        elif (
            binding.get("inventory_status") in {
                "unresolved", "ambiguous", "inventory_error", "binding_conflict"
            } and state != "returned"
        ):
            state = binding["inventory_status"]
        elif binding.get("token_present") is False and state != "returned":
            state = "token_missing"
        if state in {"returned", "returned_unverified"} and self._provider_loan_active_for_acquisition(row):
            state = "provider_conflict"
        return {
            "status": state,
            "acquisition_id": row["acquisition_id"],
            "adept_loan_id": binding.get("adept_loan_id"),
            "profile_name": binding.get("adept_profile_name"),
            "return_available": bool(
                state == "ready" and binding.get("return_available")
                and self._adept_return_capability()
            ),
        }

    def reacquire_digital_loan(self, loan: Loan) -> dict[str, Any]:
        """Explicit full recovery, serialized with automatic acquisitions.

        Preserve a usable previous acquisition/binding on failed re-acquisition.
        A successful re-acquisition publishes a distinct file if one exists.
        """
        if not self.can_download_loan(loan):
            raise ProviderConfigurationError("Ce prêt ne permet pas une acquisition numérique")
        try:
            with InterProcessLock(acquisition_lock_file()):
                bound_identity_id = self.storage.identity_for_connection(
                    loan.source_provider, loan.source_credential
                )
                identity = self.identity_by_id(bound_identity_id)
                if identity is None:
                    return {
                        "status": "attention_required", "loan_id": loan.id,
                        "code": "adept_identity_unproven", "retry": "explicit_only",
                    }
                if str(loan.source_provider).lower() == "paris":
                    _, borrower_code = self._paris_adept_identity_for_loan(
                        loan, connection_identity_id=identity.identity_id,
                    )
                    if borrower_code:
                        return {
                            "status": "attention_required", "loan_id": loan.id,
                            "code": borrower_code, "retry": "explicit_only",
                        }
                    if self._cross_identity_loan_conflict(
                        self.storage.acquisitions(provider_id=loan.source_provider),
                        provider_loan_id=loan.id, identity_id=identity.identity_id,
                        entitlements=self.storage.entitlements(
                            provider_id=loan.source_provider, state="active"
                        ),
                    ):
                        return {
                            "status": "attention_required", "loan_id": loan.id,
                            "code": "adept_loan_identity_conflict", "retry": "explicit_only",
                        }
                previous = [r for r in self.storage.acquisitions(provider_id=loan.source_provider)
                            if r.get("identity_id") == identity.identity_id and r.get("credential_id") == loan.source_credential
                            and str(r.get("account_id") or "") == str(loan.source_account_id or "")
                            and str(r.get("item_id") or "") == str(loan.record_id or loan.id) and r.get("status") == "complete"]
                self._persist_active_loans(identity.identity_id, [loan], reconcile_missing=False)
                result = self._acquire_active_loan_automatically(loan, force_refresh=True)
                if result.get("status") != "complete":
                    for row in previous:
                        path = Path(str((row.get("metadata") or {}).get("path") or ""))
                        if path.is_file() and inspect_artifact(path).valid:
                            metadata = dict(row.get("metadata") or {})
                            metadata["last_reacquisition_failure"] = {"code": result.get("code"), "error": result.get("error"), "at": int(time.time())}
                            self.storage.acquisition_put({**row, "metadata": metadata})
                    raise AttentionRequired(result.get("error") or "Acquisition non terminée", code=result.get("code") or "reacquisition_failed")
                return result
        except LockBusyError as exc:
            raise AttentionRequired("Une acquisition ou synchronisation est déjà en cours. Réessayez après sa fin.", code="acquisition_busy") from exc

    @staticmethod
    def _borrower_identity_conflict(
        accounts: Any, *, provider_id: str, account_id: str | None,
        identity_id: str, credential_id: str | None = None,
    ) -> bool:
        """Fail closed unless the borrower is bound to the requested identity.

        Paris uses the literal "primary" for its main-account provenance,
        while the provider account can have unique_id="". These values
        identify a borrower only inside the exact provider connection.
        """
        if account_id is None:
            return True
        borrower = str(account_id).strip()
        provider = str(provider_id).lower()
        matching = [
            account for account in accounts
            if str(account.provider_id).lower() == provider
        ]
        if borrower in {"", "primary"}:
            if not credential_id:
                return True
            matching = [
                account for account in matching
                if str(account.credential_id) == str(credential_id)
                and bool(account.is_primary)
                and (borrower == "primary" or str(account.unique_id or "") == "")
            ]
            return len(matching) != 1 or str(matching[0].identity_id) != identity_id

        matching = [
            account for account in matching
            if str(account.unique_id or "").strip() == borrower
        ]
        if credential_id is not None and not any(
            str(account.credential_id) == str(credential_id)
            and str(account.identity_id) == identity_id
            for account in matching
        ):
            return True
        return {str(account.identity_id) for account in matching} != {identity_id}

    def bind_paris_borrower_adept(
        self, *, credential_id: str, account_id: str, borrower_identity_id: str,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Explicitly pin a Paris account's exceptional fulfillment identity.

        This operation must be initiated by an authorized user, never by sync,
        and never by guessing from the connected/current UI identity.
        """
        if account_id is None:
            raise ValueError("Paris borrower account is required")
        borrower = str(account_id).strip()
        if borrower in {"", "primary"}:
            borrower = "primary"
        matches = [
            account for account in self.accounts
            if str(account.provider_id).lower() == "paris"
            and str(account.credential_id) == str(credential_id)
            and ((borrower == "primary" and account.is_primary)
                 or (borrower != "primary" and not account.is_primary
                     and str(account.unique_id or "").strip() == borrower))
        ]
        if (
            len(matches) != 1
            or str(matches[0].identity_id) != self.storage.identity_for_connection("paris", credential_id)
        ):
            raise ValueError("Paris borrower not uniquely identified for connection")
        owner = self.identity_by_id(borrower_identity_id)
        if owner is None:
            raise ValueError("unknown ADEPT borrower identity")
        profile_id = self.storage.adept_profile_binding_get("identity", owner.identity_id)
        if not profile_id:
            raise ValueError("ADEPT borrower identity has no stable profile binding")
        self.storage.paris_borrower_adept_binding_put(
            credential_id=credential_id, account_id=borrower,
            borrower_identity_id=owner.identity_id, profile_id=profile_id,
            confirmed=confirmed,
        )
        return self.storage.paris_borrower_adept_binding_get(
            credential_id=credential_id, account_id=borrower,
        )

    def audit_paris_adept_loan_origins(self, loan: Loan) -> dict[str, Any]:
        """Read-only historical evidence for the exceptional ADEPT resolver.

        Never authorizes new fulfillment or return; no live inventory refresh,
        provider session, profile selection or ledger mutation.
        """
        from .core.adept_origin_audit import inspect_paris_adept_origins
        if str(loan.source_provider).casefold() != "paris":
            return {"status": "unsupported_source", "shared_visibility": False,
                    "profile_disagreement": False, "sources": [], "origins": []}
        return inspect_paris_adept_origins(
            loan_id=str(loan.id or ""),
            item_id=str(loan.record_id or loan.id or ""),
            entitlements=self.storage.entitlements(provider_id="paris"),
            acquisitions=self.storage.acquisitions(provider_id="paris"),
            artifact_inspector=inspect_artifact,
        )

    def _paris_adept_identity_for_loan(
        self, loan: Loan, *, connection_identity_id: str,
    ) -> tuple[str | None, str | None]:
        """Resolve the Adobe borrower separately from the Paris connection.

        The primary account normally uses its proven connection identity.
        An explicit exceptional grant takes precedence without changing that
        identity's normal profile. Linked accounts always require a grant.
        """
        return adept_borrower._paris_adept_identity_for_loan(self, loan, connection_identity_id=connection_identity_id)

    @staticmethod
    def _cross_identity_loan_conflict(
        acquisitions: Any, *, provider_loan_id: str, identity_id: str,
        entitlements: Any = (),
    ) -> bool:
        """Reject a Paris loan recorded under another identity in either ledger.

        A currently active entitlement is also a trace of the same upstream
        loan; it does not prove that identity is the rightful ADEPT owner.
        """
        return adept_borrower._cross_identity_loan_conflict(acquisitions, provider_loan_id=provider_loan_id, identity_id=identity_id, entitlements=entitlements)

    def _acquire_active_loan_automatically(self, loan: Loan, *, force_refresh: bool = False) -> dict[str, Any]:
        """Serialize automatic acquisition across threads and processes per loan.

        Do not reuse the global acquisition lock: opening_sync already holds it.
        The loan-scoped lock also covers other callers running in another process.
        """
        loan_id = str(loan.id or "").strip()
        if not loan_id:
            return {
                "status": "attention_required", "loan_id": loan.id,
                "code": "adept_loan_identity_unproven", "retry": "explicit_only",
            }
        loan_key = hashlib.sha256(
            (str(loan.source_provider).lower() + "\\0" + loan_id).encode("utf-8")
        ).hexdigest()[:32]
        lock_path = acquisition_lock_file().with_name(
            "cifereca-loan-" + loan_key + ".lock"
        )
        try:
            with _AUTOMATIC_LOAN_GUARD, InterProcessLock(lock_path):
                return self._acquire_active_loan_automatically_guarded(
                    loan, force_refresh=force_refresh
                )
        except LockBusyError:
            return {
                "status": "attention_required", "loan_id": loan.id,
                "code": "acquisition_busy", "retry": "explicit_only",
            }

    def _acquire_active_loan_automatically_guarded(self, loan: Loan, *, force_refresh: bool = False) -> dict[str, Any]:
        """Acquire one active digital loan and fulfill ACSM through Kobo-Adept.

        Provider download remains responsible for obtaining the ACSM. Paris owns
        the destination and hands every ACSM to Adept; no ADEPT implementation
        leaks into Paris.
        """
        started = time.monotonic()
        _TRACE_LOG.info(
            "TRACE download loan_auto begin loan_id=%s item_id=%s title=%r provider=%s credential=%s account=%s direct_url_present=%s",
            loan.id, loan.record_id, loan.title, loan.source_provider, loan.source_credential, loan.source_account_id, bool(loan.ebook_url),
        )
        # DRM must never inherit the currently selected identity when a provider
        # connection has no persisted owner. Keep this fail-closed at the
        # automatic acquisition boundary; unrelated UI navigation may still
        # use the historical identity_for_connection fallback.
        bound_identity_id = self.storage.identity_for_connection(
            loan.source_provider, loan.source_credential
        )
        identity = self.identity_by_id(bound_identity_id)
        if identity is None:
            _TRACE_LOG.warning(
                "TRACE download loan_auto blocked reason=unbound_connection_identity provider=%s",
                loan.source_provider,
            )
            return {
                "status": "attention_required", "loan_id": loan.id,
                "code": "adept_identity_unproven",
                "reason": "unbound_connection_identity",
                "retry": "explicit_only",
            }
        _TRACE_LOG.info("TRACE download identity resolved loan_id=%s identity=%s", loan.id, identity.identity_id)
        if str(loan.source_provider).lower() == "paris":
            _, borrower_code = self._paris_adept_identity_for_loan(
                loan, connection_identity_id=identity.identity_id,
            )
            if borrower_code:
                _TRACE_LOG.warning(
                    "TRACE download loan_auto blocked reason=%s provider=%s",
                    borrower_code, loan.source_provider,
                )
                return {
                    "status": "attention_required", "loan_id": loan.id,
                    "code": borrower_code, "retry": "explicit_only",
                }
        entitlement_inventory = self.storage.entitlements(
            provider_id=loan.source_provider, state="active"
        )
        if str(loan.source_provider).lower() == "paris" and self._cross_identity_loan_conflict(
            self.storage.acquisitions(provider_id=loan.source_provider),
            provider_loan_id=loan.id, identity_id=identity.identity_id,
            entitlements=entitlement_inventory,
        ):
            _TRACE_LOG.warning(
                "TRACE download loan_auto blocked reason=cross_identity_loan provider=%s",
                loan.source_provider,
            )
            return {
                "status": "attention_required", "loan_id": loan.id,
                "code": "adept_loan_identity_conflict",
                "reason": "cross_identity_loan", "retry": "explicit_only",
            }
        entitlement_rows = [
            row for row in entitlement_inventory
            if row.get("loan_id") == loan.id and row.get("identity_id") == identity.identity_id
        ]
        entitlement_id = entitlement_rows[0]["entitlement_id"] if entitlement_rows else None
        existing = loan_artifact_state.local_catalog_candidates(
            self.storage.catalog_entries(),
            identity_id=identity.identity_id,
            source_provider=loan.source_provider,
            item_id=str(loan.record_id or loan.id),
        )
        # Inventory is a projection. Validate its files before deciding to skip.
        # Keep invalidation writes inline, in the same candidate order and under
        # the existing per-loan locks. No side effects occur in the helper.
        valid_existing = []
        for entry in existing:
            usable, path = loan_artifact_state.inspect_catalog_local_file(
                entry, artifact_inspector=inspect_artifact,
            )
            if usable:
                valid_existing.append(entry)
                continue
            self.storage.catalog_entry_put({**entry, "local_state": "missing" if path is None or not path.is_file() else "invalid"})
            _TRACE_LOG.info("TRACE download local-recheck provider_loan_id=%s catalog_state=%s", loan.id, "missing" if path is None or not path.is_file() else "invalid")
        existing = valid_existing
        previous_state = loan_artifact_state.inspect_previous_loan_artifact(
            self.storage.acquisitions(provider_id=loan.source_provider),
            identity_id=identity.identity_id,
            credential_id=loan.source_credential,
            account_id=loan.source_account_id,
            item_id=str(loan.record_id or loan.id),
            source_provider=loan.source_provider,
            artifact_inspector=inspect_artifact,
        )
        previous = previous_state.row
        previous_meta = previous_state.metadata
        previous_path = previous_state.path
        previous_inspection = previous_state.inspection
        legacy_acsm_artifact = previous_state.legacy_acsm_artifact
        previous_complete_local = previous_state.complete_local
        if previous_complete_local and not existing and not force_refresh:
            repaired_index = self.catalog_index_path(
                previous_path, title=loan.title, item_id=str(loan.record_id or loan.id),
                identity_id=identity.identity_id, provider_id=loan.source_provider,
            )
            _TRACE_LOG.info(
                "TRACE download loan_auto catalog-relink loan_id=%s path=%s status=%s",
                loan.id, previous_path, repaired_index.get("status"),
            )
            existing = [repaired_index] if repaired_index.get("status") == "indexed" else existing
        legacy_reason = (
            "artifact_validation"
            if legacy_acsm_artifact and previous_meta.get("post_processed")
            else "missing_post_process_marker"
            if legacy_acsm_artifact
            else ""
        )
        decision = decide_artifact_reuse(
            force_refresh=force_refresh,
            catalog_valid=bool(existing),
            ledger_complete_valid=previous_complete_local,
            legacy_repair=legacy_acsm_artifact,
            legacy_reason=legacy_reason,
            previous_exists=previous is not None,
            previous_path_exists=bool(previous_path is not None and previous_path.is_file()),
            previous_valid=(previous_inspection.valid if previous_inspection is not None else None),
            previous_protected=(previous_inspection.protected if previous_inspection is not None else None),
        )
        _TRACE_LOG.info(
            "TRACE download loan_auto decision loan_id=%s identity=%s action=%s reason=%s source=%s catalog_valid=%s ledger_valid=%s force_refresh=%s dt=%.3fs",
            loan.id, identity.identity_id, decision.action, decision.reason, decision.source,
            bool(existing), previous_complete_local, force_refresh, time.monotonic()-started,
        )
        if decision.action == "reuse":
            _TRACE_LOG.info(
                "TRACE download loan_auto skip-local loan_id=%s identity=%s source=%s reason=%s dt=%.3fs",
                loan.id, identity.identity_id, decision.source, decision.reason, time.monotonic()-started,
            )
            return {
                "status": "unchanged",
                "loan_id": loan.id,
                "decision": decision.action,
                "reason": decision.reason,
                "source": decision.source,
            }
        if blocked_automatic_adept_retry(
            previous, provider_loan_id=loan.id, entitlement_id=entitlement_id,
            force_refresh=force_refresh,
        ):
            # The existing ledger is the authoritative pending-intervention
            # state. Preserve diagnostic, ACSM staging and any prior artifact;
            # do not acquire the provider link or attempt a different profile.
            code = str((previous or {}).get("attention_code")
                       or (previous or {}).get("error_code") or "adept_attention_required")
            _TRACE_LOG.warning(
                "TRACE download loan_auto auto-retry-blocked provider=%s "
                "reason=persisted_adept_attention code=%s",
                loan.source_provider, code,
            )
            return {
                "status": "attention_required", "loan_id": loan.id,
                "code": code, "reason": "persisted_adept_attention",
                "retry": "explicit_only",
            }

        if decision.action == "repair_legacy":
            _TRACE_LOG.info(
                "TRACE download loan_auto repair-legacy loan_id=%s reason=%s",
                loan.id, decision.reason,
            )

        ledger_base = {
            "entitlement_id": entitlement_id,
            "provider_id": loan.source_provider,
            "credential_id": loan.source_credential,
            "account_id": loan.source_account_id,
            "identity_id": identity.identity_id,
            "item_id": str(loan.record_id or loan.id),
            "title": loan.title,
        }
        self.storage.acquisition_put({
            **ledger_base, "status": "acquiring", "strategy": "loan_auto",
            "executor": "provider",
            "metadata": {**previous_meta, "provider_loan_id": loan.id},
        })
        _TRACE_LOG.info("TRACE download ledger acquiring loan_id=%s item_id=%s", loan.id, ledger_base["item_id"])
        retained_acsm: Path | None = None
        failure_executor = "provider"
        try:
            provider_started = time.monotonic()
            _TRACE_LOG.info("TRACE download provider begin loan_id=%s provider=%s", loan.id, loan.source_provider)
            acquisition = self.acquire_digital_loan(loan, force_refresh=True) if force_refresh else self.acquire_digital_loan(loan)
            _TRACE_LOG.info(
                "TRACE download provider end loan_id=%s kind=%s file=%s bytes=%s dt=%.3fs",
                loan.id, acquisition.kind, acquisition.path.name if acquisition.path else None,
                acquisition.path.stat().st_size if acquisition.path and acquisition.path.is_file() else 0,
                time.monotonic()-provider_started,
            )
            final_path = acquisition.path
            artifact_drm = "unknown"
            artifact_format: str | None = None
            artifact_protected: bool | None = None
            hook_info: Any = None
            if acquisition.kind == "acsm" and acquisition.acsm_path is not None:
                retained_acsm = acquisition.acsm_path
                failure_executor = "kobo-adept"
                # Check the pinned borrower grant again immediately before ADEPT:
                # provider staging can take time, and a grant may be revoked.
                adept_borrower_identity_id = identity.identity_id
                borrower_code = None
                if str(loan.source_provider).lower() == "paris":
                    adept_borrower_identity_id, borrower_code = self._paris_adept_identity_for_loan(
                        loan, connection_identity_id=identity.identity_id,
                    )
                if borrower_code:
                    code = borrower_code
                    self.storage.acquisition_put({
                        **ledger_base, "status": "attention_required",
                        "strategy": "loan_auto", "executor": "kobo-adept",
                        "attention_code": code,
                        "metadata": {**previous_meta, "provider_loan_id": loan.id,
                                     "acsm_staging_path": str(retained_acsm)},
                    })
                    _TRACE_LOG.warning(
                        "TRACE download loan_auto blocked reason=ambiguous_borrower_account provider=%s",
                        loan.source_provider,
                    )
                    return {
                        "status": "attention_required", "loan_id": loan.id,
                        "code": code, "retry": "explicit_only",
                    }
                try:
                    profile_name, profile_source = self.resolve_adept_profile_for_identity(adept_borrower_identity_id)
                except AdeptProfileResolutionError as exc:
                    self.storage.acquisition_put({
                        **ledger_base, "status": "attention_required", "strategy": "loan_auto", "executor": "kobo-adept",
                        "attention_code": exc.code, "error_message": str(exc),
                        "metadata": {**previous_meta, "provider_loan_id": loan.id,
                                     "acsm_staging_path": str(retained_acsm)},
                    })
                    _TRACE_LOG.info(
                        "TRACE download acsm cleanup loan_id=%s action=keep reason=%s file=%s",
                        loan.id, exc.code, retained_acsm,
                    )
                    return {"status": "attention_required", "loan_id": loan.id, "code": exc.code, "error": str(exc)}
                _TRACE_LOG.info(
                    "TRACE download adept profile-resolved loan_id=%s connection_identity=%s borrower_identity=%s profile=%s source=%s",
                    loan.id, identity.identity_id, adept_borrower_identity_id, profile_name, profile_source,
                )
                # 0.13.3+ distinguishes a reader-backed profile from one that
                # can actually execute ACSM/libgourou operations. Only block
                # when Adept explicitly publishes a negative readiness signal;
                # older contracts that omit it remain compatible.
                try:
                    profile_rows = self.kobo_profiles()
                except Exception:
                    profile_rows = []
                profile_row = self._adept_profile_row(profile_name, profile_rows)
                if profile_row:
                    if adept_acsm_plan.profile_is_explicitly_acsm_unready(profile_row):
                        code = "adept_profile_acsm_not_ready"
                        message = "Le profil Kobo-Adept lié à cette identité n'est pas prêt pour les acquisitions ACSM"
                        self.storage.acquisition_put({
                            **ledger_base, "status": "attention_required", "strategy": "loan_auto", "executor": "kobo-adept",
                            "attention_code": code, "error_message": message,
                            "metadata": {**previous_meta, "provider_loan_id": loan.id,
                                         "acsm_staging_path": str(retained_acsm),
                                         "adept_attempt_profile": profile_name},
                        })
                        _TRACE_LOG.info(
                            "TRACE download acsm cleanup loan_id=%s action=keep reason=%s profile=%s file=%s",
                            loan.id, code, profile_name, retained_acsm,
                        )
                        return {"status": "attention_required", "loan_id": loan.id, "code": code, "error": message}

                acsm_format, format_source, suffix = adept_acsm_plan.select_acsm_format(
                    detect_acsm_format(acquisition.acsm_path),
                )
                destination, adept_destination, repair_mode = adept_acsm_plan.plan_adept_destination(
                    self.library_identity_provider_dir(identity.identity_id, loan.source_provider)
                    / self._loan_destination_name(loan.title, suffix),
                    force_refresh=force_refresh,
                    legacy_acsm_artifact=legacy_acsm_artifact,
                    previous_path=previous_path,
                    unique_suffix=lambda: uuid.uuid4().hex,
                )
                try:
                    adept_destination.unlink(missing_ok=True)
                except OSError:
                    pass
                request = adept_acsm_plan.build_fulfillment_request(
                    acsm_path=acquisition.acsm_path,
                    destination=adept_destination,
                    acsm_format=acsm_format,
                    profile_name=profile_name,
                    provider=loan.source_provider,
                    title=loan.title,
                    loan_id=loan.id,
                )
                _TRACE_LOG.info(
                    "TRACE download adept begin provider_loan_id=%s profile=%s source_file=%s destination=%s format=%s format_source=%s",
                    loan.id, profile_name, acquisition.acsm_path.name, adept_destination, acsm_format, format_source,
                )
                adept_started = time.monotonic()
                originating_profile_id = self._originating_adept_profile_id(adept_borrower_identity_id, profile_name)
                result = self.execute_acquisition_request(request)
                from .adept_binding import capture_binding
                adept_binding = capture_binding(result, profile_name=profile_name, profile_id=originating_profile_id)
                diag = self.runtime_diagnostic("runtime.kobo-adept")
                _TRACE_LOG.info(
                    "TRACE download adept end provider_loan_id=%s returncode=%s duration_ms=%s stdout_mode=%s dt=%.3fs",
                    loan.id, diag.get("returncode"), diag.get("duration_ms"), diag.get("stdout_mode"), time.monotonic()-adept_started,
                )
                artifact = result.get("artifact") if isinstance(result, dict) else None
                artifact_path = str((artifact or {}).get("path") or adept_destination)
                produced_path = Path(artifact_path)
                if produced_path.resolve() != adept_destination.resolve():
                    raise ProviderConfigurationError("Chemin d’artefact ADEPT inattendu")
                artifact_drm = str((artifact or {}).get("drm") or "unknown")
                artifact_format = str((artifact or {}).get("format") or acsm_format or "").casefold() or None
                hook_info = ((result or {}).get("hook") or (result or {}).get("post_download")) if isinstance(result, dict) else None
                _TRACE_LOG.info(
                    "TRACE download adept artifact loan_id=%s path=%s format=%s drm=%s hook_reported=%s repair_mode=%s",
                    loan.id, produced_path, artifact_format, artifact_drm, bool(hook_info), repair_mode,
                )
                if not produced_path.is_file():
                    raise ProviderConfigurationError("Kobo-Adept n'a produit aucun artefact")

                actual_before_publish = inspect_artifact(produced_path)
                if not actual_before_publish.valid or not actual_before_publish.media_format:
                    raise ProviderConfigurationError(f"Artefact Kobo-Adept invalide: {actual_before_publish.reason or 'format inconnu'}")
                actual_format = actual_before_publish.media_format
                actual_suffix = ".pdf" if actual_format == "pdf" else ".epub"
                corrected = adept_acsm_plan.plan_produced_format_path(
                    produced_path, actual_suffix, unique_suffix=lambda: uuid.uuid4().hex,
                )
                if corrected != produced_path:
                    produced_path.replace(corrected)
                    produced_path = corrected
                    _TRACE_LOG.info("TRACE download adept rename-format loan_id=%s format=%s path=%s", loan.id, actual_format, produced_path)
                destination = adept_acsm_plan.plan_publish_format_path(
                    destination, actual_suffix, repair_mode=repair_mode,
                    unique_suffix=lambda: uuid.uuid4().hex,
                )
                inspection = inspect_artifact(produced_path, actual_format)
                if not inspection.valid:
                    raise ProviderConfigurationError(f"Artefact final invalide: {inspection.reason or 'validation'}")
                artifact_format = actual_format
                artifact_protected = inspection.protected
                if artifact_protected is True:
                    raise ProviderConfigurationError("Artefact final encore protégé après Kobo-Adept")

                if repair_mode:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.exists() and destination.resolve() != produced_path.resolve():
                        destination.unlink()
                    produced_path.replace(destination)
                    final_path = destination
                    if previous_path is not None and previous_path.exists() and previous_path.resolve() != destination.resolve():
                        previous_path.unlink(missing_ok=True)
                    _TRACE_LOG.info("TRACE download adept repair-replace loan_id=%s path=%s", loan.id, destination)
                else:
                    if produced_path.resolve() != destination.resolve():
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.unlink(missing_ok=True)
                        produced_path.replace(destination)
                    final_path = destination
            if final_path is None or not final_path.is_file():
                raise ProviderConfigurationError("L'acquisition automatique n'a produit aucun artefact local")
            final_inspection = inspect_artifact(final_path)
            if final_path.suffix.casefold() in {".epub", ".pdf"} and not final_inspection.valid:
                raise ProviderConfigurationError(f"Artefact local invalide: {final_inspection.reason or 'validation'}")
            _TRACE_LOG.info(
                "TRACE download artifact ready loan_id=%s path=%s bytes=%s format=%s protected=%s",
                loan.id, final_path, final_path.stat().st_size, final_inspection.media_format, final_inspection.protected,
            )
            index_started = time.monotonic()
            scan = self.catalog_index_path(
                final_path, title=loan.title, item_id=str(loan.record_id or loan.id),
                identity_id=identity.identity_id, provider_id=loan.source_provider,
            )
            _TRACE_LOG.info("TRACE download catalog index end loan_id=%s status=%s dt=%.3fs", loan.id, scan.get("status"), time.monotonic()-index_started)
            if scan.get("status") != "indexed":
                raise ProviderConfigurationError(f"Indexation locale impossible: {scan.get('reason') or scan.get('status')}")
            self.storage.acquisition_put({
                **ledger_base, "status": "complete", "strategy": "loan_auto",
                "artifact_id": scan.get("artifact_id"),
                "executor": "kobo-adept" if acquisition.kind == "acsm" else "provider",
                "metadata": {
                    "path": str(final_path), "catalog_index": scan, "provider_loan_id": loan.id,
                    "reacquisition": force_refresh,
                    "acquisition_decision": decision.as_dict(),
                    "preserved_artifact_path": str(previous_path) if force_refresh and previous_path and previous_path.is_file() else None,
                    "adept_binding": adept_binding if acquisition.kind == "acsm" else None,
                    "post_processed": True if acquisition.kind == "acsm" else None,
                    "adept_artifact_drm": artifact_drm if acquisition.kind == "acsm" else None,
                    "artifact_format": artifact_format or final_inspection.media_format,
                    "artifact_protected": artifact_protected if acquisition.kind == "acsm" else final_inspection.protected,
                    "hook_reported": bool(hook_info) if acquisition.kind == "acsm" else None,
                },
            })
            if acquisition.kind == "acsm":
                self._cleanup_acsm_after_success(retained_acsm, loan_id=loan.id)
                retained_acsm = None
            if acquisition.kind == "acsm":
                self._diagnose_adept_binding(adept_binding, provider_loan_id=loan.id)
            _TRACE_LOG.info("TRACE download loan_auto complete provider_loan_id=%s path=%s dt=%.3fs", loan.id, final_path, time.monotonic()-started)
            return {
                "status": "complete",
                "loan_id": loan.id,
                "path": str(final_path),
                "adept_return_available": bool(acquisition.kind == "acsm" and adept_binding.get("return_available")),
                "decision": decision.action,
                "reason": decision.reason,
                "source": decision.source,
            }
        except (KoboAdeptError, UserFacingError) as exc:
            if retained_acsm is not None and retained_acsm.exists():
                _TRACE_LOG.info("TRACE download acsm cleanup loan_id=%s action=keep reason=adept_error file=%s", loan.id, retained_acsm)
            code = str(getattr(exc, "code", "") or "adept_error")
            process = {}
            if failure_executor == "kobo-adept":
                raw = self.runtime_diagnostic("runtime.kobo-adept")
                process = loan_failure_evidence.process_failure_facts(
                    raw, exc=exc,
                    candidate=locals().get("adept_destination"), path_type=Path,
                )
                self.diagnostics.add("KOBO-ADEPT", "acquisition failed", "error",
                    detail={"code": code, "message": str(exc), **process}, level="errors")
            message = str(exc)
            if code == "pnb_redirect_refused":
                plugin_version = "inconnue"
                if self._plugin_manager is not None:
                    try:
                        plugin_version = self._plugin_manager.status("provider." + loan.source_provider).descriptor.version
                    except KeyError:
                        pass
                message += f" (Plugin {loan.source_provider} {plugin_version} ; consulter les traces PNB target_scheme / target_port pour distinguer protocole, port et hôte.)"
                self.diagnostics.add("PNB", "redirect refused", "warning", detail={"provider_loan_id": loan.id, "provider": loan.source_provider, "plugin_version": plugin_version}, level="errors")
            if process:
                facts = ", ".join(f"{key}={value}" for key, value in process.items())
                message += f" ({facts})"
            # This is a decoder/transport-contract failure, *not* an Adobe
            # licensing decision. The next opening may retry it automatically.
            # Never infer a named Adobe refusal from a generic CLI exit code.
            retryable = bool(getattr(exc, "retryable", False)) or code in {
                "kobo_adept_invalid_json",
            }
            _TRACE_LOG.warning(
                "Acquisition failed loan_id=%s executor=%s code=%s retryable=%s exit_code=%s message=%s dt=%.3fs process=%s",
                loan.id, failure_executor, code, retryable, getattr(exc, "exit_code", None), str(exc), time.monotonic()-started, process,
            )
            status = "retryable_error" if retryable else "attention_required"
            attention_code = adept_attention_code(code, message, executor=failure_executor)
            metadata = loan_failure_evidence.adept_failure_metadata(
                previous_meta, loan_id=loan.id, process=process,
                retained_acsm=retained_acsm, executor=failure_executor,
                attempt_profile=locals().get("profile_name"),
            )
            self.storage.acquisition_put({
                **ledger_base, "status": status, "strategy": "loan_auto", "executor": failure_executor,
                "error_code": code, "error_message": message, "metadata": metadata,
                "attention_code": None if status == "retryable_error" else attention_code,
            })
            return {
                "status": status, "loan_id": loan.id, "error": message,
                "code": attention_code if status == "attention_required" else code,
                "process": process,
            }
        except Exception as exc:
            if retained_acsm is not None and retained_acsm.exists():
                _TRACE_LOG.info("TRACE download acsm cleanup loan_id=%s action=keep reason=acquisition_error file=%s", loan.id, retained_acsm)
            _TRACE_LOG.warning(
                "TRACE download provider error loan_id=%s error=%s message=%s dt=%.3fs",
                loan.id, type(exc).__name__, str(exc), time.monotonic()-started,
            )
            attention_code = self._provider_attention_code(exc)
            # "Quota dépassé" from DownloadDigitalDocument is not a reliable
            # indication that the already-active loan or its ACSM is unavailable
            # (manual ACSM retrieval can still succeed). Keep it observable but
            # retryable instead of permanently blocking the loan.
            validation_failure = failure_executor == "kobo-adept" and isinstance(exc, ProviderConfigurationError)
            metadata = loan_failure_evidence.provider_failure_metadata(
                previous_meta, loan_id=loan.id, retained_acsm=retained_acsm,
                attention_code=attention_code, validation_failure=validation_failure,
            )
            self.storage.acquisition_put({
                **ledger_base, "status": "retryable_error", "strategy": "loan_auto", "executor": failure_executor,
                "error_code": "artifact_invalid" if validation_failure else type(exc).__name__, "error_message": str(exc),
                "attention_code": None,
                "metadata": metadata,
            })
            return {"status": "retryable_error", "loan_id": loan.id, "code": attention_code, "error": str(exc)}

    def _synchronize_account_source_impl(self, account: Account, *, deep: bool = False, control: Any = None) -> dict[str, Any]:
        """Synchronize one explicit account/source without changing account selection.

        This is the Core-owned primitive used by the public sync broker.  It
        deliberately isolates one source so disabling or failing a provider does
        not prevent the other sources from being synchronized.
        """
        return sync_coordination._synchronize_account_source_impl(self, account, deep=deep, control=control)

    def synchronize_account_source(self, account: Account, *, deep: bool = False, control: Any = None) -> dict[str, Any]:
        return sync_coordination.synchronize_account_source(self, account, deep=deep, control=control)

    def preview_library_filename(self, *, title: str, author: str = "", series: str = "", index: str = "", extension: str = "epub") -> str:
        """Return the Core naming policy result without touching the filesystem."""
        title = str(title or "").strip() or "Sans titre"
        author = str(author or "").strip()
        series = str(series or "").strip()
        index = str(index or "").strip()
        template = str(self.config.get("library.filename_template", "{author} - {title}"))
        values = {"title": title, "author": author, "series": series, "index": index}
        try:
            stem = template.format(**values)
        except (KeyError, ValueError):
            stem = f"{author} - {title}" if author else title
        stem = " - ".join(part.strip() for part in stem.split(" - ") if part.strip())
        safe = self.acquisition._safe_filename(stem or title)
        ext = str(extension or "epub").strip().lstrip(".") or "epub"
        return f"{safe}.{ext}"

    def opening_sync(self, progress: Callable[[dict[str, Any]], None] | None = None, *, deep: bool = False) -> dict[str, Any]:
        """Synchronize remote state; filesystem reconciliation is opt-in/deep."""
        return sync_coordination.opening_sync(self, progress, deep=deep, lock_factory=InterProcessLock, lock_path=acquisition_lock_file)

    def _opening_sync_locked(self, progress: Callable[[dict[str, Any]], None] | None = None, *, deep: bool = False) -> dict[str, Any]:
        return sync_coordination._opening_sync_locked(self, progress, deep=deep, logger=_TRACE_LOG)

    def _record_action(self, identity_id: str | None, action_type: str, outcome: str, *, provider_id: str = "", plugin_id: str = "", facts: Mapping[str, Any] | None = None) -> None:
        return sync_coordination._record_action(self, identity_id, action_type, outcome, provider_id=provider_id, plugin_id=plugin_id, facts=facts)

    def action_statistics(self, identity_id: str, *, window_seconds: int = 2592000):
        return sync_projection.action_statistics(self, identity_id, window_seconds=window_seconds)

    def _record_loan_statistics(self, account, rows, *, from_cache=False):
        return sync_coordination._record_loan_statistics(self, account, rows, from_cache=from_cache)

    def identity_statistics(self, identity_id, *, window_seconds: int = 2592000):
        """Persisted inventory facts only; no scan, authentication or download."""
        return sync_projection.identity_statistics(self, identity_id, window_seconds=window_seconds)

    def dashboard_state(self) -> list[dict[str, Any]]:
        """Return dashboard cards without triggering network I/O."""
        return sync_projection.dashboard_state(self)

    def acquisition_ledger_records(self, *, status: str | None = None) -> list[dict[str, Any]]:
        return self.storage.acquisitions(status=status)

    def reconcile_acquisitions(self) -> dict[str, Any]:
        interrupted = self.acquisition_reconciler.recover_interrupted()
        realized = self.acquisition_reconciler.realize_expired_loans()
        scan = self.catalog_scan()
        return {
            "recovered_interrupted": interrupted,
            "loans_realized": realized,
            "catalog": scan,
            "storage_schema_version": self.storage.schema_version(),
        }

    def kobo_backup_records(self) -> list[dict[str, Any]]:
        return kobo_backup.kobo_backup_records(self)

    def kobo_backup_enabled(self) -> bool:
        return kobo_backup.kobo_backup_enabled(self)

    def kobo_backup_interval_minutes(self) -> int:
        return kobo_backup.kobo_backup_interval_minutes(self)

    def _kobo_backup_target_dir(self, identity_id: str | None = None) -> Path:
        # 0.8.1 makes the visible layout identity/provider mandatory for new
        # materializations. The historical Kobo override remains a base root;
        # existing flat files are never moved automatically.
        return kobo_backup._kobo_backup_target_dir(self, identity_id)

    @staticmethod
    def _manifest_author(item: dict[str, Any]) -> str | None:
        return kobo_backup._manifest_author(item)

    @staticmethod
    def _kobo_backup_error_info(exc: Exception) -> dict[str, Any]:
        """Return a compact, non-secret diagnostic classification for a backup error."""
        return kobo_backup._kobo_backup_error_info(exc)

    def kobo_backup_diagnostic(self) -> dict[str, Any]:
        """Summarise the persisted Kobo backup state without network access."""
        return kobo_backup.kobo_backup_diagnostic(self)

    def kobo_backup_preview(
        self, credential_id: str, *,
        refresh_manifest: bool = False, verify_local_files: bool = False,
    ) -> dict[str, Any]:
        """Preview Kobo acquisition candidates, without writes or downloads.

        Optional refresh reads the authorized provider's *manifest only*.
        This result is advisory, not a permit to download or prune anything.
        """
        return kobo_backup.kobo_backup_preview(
            self, credential_id, refresh_manifest=refresh_manifest,
            verify_local_files=verify_local_files,
        )

    def _kobo_backup_due(self) -> bool:
        return kobo_backup._kobo_backup_due(self)

    def _kobo_backup_account_due(self, credential_id: str) -> bool:
        return kobo_backup._kobo_backup_due(self, credential_id=credential_id)

    def sync_kobo_backup(self, progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        """Compatibility entry point for the Kobo local synchronization.

        0.8.0 keeps the validated cross-process serialization for acquisition work. The
        same global lock is intentionally named for local acquisition rather
        than Kobo so the 0.8.x loan synchronizer can share it.
        """
        return kobo_backup.sync_kobo_backup(self, progress, lock_factory=InterProcessLock, lock_path=acquisition_lock_file)

    def sync_kobo_backup_account(
        self, credential_id: str,
        progress: Callable[[dict[str, Any]], None] | None = None,
        *, retry_scope_blocked: bool = False, cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """One Kobo account: cooperative cancellation and scoped delegation retry.

        retry_scope_blocked is for an explicit action after provider reauthorization.
        Cancellation stops between items, never amid a file write. All local
        artifact and duplicate reconciliation guards remain enforced.
        """
        return kobo_backup.sync_kobo_backup(
            self, progress, credential_id=credential_id, cancelled=cancelled,
            retry_scope_blocked=retry_scope_blocked,
            lock_factory=InterProcessLock, lock_path=acquisition_lock_file,
        )

    def kobo_unlinked_local_count(self, identity_id: str) -> int:
        """Conservative guard, never infer a Kobo link from a filename/title."""
        return kobo_backup.kobo_unlinked_local_count(self, identity_id)

    def _sync_kobo_backup_locked(self, progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        """Reconcile the Vault manifest and acquire missing Kobo books locally.

        Paris owns the destination and orchestration. Vault Provider API v2.x
        resolves Kobo acquisition information; Kobo-Adept's negotiated public
        contract executes the local download. Neither supplier drives the other.

        0.8.0 keeps Vault API 2.3 semantics and Adept contract v8+, but candidate policy/execution now live in the generic engine and Kobo adapter. It keeps
        transport selection with candidate.handling authoritative and adds Vault 2.3.3 library_sync pinned KDRM bridging:
        ACSM/ADEPT first, then SignedNoDrm/http_plain by direct HTTP, then true
        KDRM/kobo_kdrm through delegated Kobo execution. Explicit user-attention
        states and cross-process serialization remain unchanged. Adept contract v8 remains the preferred local executor;
        existing reconciliation safeguards still prevent duplicate redownloads.
        """
        return kobo_backup._sync_kobo_backup_locked(self, progress, logger=_TRACE_LOG)

    def _sync_kobo_backup_account_locked(
        self, credential_id: str,
        progress: Callable[[dict[str, Any]], None] | None = None,
        *, retry_scope_blocked: bool = False, cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Internal scoped variant: caller owns the global acquisition lock."""
        return kobo_backup._sync_kobo_backup_locked(
            self, progress, logger=_TRACE_LOG, credential_id=credential_id,
            cancelled=cancelled, retry_scope_blocked=retry_scope_blocked,
        )

    def _account_for_loan(self, loan: Loan) -> Account:
        """Resolve the exact borrower recorded on a loan.

        0.3.2 inferred this from the currently selected account and could pick
        the primary account for a linked-account loan. New loans carry
        ``source_account_id``; old cached loans keep a conservative fallback.
        """
        return loan_accounts.account_for_loan(
            loan, accounts=self.accounts, current_account_get=lambda: self.current_account,
            configuration_error=ProviderConfigurationError,
        )

    def _acquisition_work_dir(self, identity_id: str, provider_id: str, loan_id: str | None = None) -> Path:
        """Return a mutable staging directory outside the user library tree."""
        return acsm_staging.acquisition_work_dir(
            storage_path=self.storage.path, identity_id=identity_id,
            provider_id=provider_id, loan_id=loan_id,
            component_name=safe_library_component,
        )

    def _cleanup_acsm_after_success(self, path: Path | None, *, loan_id: str) -> None:
        return acsm_staging.cleanup_acsm_after_success(
            path, loan_id=loan_id, forget=acsm_cache.forget, logger=_TRACE_LOG,
        )

    def _cleanup_old_acsm_work(self, *, max_age_seconds: int | None = None) -> dict[str, int]:
        return acsm_staging.cleanup_old_acsm_work(
            self.storage.path, max_age_seconds=max_age_seconds,
            config_get=self.config.get, clock=time.time,
            forget=acsm_cache.forget, logger=_TRACE_LOG,
        )

    def acquire_digital_loan(self, loan: Loan, *, force_refresh: bool = False) -> Acquisition:
        started = time.monotonic()
        session = self._session_for_source(loan.source_provider, loan.source_credential)
        account = self._account_for_loan(loan)
        # Keep the stable Paris-side borrower key all the way through the
        # domain/acquisition pipeline. For the primary Paris account this is
        # ``primary``; the provider protocol may still legitimately use an
        # empty upstream UID, but only at the HTTP boundary.
        account_key = str(loan.source_account_id or account.unique_id or "primary")
        _TRACE_LOG.info(
            "TRACE download acquire provider-session loan_id=%s provider=%s credential=%s account_key=%s upstream_uid_present=%s",
            loan.id, session.provider_id, loan.source_credential, account_key, bool(account.unique_id),
        )
        # Digital provider payloads are staged outside ~/EBooks. In particular,
        # ACSM files are transient inputs and must not become user-library items.
        identity = self.identity_for_connection(loan.source_provider, loan.source_credential)
        library_target_dir = self.library_identity_provider_dir(identity.identity_id, loan.source_provider)
        target_dir = self._acquisition_work_dir(identity.identity_id, loan.source_provider, loan.id)
        cached = None if force_refresh else acsm_cache.reuse(target_dir, loan, identity.identity_id, account_key)
        if cached is not None:
            path, url = cached, loan.ebook_url or ""
            self.diagnostics.add("PNB", "reuse artifact", detail={"provider": session.provider_id, "file": path.name})
        else:
            _TRACE_LOG.info("TRACE download provider-download begin loan_id=%s staging_dir=%s", loan.id, target_dir)
            path, url = session.download_digital(loan, account, target_dir)
        _TRACE_LOG.info(
            "TRACE download provider-download end loan_id=%s file=%s suffix=%s bytes=%s dt=%.3fs",
            loan.id, path.name, path.suffix, path.stat().st_size if path.is_file() else 0, time.monotonic()-started,
        )
        hook_item = loan_download_projection.build_hook_item(
            loan, url, result_factory=SearchResult,
        )
        if cached is None:
            self.acquisition.run_post_download(hook_item, path, url, target_dir)
            acsm_cache.remember(path, target_dir, loan, identity.identity_id, account_key)
        is_acsm = path.suffix.casefold() == ".acsm"
        if not is_acsm and path.is_file():
            # A provider may yield a final file directly. Publish it into the
            # Paris-owned library only after the provider stage succeeded.
            published = library_target_dir / path.name
            if path.resolve() != published.resolve():
                published.parent.mkdir(parents=True, exist_ok=True)
                path.replace(published)
                path = published
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        self.diagnostics.add(
            "PNB", "download artifact",
            detail={"provider": session.provider_id, "file": path.name, "bytes": size},
        )
        return loan_download_projection.build_acquisition(
            loan, account_key=account_key, path=path, url=url,
            is_acsm=is_acsm, mime_type=self.acquisition.mime_type_for(path, hook_item),
            library_target_dir=library_target_dir, acquisition_factory=Acquisition,
        )

    def prepare_digital_download(self, loan: Loan, *, force_refresh: bool = False) -> tuple[Path, str]:
        """0.3.2 compatibility wrapper around ``acquire_digital_loan``."""
        result = self.acquire_digital_loan(loan, force_refresh=True) if force_refresh else self.acquire_digital_loan(loan)
        path = result.path
        if path is None:
            raise ProviderConfigurationError("L'acquisition numérique n'a produit aucun fichier local")
        return path, result.url

    def _download_filename(self, response, loan: Loan, content_type: str) -> str:
        disposition = response.headers.get("content-disposition", "")
        m = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition, re.I)
        if m:
            name = m.group(1).strip().strip('"')
        else:
            name = Path(urlparse(str(response.url)).path).name
        if not name or "." not in name:
            ext = ".acsm" if "adobe" in content_type.lower() or b"fulfillmentToken" in response.content[:2048] else ".bin"
            name = f"{loan.title}{ext}"
        name = SAFE_FILENAME.sub("_", name).strip(" ._") or "download.acsm"
        return name[:180]
