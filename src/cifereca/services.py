from __future__ import annotations

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
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urlparse, urljoin
from . import acsm_cache
from .acquisition import Acquisition, AcquisitionManager, AcquisitionResult
from .acquisition_engine import AcquisitionLedger, AcquisitionReconciler, Entitlement, SourceIdentity
from .catalog import LocalCatalog
from .epub_metadata import cached_cover_url
from .config import Config
from .diagnostics import Diagnostics, redact
from .kobo import KoboAdeptError
from .kobo.acquisition import KoboOfferAdapter, KoboAcquisitionSelectionError
from .kobo.executor import execute_kobo_acquisition
from .kobo.vault import KoboVaultError
from .identities import Identity, IdentityPreferences, AdeptProfileResolver, AdeptProfileResolutionError
from .locking import InterProcessLock, LockBusyError
from .library_files import safe_library_component, sha256_file, try_sha256_file
from .artifact_inspection import detect_acsm_format, inspect_artifact
from .paths import acquisition_lock_file, data_dir
from .models import Account, Booking, HistoryItem, Holding, ItemDetail, Loan, SearchResult
from .providers import ProviderConfigurationError, ProviderRegistry, ProviderSearchCapabilities, ProviderSession
from .plugin_api import AttentionRequired, BundleError, DrmMutationRequest, UserFacingError, load_bundle_manifest
from .storage import Storage

SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._()\- àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ]+")

_TRACE_LOG = logging.getLogger("cifereca")
_SEARCH_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="cifereca-search")

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

    def accounts_for_identity(self, identity_id: str | None = None) -> list[Account]:
        target = identity_id or (self.current_identity.identity_id if self.current_identity else None)
        return [account for account in self.accounts if target is None or account.identity_id == target]

    def sync_source_id(self, account: Account) -> str:
        account_id = str(account.unique_id or "").strip() or f"{account.provider_id}:{account.credential_id}:primary"
        raw = "\0".join((str(account.identity_id or ""), str(account.provider_id or ""), str(account.credential_id or ""), account_id))
        return "sync:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def sync_source_enabled(self, account: Account) -> bool:
        raw = self.storage.metadata_get("sync.source.enabled." + self.sync_source_id(account))
        return raw is None or str(raw).strip().lower() not in {"0", "false", "no", "off"}

    def select_identity(self, identity_id: str) -> Identity:
        identity = self.identity_by_id(identity_id)
        if identity is None:
            raise ProviderConfigurationError(f"Identité inconnue: {identity_id}")
        self.current_identity = identity
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
        self._plugin_manager = manager
        attached: list[str] = []
        try:
            statuses = manager.candidates("provider.library")
        except Exception:
            return attached
        from .providers.base import ProviderPlugin
        for status in statuses:
            service = manager.service(status.descriptor.id)
            if isinstance(service, ProviderPlugin):
                self.registry.register(service, replace=True)
                self._provider_plugin_ids[service.descriptor.id] = status.descriptor.id
                attached.append(service.descriptor.id)
        if attached:
            self.diagnostics.add("PLUGIN", "external providers attached", "ok", detail={"providers": attached}, level="detailed")
        return attached

    @property
    def plugin_manager(self) -> Any | None:
        """Public-ish bridge used by the shell until the UI owns PluginContext directly."""
        return self._plugin_manager

    def _provider_plugin_status(self, provider_id: str) -> Any | None:
        manager = self._plugin_manager
        plugin_id = self._provider_plugin_ids.get(str(provider_id))
        if manager is None or not plugin_id:
            return None
        try:
            return manager.status(plugin_id)
        except Exception:
            return None

    def _provider_enabled_for_identity(self, provider_id: str, identity_id: str | None = None) -> bool:
        status = self._provider_plugin_status(provider_id)
        if status is None:
            return str(provider_id) not in self._provider_plugin_ids
        if not status.enabled or status.state != "ready":
            return False
        if getattr(status.descriptor, "instantiation", "multiple") != "singleton_public":
            return True
        identity = identity_id or (self.current_identity.identity_id if self.current_identity else "default")
        try:
            return bool(self._plugin_manager.enabled_for_identity(status.descriptor.id, identity))
        except Exception:
            return False

    def provider_descriptors(self):
        return self.registry.descriptors()

    def provider_diagnostic_state(self) -> dict[str, Any]:
        try:
            adept = self.kobo_adept_state()
        except Exception as exc:
            adept = {"available": False, "error": str(exc)}
        return {
            "plugins": self.registry.diagnostic_state(),
            "sessions": [session.diagnostic_state() for session in self._sessions.values() if session.credential_id != "legacy"],
            "errors": dict(self._session_errors),
            "kobo_adept": adept,
        }

    def close(self) -> None:
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
        for key, session in list(self._sessions.items()):
            try:
                session.close()
            except Exception:
                pass
            self._sessions.pop(key, None)
        self._session_errors = {}
        self._active_session_key = None
        self.primary = self._local_account()
        self.accounts = [self.primary]
        self.current_account = self.primary
        self.last_loans = []
        self.last_history = []
        self.last_bookings = []

    def connect_configured(self) -> dict[str, Any]:
        """Connect all enabled provider identities from local preferences.

        Failures are isolated per provider account: one unavailable source does
        not prevent another source from starting. At least one logical account
        must be available for the current account-oriented GUI. Anonymous
        catalogue-only providers will be included once their plugin is ready.
        """
        started_all = time.monotonic()
        _TRACE_LOG.info("TRACE connect-configured begin current_identity=%s", getattr(getattr(self, "current_identity", None), "identity_id", None))
        self.disconnect_configured()
        all_accounts: list[Account] = []
        connected: list[str] = []
        errors: dict[str, str] = {}

        rows = self.config.provider_accounts(enabled_only=True)
        configured_provider_ids = {str(row.get("provider") or "").strip().lower() for row in rows if isinstance(row, dict)}
        # Plugin API v1: a ready provider with no credential fields is a public
        # anonymous source.  Do not require a provider-specific config migration
        # or a fake account row merely to make it searchable.
        for plugin in self.registry.all():
            descriptor = plugin.descriptor
            provider_id = str(descriptor.id or "").strip().lower()
            plugin_status = self._provider_plugin_status(provider_id)
            instantiation = getattr(getattr(plugin_status, "descriptor", None), "instantiation", "multiple")
            if (
                provider_id
                and descriptor.state == "ready"
                and not descriptor.credential_fields
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
            provider_id = str(plugin.descriptor.id or "").strip().lower()
            plugin_status = self._provider_plugin_status(provider_id)
            if (
                not provider_id or plugin.descriptor.state != "ready" or plugin.descriptor.credential_fields
                or getattr(getattr(plugin_status, "descriptor", None), "instantiation", "multiple") != "singleton_public"
            ):
                continue
            credential_id = "__public__"
            session = plugin.create_session(
                credential_id=credential_id, label=plugin.descriptor.display_name, credentials={}, options={},
                config=self.config, storage=self.storage, diagnostics=self.diagnostics,
            )
            key = self._session_key(provider_id, credential_id)
            try:
                session.connect()
                self._sessions[key] = session
                connected.append(key)
                self.diagnostics.add(
                    "PLUGIN", "public provider connected", "ok",
                    detail={"provider": provider_id, "instantiation": "singleton_public"}, level="detailed",
                )
            except Exception as exc:
                try:
                    session.close()
                except Exception:
                    pass
                errors[key] = str(exc)

        _TRACE_LOG.info("TRACE connect-configured enabled-rows=%s", len(rows))
        for row in rows:
            provider_id = str(row.get("provider") or "").strip().lower()
            credential_id = str(row.get("id") or "").strip()
            identity_id = str(row.get("identity_id") or (self.current_identity.identity_id if self.current_identity else "default")).strip()
            if not provider_id or not credential_id:
                continue
            item_started = time.monotonic()
            _TRACE_LOG.info("TRACE provider prepare provider=%s credential=%s identity=%s", provider_id, credential_id, identity_id)
            plugin = self.registry.maybe_get(provider_id)
            if plugin is None:
                errors[f"{provider_id}:{credential_id}"] = "Plugin inconnu"
                continue
            if plugin.descriptor.state != "ready":
                # Skeletons are visible to the GUI/docs but never perform I/O.
                continue
            session = plugin.create_session(
                credential_id=credential_id,
                label=str(row.get("label") or plugin.descriptor.display_name),
                credentials=dict(row.get("credentials") or {}),
                options=dict(row.get("options") or {}),
                config=self.config, storage=self.storage, diagnostics=self.diagnostics,
            )
            key = self._session_key(provider_id, credential_id)
            try:
                _TRACE_LOG.info("TRACE provider connect begin provider=%s credential=%s identity=%s", provider_id, credential_id, identity_id)
                accounts = session.connect()
                _TRACE_LOG.info("TRACE provider connect end provider=%s credential=%s accounts=%s dt=%.3fs", provider_id, credential_id, len(accounts), time.monotonic()-item_started)
                self._sessions[key] = session
                connection_id = self.storage.provider_connection_put({
                    "identity_id": identity_id, "provider_id": provider_id, "credential_id": credential_id,
                    "label": str(row.get("label") or plugin.descriptor.display_name), "enabled": True,
                })
                for account in accounts:
                    account.identity_id = identity_id
                    self.storage.provider_account_put({
                        "connection_id": connection_id, "identity_id": identity_id, "provider_id": provider_id,
                        "credential_id": credential_id, "account_id": account.unique_id,
                        "label": account.display_name or ("Compte principal" if account.is_primary else "Compte lié"),
                        "is_primary": account.is_primary,
                        "metadata": {"barcode": account.barcode},
                    })
                all_accounts.extend(accounts)
                connected.append(key)
                self.diagnostics.add(
                    "PLUGIN", "provider connected", "ok",
                    detail={"provider": provider_id, "credential_id": credential_id, "accounts": len(accounts)},
                )
            except Exception as exc:
                _TRACE_LOG.exception("TRACE provider connect error provider=%s credential=%s identity=%s dt=%.3fs", provider_id, credential_id, identity_id, time.monotonic()-item_started)
                try:
                    session.close()
                except Exception:
                    pass
                errors[key] = str(exc)
                self.diagnostics.add(
                    "PLUGIN", "provider connect", "error",
                    detail={"provider": provider_id, "credential_id": credential_id, "error": type(exc).__name__, "message": str(exc)},
                )

        self._session_errors = errors
        if not all_accounts:
            # Local-first cifereca is usable without any provider. Plugin
            # installation/configuration and the local Library remain available.
            self.primary = self._local_account()
            self.accounts = [self.primary]
            self.current_account = self.primary
            self._active_session_key = None
            self.last_loans = []
            return {"accounts": self.accounts, "identities": self.identities, "loans": [], "connected": connected, "errors": errors}

        self.accounts = all_accounts
        self.current_account = all_accounts[0]
        self.current_identity = self.identity_by_id(self.current_account.identity_id) or self.current_identity
        self._active_session_key = self._session_key(self.current_account.provider_id, self.current_account.credential_id)
        self.primary = self.current_account
        loans = self.refresh_identity_loans(force=True)
        return {"accounts": self.accounts, "identities": self.identities, "loans": loans, "connected": connected, "errors": errors}

    def login(self, username: str, password: str) -> dict[str, Any]:
        raise ProviderConfigurationError(
            "La connexion legacy Paris a été retirée de cifereca 0.9.2 ; configurez le plugin provider.paris."
        )

    def select_account(self, unique_id: str, provider_id: str | None = None, credential_id: str | None = None) -> Account:
        for account in self.accounts:
            if account.unique_id != unique_id:
                continue
            if provider_id and account.provider_id != provider_id:
                continue
            if credential_id and account.credential_id != credential_id:
                continue
            self.current_account = account
            self.current_identity = self.identity_by_id(account.identity_id) or self.current_identity
            key = self._session_key(account.provider_id, account.credential_id)
            if key in self._sessions:
                self._active_session_key = key
            return account
        return self.current_account

    def _session_for_account(self, account: Account | None = None) -> ProviderSession:
        account = account or self.current_account
        key = self._session_key(account.provider_id, account.credential_id)
        session = self._sessions.get(key)
        if session is None:
            raise ProviderConfigurationError(f"Provider non connecté: {account.provider_id}")
        return session

    def _session_for_source(self, provider_id: str, credential_id: str | None = None) -> ProviderSession:
        if not self._provider_enabled_for_identity(provider_id):
            raise ProviderConfigurationError(f"Provider désactivé ou indisponible: {provider_id}")
        if credential_id:
            key = self._session_key(provider_id, credential_id)
            if key in self._sessions:
                return self._sessions[key]
        for session in self._sessions.values():
            if session.provider_id == provider_id and session.connected:
                return session
        raise ProviderConfigurationError(f"Provider non connecté: {provider_id}")

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

    def public_catalogue_url(self, row):
        from .catalogue_link import catalogue_url
        from urllib.parse import urljoin
        provider = str(row.get('provider_id') or row.get('source_provider') or '')
        descriptor = next((d for d in self.provider_descriptors() if d.id == provider), None)
        base = descriptor.homepage if descriptor else None
        if provider == 'paris':
            base = self.config.paris_base_url(row.get('source_credential'))
        elif provider == 'kobo' and not base:
            base = self.config.provider_option('kobo', 'catalogue_url', 'https://www.kobo.com', credential_id=row.get('source_credential'))
        metadata = row.get('metadata') or {}
        search = metadata.get('search_result') or {}
        friendly = row.get('friendly_url') or metadata.get('friendly_url') or (search.get('friendly_url') if isinstance(search, dict) else None)
        candidate = dict(row)
        if isinstance(friendly, str) and base:
            candidate['friendly_url'] = urljoin(base, friendly)
        url = catalogue_url(candidate) or catalogue_url({'friendly_url': base})
        if url: return url
        # A local book with unknown origin can still be sought in a public catalogue.
        fallback = self.config.get('library.public_catalogue_fallback', 'https://catalogue.bnf.fr')
        return catalogue_url({'friendly_url': fallback})

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
        key = self._key("history-v3")
        ttl = int(self.config.get("cache.history_seconds", 1800))
        if not force:
            cached = self.storage.cache_get("account", key)
            if isinstance(cached, list):
                self.last_history = [HistoryItem(**x) for x in cached]
                self.diagnostics.add("CACHE", "history", detail={"hit": True, "count": len(self.last_history)})
                return self.last_history
        session = self._session_for_account()
        if not session.capabilities.history:
            self.last_history = []
            return []
        rows = session.history(self.current_account)
        direct = sum(1 for x in rows if x.thumbnail_url)
        default_only = sum(1 for x in rows if not x.thumbnail_url and x.default_thumbnail_url)
        missing = sum(1 for x in rows if not x.thumbnail_url and not x.default_thumbnail_url)
        self.diagnostics.add(
            "API", "history thumbnails", "ok",
            detail={
                "provider_plugin": self.current_account.provider_id,
                "account": "primary" if self.current_account.is_primary else "linked",
                "count": len(rows), "direct_thumbnail": direct,
                "default_only": default_only, "missing": missing,
            },
            level="detailed",
        )
        self.storage.cache_put("account", key, [asdict(x) for x in rows], ttl)
        self.last_history = rows
        return rows

    @staticmethod
    def _history_fold(value: str | None) -> str:
        text = unicodedata.normalize("NFKD", str(value or "").casefold())
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        return re.sub(r"[^0-9a-z]+", " ", text).strip()

    @classmethod
    def _history_cover_score(
        cls,
        item_title: str,
        item_author: str | None,
        item_year: str | None,
        record_id: str | None,
        candidate: SearchResult,
    ) -> float:
        title = cls._history_fold(item_title)
        cand_title = cls._history_fold(candidate.title)
        if not title or not cand_title:
            return 0.0
        ratio = SequenceMatcher(None, title, cand_title).ratio()
        left = set(title.split())
        right = set(cand_title.split())
        token_overlap = len(left & right) / max(1, len(left | right))
        score = ratio * 55.0 + token_overlap * 25.0
        if title == cand_title:
            score += 30.0
        elif title in cand_title or cand_title in title:
            score += 12.0

        if record_id and str(candidate.record_id) == str(record_id):
            # Si Syracuse finit par renvoyer le même identifiant via Search,
            # c'est un signal plus fiable que le GetListHoldings défaillant.
            score += 60.0

        author = cls._history_fold(item_author)
        cand_author = cls._history_fold(candidate.author)
        if author and cand_author:
            author_ratio = SequenceMatcher(None, author, cand_author).ratio()
            aleft = set(author.split())
            aright = set(cand_author.split())
            author_tokens = len(aleft & aright) / max(1, len(aleft | aright))
            score += max(author_ratio, author_tokens) * 18.0
            if author == cand_author:
                score += 5.0

        year = re.sub(r"\D", "", str(item_year or ""))[:4]
        published = str(candidate.published_at or "")
        if year and year in published:
            score += 9.0
        return score

    @classmethod
    def _history_search_queries(cls, title: str) -> list[str]:
        """Construit au plus trois requêtes, du plus précis au plus tolérant.

        Syracuse peut indexer un titre différemment de celui renvoyé par
        ``ListHandings`` (sous-titre, ponctuation, mention de volume). On ne
        journalise jamais le texte de ces requêtes dans les diagnostics.
        """
        original = re.sub(r"\s+", " ", str(title or "")).strip()[:180]
        if not original:
            return []
        variants = [original]

        # Titre principal avant un séparateur de sous-titre fréquent.
        main = re.split(r"\s(?:[:;/=])\s|\s[-–—]\s", original, maxsplit=1)[0].strip()
        if len(main) >= 4:
            variants.append(main[:180])

        # Variante sans ponctuation ni diacritiques : utile quand ListHandings
        # et le catalogue n'emploient pas exactement la même typographie.
        folded = cls._history_fold(original)
        if folded:
            words = folded.split()
            variants.append(" ".join(words[:8])[:180])

        out: list[str] = []
        seen: set[str] = set()
        for value in variants:
            key = value.casefold().strip()
            if key and key not in seen:
                seen.add(key)
                out.append(value)
        return out[:3]

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
        provider = (provider or "SYRACUSE").upper()
        title = str(title or "").strip()
        if not title:
            return default_url, "not_found"

        folded = "\0".join([
            provider,
            str(record_id or ""),
            self._history_fold(title),
            self._history_fold(author),
            str(year or ""),
        ])
        key = hashlib.sha256(folded.encode("utf-8")).hexdigest()
        # Nouveau namespace : invalide automatiquement les not_found de 0.2.5.
        namespace = "history-cover-search-v2"
        cached = self.storage.cache_get(namespace, key)
        if isinstance(cached, dict) and cached.get("status"):
            status = str(cached.get("status"))
            url = str(cached.get("url") or "").strip() or None
            self.diagnostics.add(
                "COVER", "history search", "ok",
                detail={"provider": provider, "result": status, "cache": True},
                level="detailed",
            )
            return url or default_url, status

        # Cover enrichment is provider-agnostic: use the current provider
        # session contract rather than any concrete Syracuse client.
        session = self._session_for_account()
        all_rows: list[SearchResult] = []
        seen: set[str] = set()
        attempts = 0
        search_error: Exception | None = None
        search_queries = self._history_search_queries(title)
        folded_queries = {self._history_fold(q) for q in search_queries if q}

        for query in search_queries:
            attempts += 1
            try:
                rows = session.search(query, scope="all", page=0, field="all")
            except Exception as exc:
                search_error = exc
                self.diagnostics.add(
                    "COVER", "history search attempt", "error",
                    detail={
                        "provider": provider,
                        "attempt": attempts,
                        "error": type(exc).__name__,
                        "message": str(exc)[:160],
                    },
                    level="detailed",
                )
                continue

            for row in rows:
                if row.id not in seen:
                    seen.add(row.id)
                    all_rows.append(row)
            self.diagnostics.add(
                "COVER", "history search attempt", "ok",
                detail={
                    "provider": provider,
                    "attempt": attempts,
                    "rows": len(rows),
                    "rows_total": len(all_rows),
                    "with_thumbnail": sum(1 for x in rows if x.thumbnail_url),
                },
                level="detailed",
            )

            # Une égalité forte permet d'éviter les requêtes plus larges.
            preview = sorted(
                ((self._history_cover_score(title, author, year, record_id, x), x) for x in all_rows),
                key=lambda pair: pair[0], reverse=True,
            )
            if preview:
                pscore, pbest = preview[0]
                if str(record_id or "") and str(pbest.record_id) == str(record_id):
                    break
                if self._history_fold(pbest.title) == self._history_fold(title) and pscore >= 85.0:
                    break

        if not all_rows and search_error is not None:
            self.storage.cache_put(namespace, key, {"url": "", "status": "error"}, 5 * 60)
            self.diagnostics.add(
                "COVER", "history search", "error",
                detail={
                    "provider": provider,
                    "result": "error",
                    "cache": False,
                    "attempts": attempts,
                    "error": type(search_error).__name__,
                    "message": str(search_error)[:160],
                },
                level="detailed",
            )
            return default_url, "error"

        scored = sorted(
            ((self._history_cover_score(title, author, year, record_id, x), x) for x in all_rows),
            key=lambda pair: pair[0], reverse=True,
        )
        status = "not_found"
        url: str | None = None
        best_score = 0.0
        gap = 0.0
        cover_source = "none"
        best: SearchResult | None = None

        if scored:
            best_score, best = scored[0]
            second_score = scored[1][0] if len(scored) > 1 else 0.0
            gap = best_score - second_score
            best_title = self._history_fold(best.title)
            history_title = self._history_fold(title)
            exact_title = bool(history_title and best_title == history_title)
            exact_variant = bool(best_title and best_title in folded_queries)
            same_record = bool(record_id and str(best.record_id) == str(record_id))

            confident = False
            if same_record:
                confident = True
            elif (exact_title or exact_variant) and (len(scored) == 1 or gap >= 5.0):
                confident = True
            elif best_score >= 92.0 and gap >= 10.0:
                confident = True
            elif best_score >= 72.0:
                status = "ambiguous"

            if confident:
                if best.thumbnail_url:
                    url = best.thumbnail_url
                    status = "matched"
                    cover_source = "search"
                else:
                    # Les résultats physiques peuvent être dépourvus d'image
                    # dans CompactResult/CustomResult. L'identifiant Search.svc
                    # est en revanche valide pour la fiche détaillée.
                    try:
                        detail = session.holdings(best, search_terms="")
                        if detail.thumbnail_url:
                            url = detail.thumbnail_url
                            status = "matched"
                            cover_source = "detail"
                        else:
                            status = "no_cover"
                    except Exception as exc:
                        status = "no_cover"
                        self.diagnostics.add(
                            "COVER", "history detail cover", "error",
                            detail={
                                "provider": provider,
                                "error": type(exc).__name__,
                                "message": str(exc)[:160],
                            },
                            level="detailed",
                        )

        # Match positif durable ; les négatifs restent courts pour ne plus
        # verrouiller une mauvaise première résolution pendant plusieurs jours.
        ttl = {
            "matched": 30 * 24 * 3600,
            "no_cover": 6 * 3600,
            "ambiguous": 30 * 60,
            "not_found": 15 * 60,
            "error": 5 * 60,
        }.get(status, 15 * 60)
        self.storage.cache_put(namespace, key, {"url": url or "", "status": status}, ttl)
        self.diagnostics.add(
            "COVER", "history search", "ok",
            detail={
                "provider": provider,
                "result": status,
                "cache": False,
                "attempts": attempts,
                "candidates": len(all_rows),
                "with_thumbnail": sum(1 for x in all_rows if x.thumbnail_url),
                "score": round(best_score, 1),
                "gap": round(gap, 1),
                "cover_source": cover_source,
            },
            level="detailed",
        )
        return url or default_url, status

    def refresh_bookings(self, force: bool = False) -> list[Booking]:
        key = self._key("bookings")
        if not force:
            cached = self.storage.cache_get("account", key)
            if isinstance(cached, list):
                self.last_bookings = [Booking(**x) for x in cached]
                return self.last_bookings
        session = self._session_for_account()
        if not session.capabilities.bookings:
            self.last_bookings = []
            return []
        rows = session.bookings(self.current_account)
        serial = []
        for row in rows:
            d = asdict(row); d["raw"] = {}
            serial.append(d)
        self.storage.cache_put("account", key, serial, 300)
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
        original = re.sub(r"\s+", " ", str(query or "")).strip()[:180]
        if not original:
            return []
        raw_tokens = re.findall(r"[0-9A-Za-zÀ-ÖØ-öø-ÿŒœ'-]+", original)
        stop = {"a", "au", "aux", "d", "de", "des", "du", "et", "l", "la", "le", "les", "un", "une"}
        tokens = [t for t in raw_tokens if len(cls._history_fold(t)) >= 2 and cls._history_fold(t) not in stop]
        if len(tokens) < 2:
            return [original]

        variants: list[str] = [original]
        if len(tokens) == 2:
            # Longest term first: for "Zola Germinal", "Germinal" is normally
            # the most discriminating fallback.
            variants.extend(sorted(tokens, key=lambda value: (-len(value), tokens.index(value))))
        else:
            midpoint = max(1, len(tokens) // 2)
            variants.append(" ".join(tokens[:midpoint]))
            variants.append(" ".join(tokens[midpoint:]))
            variants.extend(sorted(tokens, key=lambda value: (-len(value), tokens.index(value)))[:2])

        out: list[str] = []
        seen: set[str] = set()
        for value in variants:
            value = re.sub(r"\s+", " ", value).strip()[:180]
            key = cls._history_fold(value)
            if key and key not in seen:
                seen.add(key)
                out.append(value)
        return out[:5]

    @classmethod
    def _search_relevance(cls, query: str, row: SearchResult) -> int:
        tokens = [x for x in cls._history_fold(query).split() if len(x) >= 2]
        if not tokens:
            return 0
        title = cls._history_fold(row.title)
        author = cls._history_fold(row.author)
        combined = f"{title} {author}".strip()
        score = 0
        matched = sum(1 for token in tokens if token in combined)
        score += matched * 20
        score += sum(8 for token in tokens if token in title)
        score += sum(6 for token in tokens if token in author)
        if matched == len(tokens):
            score += 60
        folded_query = cls._history_fold(query)
        if folded_query and folded_query in combined:
            score += 30
        return score

    @staticmethod
    def _normalized_language_values(value: Any) -> list[str]:
        if isinstance(value, dict):
            values: list[Any] = []
            for key in ("code", "id", "value", "name"):
                if key in value:
                    values.append(value[key])
            if not values:
                values.extend(value.values())
        elif isinstance(value, (list, tuple, set)):
            values = list(value)
        else:
            values = [value]
        out: list[str] = []
        for raw in values:
            text = str(raw or "").strip().lower().replace("_", "-")
            if not text:
                continue
            # Keep ISO-ish codes stable while tolerating provider values such as
            # fr-FR. Human language names are left untouched and simply won't
            # match the configured short codes unless a provider normalizes them.
            code = text.split("-", 1)[0]
            if code not in out:
                out.append(code)
        return out

    def _search_preference_score(self, row: SearchResult) -> int:
        identity = self.current_identity
        if identity is None:
            return 0
        metadata = row.metadata if isinstance(row.metadata, dict) else {}
        languages: list[str] = []
        for key in ("language", "languages", "lang", "dc_language", "dc:language"):
            if key in metadata:
                for code in self._normalized_language_values(metadata.get(key)):
                    if code not in languages:
                        languages.append(code)
        prefs = identity.preferences
        score = 0
        for index, preferred in enumerate(prefs.reading_languages):
            code = str(preferred or "").strip().lower().split("-", 1)[0]
            if code and code in languages:
                score += max(1, len(prefs.reading_languages) - index) * 10
                break
        if prefs.prefer_original_language and languages:
            originals: list[str] = []
            for key in ("original_language", "originalLanguage", "language_original"):
                if key in metadata:
                    originals.extend(self._normalized_language_values(metadata.get(key)))
            if originals and any(code in originals for code in languages):
                score += 5
        return score

    def availability_display_label(self, item: SearchResult, detail: ItemDetail) -> str:
        """Translate provider availability into a user-actionable label."""
        if item.source_provider == "kobo":
            if item.owned:
                return "Dans ma bibliothèque Kobo · téléchargeable" if self.can_direct_download(item) else "Dans ma bibliothèque Kobo"
            if item.price:
                return f"Catalogue Kobo · {item.price} {item.currency or ''}".strip()
            return "Catalogue Kobo"
        if self.can_direct_download(item):
            return "Téléchargement direct"
        label = str(detail.availability_label or detail.availability_status or "—").strip()
        status = self._history_fold(detail.availability_status)
        folded_label = self._history_fold(label)
        available = status in {"available", "disponible"} or (
            folded_label.startswith("disponible") and not folded_label.startswith("indisponible")
        )
        if item.source_provider == "paris" and item.medium == "digital" and available:
            return "Disponible à l’emprunt"
        return label or "—"

    def searchable_providers(self) -> list[tuple[str, str]]:
        """Connected provider ids/display names usable by the global search UI."""
        seen: set[str] = set()
        out: list[tuple[str, str]] = []
        for session in self._sessions.values():
            if not session.connected or not session.capabilities.search or session.provider_id in seen:
                continue
            if not self._provider_enabled_for_identity(session.provider_id):
                continue
            seen.add(session.provider_id)
            out.append((session.provider_id, session.descriptor.display_name))
        return out

    def provider_search_capabilities(self) -> dict[str, ProviderSearchCapabilities]:
        """Return declared search limits for connected providers, keyed by provider id."""
        result: dict[str, ProviderSearchCapabilities] = {}
        for session in self._sessions.values():
            if not session.connected or not session.capabilities.search:
                continue
            if not self._provider_enabled_for_identity(session.provider_id):
                continue
            result.setdefault(session.provider_id, session.search_capabilities)
        return result

    @staticmethod
    def _search_contract_warning(session: ProviderSession, field: str, scope: str) -> str:
        contract = session.search_capabilities
        if not contract.supports_field(field):
            return f"{session.descriptor.display_name}: champ de recherche non pris en charge ({field})"
        if not contract.supports_scope(scope):
            return f"{session.descriptor.display_name}: périmètre de recherche non pris en charge ({scope})"
        return ""

    @classmethod
    def _field_match(cls, field: str, query: str, row: SearchResult) -> bool:
        field = str(field or "all").lower()
        if field == "all" or field in {"isbn", "subject"}:
            # ISBN/sujet are forwarded to providers that understand them. The
            # common model does not yet expose normalized values to post-filter.
            return True
        tokens = [x for x in cls._history_fold(query).split() if len(x) >= 2]
        if not tokens:
            return True
        target = cls._history_fold(row.title if field == "title" else row.author if field == "author" else "")
        return all(token in target for token in tokens)

    def search(self, query: str, scope: str = "all", force: bool = False, *, provider_id: str = "all", field: str = "all", content_types: tuple[str, ...] | list[str] | None = None) -> list[SearchResult]:
        provider_scope = "all" if str(scope or "all").casefold() == "unknown" else scope
        # Search is global: one connected session per provider is queried,
        # independently of the reader account currently selected in the GUI.
        searchable: list[ProviderSession] = []
        seen_provider: set[str] = set()
        requested_provider = str(provider_id or "all").strip().lower()
        for session in self._sessions.values():
            if not session.connected or not session.capabilities.search:
                continue
            if not self._provider_enabled_for_identity(session.provider_id):
                continue
            if requested_provider != "all" and session.provider_id != requested_provider:
                continue
            if session.provider_id in seen_provider:
                continue
            seen_provider.add(session.provider_id)
            searchable.append(session)
        provider_key = ",".join(sorted(x.provider_id for x in searchable)) or requested_provider
        identity = self.current_identity
        pref_key = ""
        if identity is not None:
            pref_key = f"{identity.identity_id}:{','.join(identity.preferences.reading_languages)}:{int(identity.preferences.prefer_original_language)}"
        cache_key = hashlib.sha256(f"search-v7\0{provider_key}\0{scope}\0{field}\0{tuple(sorted(content_types or ()))!r}\0{pref_key}\0{query.casefold()}".encode()).hexdigest()
        ttl = int(self.config.get("cache.search_seconds", 21600))
        if not force:
            cached = self.storage.cache_get("search", cache_key)
            if isinstance(cached, list):
                self.last_search = [SearchResult(**x) for x in cached]
                # Never leak a warning from a previous, unrelated search.
                self.last_search_warnings = []
                self.diagnostics.add("CACHE", "search", detail={"hit": True, "results": len(self.last_search), "q_len": len(query), "providers": provider_key})
                return self.last_search

        rows: list[SearchResult] = []
        warnings: list[str] = []
        variants = self._search_query_variants(query)
        for session in searchable:
            provider_rows: list[SearchResult] = []
            provider_seen: set[tuple[str, str, str]] = set()
            contract_warning = self._search_contract_warning(session, field, scope)
            if contract_warning:
                warnings.append(contract_warning)
                continue
            primary_ok = False
            for attempt, variant in enumerate(variants):
                # Loose fallbacks are needed only when the exact query produced
                # no strong title/author match. This keeps normal searches fast.
                if attempt > 0 and primary_ok:
                    break
                try:
                    try:
                        import inspect
                        parameters = inspect.signature(session.search).parameters
                        kwargs = {"field": field}
                        if "content_types" in parameters:
                            kwargs["content_types"] = tuple(content_types or ())
                        session_rows = session.search(variant, provider_scope, **kwargs)
                    except TypeError as exc:
                        # 0.4 keeps source compatibility with small third-party
                        # provider prototypes that still implement the 0.3 signature.
                        if "field" not in str(exc) or "unexpected keyword" not in str(exc):
                            raise
                        session_rows = session.search(variant, provider_scope)
                    for row in session_rows:
                        key = (row.source_provider, row.docbase, row.record_id)
                        if key not in provider_seen:
                            provider_seen.add(key)
                            provider_rows.append(row)
                    concrete = getattr(session, "client", None)
                    partial_failure = False
                    for source in (session, concrete):
                        for warning in list(getattr(source, "last_search_warnings", []) or []):
                            warnings.append(f"{session.descriptor.display_name}: {warning}")
                            partial_failure = True
                    if partial_failure:
                        break
                    if attempt == 0 and provider_rows:
                        primary_ok = max(self._search_relevance(query, row) for row in provider_rows) >= 100
                except Exception as exc:
                    # An exact-query failure should not prevent a looser retry.
                    if attempt == 0 or len(variants) == 1:
                        warnings.append(f"{session.descriptor.display_name}: {exc}")
                    self.diagnostics.add(
                        "PLUGIN", "search", "error",
                        detail={
                            "provider": session.provider_id,
                            "attempt": attempt + 1,
                            "q_len": len(variant),
                            "error": type(exc).__name__,
                            "message": str(exc),
                        },
                    )
                    from .search_failures import is_technical_search_error
                    if is_technical_search_error(exc):
                        break
            if field in {"title", "author"}:
                provider_rows = [row for row in provider_rows if self._field_match(field, query, row)]
            if str(scope or "all").casefold() == "unknown":
                from .core.search import support_kind
                provider_rows = [row for row in provider_rows if support_kind(asdict(row)) == "unknown"]
            rows.extend(provider_rows)

        # Stable de-duplication across plugins; source provider remains visible.
        unique: dict[tuple[str, str, str], SearchResult] = {}
        for row in rows:
            unique[(row.source_provider, row.docbase, row.record_id)] = row
        rows = list(unique.values())
        preference_scores = {id(row): self._search_preference_score(row) for row in rows}
        if len(variants) > 1 or any(preference_scores.values()):
            # Relevance remains authoritative; identity preferences only rank
            # otherwise comparable results and never hide other languages.
            rows.sort(
                key=lambda row: (
                    self._search_relevance(query, row) if len(variants) > 1 else 0,
                    preference_scores.get(id(row), 0),
                ),
                reverse=True,
            )
        warnings = list(dict.fromkeys(warnings))
        try:
            self._mirror_remote_results(rows)
        except Exception as exc:
            _TRACE_LOG.warning("TRACE catalog mirror search error=%s", exc)
        if not warnings:
            self.storage.cache_put("search", cache_key, [asdict(x) for x in rows], ttl)
        self.last_search = rows
        self.last_search_warnings = warnings
        self.diagnostics.add(
            "SEARCH", "federated",
            detail={
                "providers": provider_key,
                "field": field,
                "results": len(rows),
                "variants": len(variants),
                "fallback": len(variants) > 1,
            },
            level="detailed",
        )
        return rows

    def _search_provider_session(self, session: ProviderSession, query: str, scope: str, *, field: str = "all", content_types: tuple[str, ...] = ()) -> tuple[list[SearchResult], list[str], str, str]:
        """Search one provider without mutating shared result state.

        This is deliberately safe to run in parallel with *other* provider sessions;
        a given session is still called sequentially for its own query variants.
        """
        provider_scope = "all" if str(scope or "all").casefold() == "unknown" else scope
        provider_rows: list[SearchResult] = []
        provider_seen: set[tuple[str, str, str]] = set()
        warnings: list[str] = []
        contract_warning = self._search_contract_warning(session, field, scope)
        if contract_warning:
            return [], [contract_warning], "", ""
        variants = self._search_query_variants(query)
        primary_ok = False
        terminal_error_code = terminal_error_message = ""
        for attempt, variant in enumerate(variants):
            if attempt > 0 and primary_ok:
                break
            try:
                try:
                    import inspect
                    parameters = inspect.signature(session.search).parameters
                    kwargs = {"field": field}
                    if "content_types" in parameters:
                        kwargs["content_types"] = tuple(content_types or ())
                    session_rows = session.search(variant, provider_scope, **kwargs)
                except TypeError as exc:
                    if "field" not in str(exc) or "unexpected keyword" not in str(exc):
                        raise
                    session_rows = session.search(variant, provider_scope)
                for row in session_rows:
                    key = (row.source_provider, row.docbase, row.record_id)
                    if key not in provider_seen:
                        provider_seen.add(key); provider_rows.append(row)
                concrete = getattr(session, "client", None)
                partial_failure = False
                for source in (session, concrete):
                    for warning in list(getattr(source, "last_search_warnings", []) or []):
                        warnings.append(f"{session.descriptor.display_name}: {warning}")
                        partial_failure = True
                if partial_failure:
                    break
                if attempt == 0 and provider_rows:
                    primary_ok = max(self._search_relevance(query, row) for row in provider_rows) >= 100
            except Exception as exc:
                if attempt == 0 or len(variants) == 1:
                    terminal_error_code = type(exc).__name__
                    terminal_error_message = str(exc)
                    warnings.append(f"{session.descriptor.display_name}: {exc}")
                self.diagnostics.add(
                    "PLUGIN", "search", "error",
                    detail={"provider": session.provider_id, "attempt": attempt + 1, "q_len": len(variant),
                            "error": type(exc).__name__, "message": str(exc)},
                )
                from .search_failures import is_technical_search_error
                if is_technical_search_error(exc):
                    break
        if field in {"title", "author"}:
            provider_rows = [row for row in provider_rows if self._field_match(field, query, row)]
        if str(scope or "all").casefold() == "unknown":
            from .core.search import support_kind
            provider_rows = [row for row in provider_rows if support_kind(asdict(row)) == "unknown"]
        return provider_rows, list(dict.fromkeys(warnings)), terminal_error_code, terminal_error_message

    def search_progressive(
        self, query: str, scope: str = "all", force: bool = False, *,
        provider_id: str = "all", provider_ids: tuple[str, ...] | list[str] | None = None,
        field: str = "all", content_types: tuple[str, ...] | list[str] | None = None,
        on_batch: Callable[[Any], None] | None = None, control: Any = None,
    ):
        """Parallel federated search with provider-scoped progressive batches.

        Providers keep their simple synchronous ``search`` contract.  Core owns
        concurrency, partial-failure isolation, cancellation checks, mirroring and
        final caching.  ``on_batch`` is invoked from the caller/worker thread, never
        from provider worker threads.
        """
        from datetime import datetime, timezone
        from .plugin_api import CatalogSearchBatch, CatalogSearchSummary

        started_dt = datetime.now(timezone.utc)
        started_mono = time.monotonic()
        with self._search_generation_lock:
            self._search_generation += 1
            search_generation = self._search_generation
        requested = {str(x).strip().lower() for x in (provider_ids or ()) if str(x).strip() and str(x).strip().lower() != "local"}
        single = str(provider_id or "all").strip().lower()
        if single != "all": requested.add(single)
        searchable: list[ProviderSession] = []
        seen_provider: set[str] = set()
        for session in self._sessions.values():
            if not session.connected or not session.capabilities.search:
                continue
            if not self._provider_enabled_for_identity(session.provider_id):
                continue
            if requested and session.provider_id not in requested:
                continue
            if session.provider_id in seen_provider:
                continue
            seen_provider.add(session.provider_id); searchable.append(session)
        searchable.sort(key=lambda row: row.provider_id)
        provider_key = ",".join(sorted(x.provider_id for x in searchable)) or (",".join(sorted(requested)) or single)
        identity = self.current_identity
        pref_key = "" if identity is None else f"{identity.identity_id}:{','.join(identity.preferences.reading_languages)}:{int(identity.preferences.prefer_original_language)}"
        types = tuple(sorted(content_types or ()))
        cache_key = hashlib.sha256(f"search-v8-progressive\0{provider_key}\0{scope}\0{field}\0{types!r}\0{pref_key}\0{query.casefold()}".encode()).hexdigest()
        ttl = int(self.config.get("cache.search_seconds", 21600))

        def emit(batch):
            if on_batch is None: return
            try: on_batch(batch)
            except Exception as exc:
                self.diagnostics.add("SEARCH", "progress callback", "error", detail={"error": type(exc).__name__, "message": str(exc)})

        if not force:
            cached = self.storage.cache_get("search", cache_key)
            if isinstance(cached, list):
                rows = [SearchResult(**x) for x in cached]
                groups: dict[str, list[SearchResult]] = {}
                for row in rows: groups.setdefault(str(row.source_provider or "unknown"), []).append(row)
                for pid in sorted(groups):
                    emit(CatalogSearchBatch(pid, self.provider_display_name(pid), tuple(groups[pid]), cached=True))
                self.last_search = rows; self.last_search_warnings = []
                completed = datetime.now(timezone.utc).isoformat(timespec="seconds")
                return CatalogSearchSummary(query, len(rows), len(groups), len(groups), 0, False, (), started_dt.isoformat(timespec="seconds"), completed)

        aggregate: list[SearchResult] = []
        warnings: list[str] = []
        failed = completed_count = 0
        cancelled = False
        futures = {}
        if searchable:
            for session in searchable:
                futures[_SEARCH_EXECUTOR.submit(self._search_provider_session, session, query, scope, field=field, content_types=tuple(content_types or ()))] = (session, time.monotonic())
            pending = set(futures)
            while pending:
                with self._search_generation_lock:
                    superseded = search_generation != self._search_generation
                if superseded:
                    cancelled = True
                    for future in pending: future.cancel()
                    break
                if control is not None:
                    try:
                        if callable(getattr(control, "cancelled", None)) and control.cancelled():
                            cancelled = True
                        check = getattr(control, "check_cancelled", None)
                        if callable(check): check()
                    except Exception:
                        cancelled = True
                    if cancelled:
                        for future in pending: future.cancel()
                        break
                done, pending = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
                if not done:
                    continue
                for future in done:
                    session, provider_started = futures[future]
                    try:
                        provider_rows, provider_warnings, error_code, error_message = future.result()
                    except Exception as exc:
                        provider_rows, provider_warnings = [], [f"{session.descriptor.display_name}: {exc}"]
                        error_code, error_message = type(exc).__name__, str(exc)
                    aggregate.extend(provider_rows); warnings.extend(provider_warnings)
                    completed_count += 1
                    if error_code and not provider_rows: failed += 1
                    emit(CatalogSearchBatch(
                        session.provider_id, session.descriptor.display_name, tuple(provider_rows), tuple(provider_warnings),
                        error_code, error_message, False, int((time.monotonic() - provider_started) * 1000),
                    ))
                    if control is not None and callable(getattr(control, "report", None)):
                        try: control.report(current=completed_count, total=len(searchable), message=f"Recherche · {session.descriptor.display_name}")
                        except Exception: pass

        unique: dict[tuple[str, str, str], SearchResult] = {}
        for row in aggregate: unique[(row.source_provider, row.docbase, row.record_id)] = row
        rows = list(unique.values())
        variants = self._search_query_variants(query)
        preference_scores = {id(row): self._search_preference_score(row) for row in rows}
        if len(variants) > 1 or any(preference_scores.values()):
            rows.sort(key=lambda row: (self._search_relevance(query, row) if len(variants) > 1 else 0, preference_scores.get(id(row), 0)), reverse=True)
        warnings = list(dict.fromkeys(warnings))
        if not cancelled:
            try: self._mirror_remote_results(rows)
            except Exception as exc: _TRACE_LOG.warning("TRACE catalog mirror progressive search error=%s", exc)
        if not cancelled and not warnings and failed == 0:
            self.storage.cache_put("search", cache_key, [asdict(x) for x in rows], ttl)
        self.last_search = rows; self.last_search_warnings = warnings
        self.diagnostics.add("SEARCH", "progressive federated", detail={"providers": provider_key, "field": field, "results": len(rows), "completed": completed_count, "failed": failed, "cancelled": cancelled, "dt_ms": int((time.monotonic()-started_mono)*1000)}, level="detailed")
        completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return CatalogSearchSummary(query, len(rows), len(searchable), completed_count, failed, cancelled, tuple(warnings), started_dt.isoformat(timespec="seconds"), completed_at)

    def search_local_library(
        self, query: str, scope: str = "all", *, field: str = "all", identity_id: str | None = None,
    ) -> list[dict[str, Any]]:
        from .core.search import LocalLibrarySearch

        resolved_identity = identity_id or (self.current_identity.identity_id if self.current_identity else None)
        rows = LocalLibrarySearch(self.library_entries).search(
            query, identity_id=resolved_identity, scope=scope, field=field,
        )
        _TRACE_LOG.info(
            "TRACE search local identity=%s query_len=%s scope=%s field=%s results=%s",
            resolved_identity, len(str(query or "")), scope, field, len(rows),
        )
        return rows

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
        from .core.search import KnownCatalogSearch

        resolved_identity = identity_id or (self.current_identity.identity_id if self.current_identity else None)
        rows = KnownCatalogSearch(self.catalog_entries, self.library_entries).search(
            query, identity_id=resolved_identity, scope=scope, field=field,
            providers=provider_ids, content_types=content_types,
        )
        _TRACE_LOG.info(
            "TRACE search known identity=%s query_len=%s scope=%s field=%s results=%s",
            resolved_identity, len(str(query or "")), scope, field, len(rows),
        )
        return rows

    @staticmethod
    def filter_loaded_search_results(
        rows: list[dict[str, Any]], query: str = "", *, field: str = "all", scope: str = "all",
        provider_ids: list[str] | tuple[str, ...] | None = None,
        content_types: list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Apply current Search controls without network or cache mutation."""
        from .core.search import filter_loaded_results
        return filter_loaded_results(
            rows, query, providers=provider_ids, content_types=content_types, scope=scope, field=field,
        )

    def _mirror_remote_results(self, rows: list[SearchResult], *, identity_id: str | None = None) -> int:
        """Persist progressively discovered remote catalog metadata (0.8.3)."""
        identity_id = identity_id or (self.current_identity.identity_id if self.current_identity else None)
        if not identity_id:
            return 0
        now = int(time.time())
        keyed: list[tuple[str, SearchResult, str]] = []
        for row in rows:
            state = "remote_owned" if row.owned else ("available_to_buy" if row.source_provider == "kobo" and row.price else ("available_to_borrow" if row.source_provider == "paris" and row.medium == "digital" else "remote_catalog"))
            key = hashlib.sha256(f"mirror\0{identity_id}\0{row.source_provider}\0{row.record_id}".encode()).hexdigest()
            keyed.append((key, row, state))
        existing_by_id = self.storage.catalog_entries_by_ids([key for key, _row, _state in keyed])
        upserts: list[dict[str, Any]] = []
        for key, row, state in keyed:
            existing = existing_by_id.get(key)
            if existing and existing.get("local_state") == "local":
                continue
            raw_meta = dict(row.metadata or {})
            allowed_meta = {
                name: raw_meta.get(name)
                for name in ("subjects", "subject", "series", "collection", "publisher", "languages", "isbn", "isbns", "original_language", "authors", "contributors", "identifiers", "edition", "edition_statement", "series_index", "collection_index", "genre", "genres", "categories", "audience", "target_age", "rights", "dates")
                if raw_meta.get(name) not in (None, "", [], {})
            }
            upserts.append({
                "catalog_entry_id": key, "title": row.title, "author": row.author, "media_format": row.format,
                "identity_id": identity_id, "provider_id": row.source_provider, "item_id": row.record_id, "local_state": state,
                "metadata": {
                    **allowed_meta, "docbase": row.docbase, "scenario": row.scenario, "cover_url": row.thumbnail_url, "product_url": row.friendly_url,
                    "price": row.price, "currency": row.currency, "source_credential": row.source_credential,
                    "source_collection": row.source_collection, "publisher": row.publisher or allowed_meta.get("publisher"),
                    "published_at": row.published_at, "type": row.type, "medium": row.medium,
                    "mirrored_at": now, "description": row.description,
                },
            })
        self.storage.catalog_entries_upsert_many(upserts)
        return len(upserts)

    def mirror_personal_libraries(self) -> dict[str, int]:
        """Refresh enumerable personal libraries and acquire qualified Numilog EPUBs.

        Mirroring remains provider-agnostic.  Automatic acquisition is deliberately
        restricted to providers advertising both a personal library and direct
        downloads; the first provider using this path is Numilog.  Existing valid
        artifacts are checked before calling the provider so a refresh is idempotent.
        """
        result: dict[str, int] = {}
        for session in self._sessions.values():
            if not session.connected or not session.capabilities.personal_library:
                continue
            account = next((a for a in self.accounts if a.provider_id == session.provider_id and a.credential_id == session.credential_id), None)
            if account is not None and not self.sync_source_enabled(account):
                continue
            identity = self.identity_for_connection(session.provider_id, session.credential_id)
            try:
                rows = session.library()
                result[f"{session.provider_id}:{session.credential_id}"] = self._mirror_remote_results(rows, identity_id=identity.identity_id)
                if session.provider_id == "numilog" and session.capabilities.direct_download:
                    for row in rows:
                        self._acquire_personal_library_item(session, row, identity.identity_id)
            except Exception as exc:
                _TRACE_LOG.warning("TRACE catalog mirror error provider=%s credential=%s error=%s", session.provider_id, session.credential_id, exc)
        return result

    def _acquire_personal_library_item(self, session, row, identity_id: str) -> dict[str, Any]:
        """Acquire one owned-library row only when no valid local artifact exists."""
        provider_id = str(session.provider_id)
        item_id = str(getattr(row, "record_id", None) or getattr(row, "id", None) or "")
        if not item_id:
            return {"status": "skipped", "reason": "missing_item_id"}
        existing = [entry for entry in self.storage.catalog_entries()
                    if entry.get("identity_id") == identity_id
                    and entry.get("provider_id") == provider_id
                    and str(entry.get("item_id") or "") == item_id
                    and entry.get("local_state") == "local"]
        for entry in existing:
            path = Path(str((entry.get("metadata") or {}).get("path") or ""))
            if path.is_file() and inspect_artifact(path).valid:
                return {"status": "skipped", "reason": "valid_local", "path": str(path)}
        target_dir = self.library_identity_provider_dir(identity_id, provider_id)
        try:
            payload = asdict(row) if hasattr(row, "__dataclass_fields__") else dict(row)
            path, media_format, metadata = session.download_library_item(payload, target_dir)
            path = Path(path)
            if not path.is_file() or not inspect_artifact(path, media_format or None).valid:
                raise ProviderConfigurationError("Le téléchargement de la bibliothèque n'est pas un artifact valide")
            self.catalog_index_path(path, title=str(getattr(row, "title", "") or path.stem),
                                    item_id=item_id, identity_id=identity_id, provider_id=provider_id)
            _TRACE_LOG.info("TRACE catalog personal-library acquired provider=%s item_id=%s identity=%s path=%s", provider_id, item_id, identity_id, path)
            return {"status": "complete", "path": str(path)}
        except Exception as exc:
            _TRACE_LOG.warning("TRACE catalog personal-library acquisition error provider=%s item_id=%s identity=%s error=%s", provider_id, item_id, identity_id, exc)
            return {"status": "error", "error": str(exc)}

    def recent_acquisitions(self, *, identity_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent local materializations, newest first.

        The indexes below deliberately avoid the historical N+1 pattern where
        every visible placement re-read the full Kobo backup and entitlement
        tables solely to resolve a cover.
        """
        identity_id = identity_id or (self.current_identity.identity_id if self.current_identity else None)
        entries = {str(x.get("placement_id")): x for x in self.storage.catalog_entries() if x.get("placement_id")}
        backup_by_item: dict[str, list[tuple[dict[str, Any], str | None]]] = {}
        for record in self.storage.backup_list("kobo"):
            item_key = str(record.get("item_id") or record.get("product_id") or "")
            if not item_key:
                continue
            try:
                record_identity = self.identity_for_connection("kobo", str(record.get("credential_id") or "")).identity_id
            except Exception:
                record_identity = None
            backup_by_item.setdefault(item_key, []).append((record, record_identity))
        loan_by_key: dict[tuple[str, str, str | None], Loan] = {}
        for loan in self.last_loans:
            try:
                loan_identity = loan.identity_id or self.identity_for_connection(loan.source_provider, loan.source_credential).identity_id
            except Exception:
                loan_identity = loan.identity_id
            loan_by_key[(loan.source_provider, str(loan.record_id or loan.id), loan_identity)] = loan
        entitlements_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for entitlement in self.storage.entitlements():
            entitlements_by_key.setdefault(
                (str(entitlement.get("provider_id") or ""), str(entitlement.get("item_id") or "")), []
            ).append(entitlement)
        rows: list[dict[str, Any]] = []
        for placement in sorted(self.storage.placements(), key=lambda x: int(x.get("updated_at") or 0), reverse=True):
            if placement.get("state") != "present":
                continue
            if identity_id and placement.get("identity_id") not in {identity_id, None, ""}:
                continue
            entry = entries.get(str(placement.get("placement_id"))) or {}
            metadata = dict(entry.get("metadata") or {})
            provider_id = str(placement.get("provider_id") or entry.get("provider_id") or "local")
            row_identity = placement.get("identity_id") or identity_id
            item_id = str(entry.get("item_id") or placement.get("source_item_id") or "")
            thumbnail_url = cached_cover_url(metadata) or metadata.get("cover_url") or metadata.get("thumbnail_url")
            if not thumbnail_url and item_id:
                if provider_id == "kobo":
                    for record, record_identity in backup_by_item.get(item_id, []):
                        if row_identity and record_identity and record_identity != row_identity:
                            continue
                        thumbnail_url = record.get("cover_url") or thumbnail_url
                        if thumbnail_url:
                            break
                if not thumbnail_url:
                    candidates = [
                        loan_by_key.get((provider_id, item_id, row_identity)),
                        loan_by_key.get((provider_id, item_id, None)),
                    ]
                    loan = next((candidate for candidate in candidates if candidate is not None), None)
                    if loan is None:
                        loan = next((
                            candidate for (p, i, _identity), candidate in loan_by_key.items()
                            if p == provider_id and i == item_id and (not row_identity or _identity == row_identity)
                        ), None)
                    if loan is not None:
                        thumbnail_url = loan.thumbnail_url or thumbnail_url
                if not thumbnail_url:
                    for entitlement in entitlements_by_key.get((provider_id, item_id), []):
                        if row_identity and entitlement.get("identity_id") not in {row_identity, None, ""}:
                            continue
                        emeta = dict(entitlement.get("metadata") or {})
                        thumbnail_url = emeta.get("cover_url") or emeta.get("thumbnail_url") or thumbnail_url
                        if thumbnail_url:
                            break
            rows.append({
                "placement_id": placement.get("placement_id"), "artifact_id": placement.get("artifact_id"),
                "title": entry.get("title") or Path(str(placement.get("path") or "")).stem, "author": entry.get("author"),
                "identity_id": row_identity, "provider_id": provider_id, "item_id": item_id or None,
                "path": placement.get("path"), "media_format": entry.get("media_format"), "thumbnail_url": thumbnail_url,
                "metadata": metadata, "updated_at": placement.get("updated_at"),
            })
            if len(rows) >= max(1, limit):
                break
        return rows

    def local_book_path(self, placement_id: str, *, identity_id: str) -> Path:
        """Resolve an inventoried file in the explicit identity scope, no acquisition."""
        if not identity_id or not self.identity_by_id(identity_id):
            raise ProviderConfigurationError('Identité locale inconnue')
        row=self.storage.placement_get(str(placement_id))
        owner=(row or {}).get('identity_id')
        if not owner and self.identities:owner=self.identities[0].identity_id
        if not row or owner!=identity_id or row.get('state')!='present':
            raise ProviderConfigurationError('Fichier local absent ou hors de cette identité')
        path=Path(str(row.get('path') or '')).expanduser().resolve()
        if path.is_dir():
            try:manifest=load_bundle_manifest(path,verify=True)
            except BundleError as exc:raise ProviderConfigurationError('Bundle local invalide') from exc
            if not manifest.playlist:raise ProviderConfigurationError('Ce bundle ne possède pas de fichier à ouvrir')
            target=(path/manifest.playlist).resolve()
            if path not in target.parents:raise ProviderConfigurationError('Playlist locale invalide')
            path=target
        if not path.is_file():raise ProviderConfigurationError('Le fichier local a disparu')
        return path

    def local_placement_for_source(self, *, provider_id, credential_id, item_id, identity_id):
        """Find only an acquisition-attested unique placement, never by title."""
        artifact_ids={str(row.get('artifact_id') or '') for row in self.storage.acquisitions()
                      if row.get('status')=='complete' and row.get('identity_id')==identity_id
                      and row.get('provider_id')==provider_id and row.get('credential_id')==credential_id
                      and str(row.get('item_id') or '')==str(item_id)}
        rows=[row for row in self.storage.placements() if row.get('state')=='present'
              and row.get('identity_id')==identity_id and row.get('artifact_id') in artifact_ids]
        if len(rows)!=1:return None
        try:self.local_book_path(rows[0]['placement_id'],identity_id=identity_id)
        except ProviderConfigurationError:return None
        return rows[0]

    def local_placements_for_sources(self, identity_id):
        """One snapshot for search; only unique acquisition-attested placements."""
        artifacts = {}
        for row in self.storage.acquisitions(status='complete'):
            if row.get('identity_id') != identity_id or not row.get('artifact_id'): continue
            key = (row.get('provider_id'), row.get('credential_id'), str(row.get('item_id') or ''))
            artifacts.setdefault(key, set()).add(row['artifact_id'])
        placements = {}
        for row in self.storage.placements():
            if row.get('identity_id') == identity_id and row.get('state') == 'present':
                placements.setdefault(row.get('artifact_id'), []).append(row)
        linked = {}
        for key, ids in artifacts.items():
            rows = [row for artifact_id in ids for row in placements.get(artifact_id, [])]
            if len(rows) != 1: continue
            try: self.local_book_path(rows[0]['placement_id'], identity_id=identity_id)
            except ProviderConfigurationError: continue
            linked[key] = rows[0]
        return linked

    def materialize_to_identity(self, placement_id: str, target_identity_id: str) -> dict[str, Any]:
        """Materialize an existing Artifact for another Identity without reacquisition.

        0.9.0.1 deliberately performs targeted catalog registration here. A full
        catalog scan after a one-file hardlink/copy made this visible user action
        proportional to the entire library size.
        """
        source = self.storage.placement_get(placement_id)
        if not source or source.get("state") != "present":
            raise ProviderConfigurationError("Placement source local introuvable")
        src = Path(str(source.get("path") or ""))
        if not src.is_file() and not src.is_dir():
            raise ProviderConfigurationError("Artifact source introuvable")
        is_bundle = src.is_dir()
        source_bundle = None
        if is_bundle:
            try:
                source_bundle = load_bundle_manifest(src, verify=True)
            except BundleError as exc:
                raise ProviderConfigurationError(f"Bundle source invalide: {exc}") from exc
        target_identity = self.identity_by_id(target_identity_id)
        if target_identity is None:
            raise ProviderConfigurationError(f"Identité cible inconnue: {target_identity_id}")
        provider = str(source.get("provider_id") or "shared")
        source_entries = [
            row for row in self.storage.catalog_entries()
            if str(row.get("placement_id") or "") == str(placement_id)
        ]
        source_entry = source_entries[0] if source_entries else {}
        dest_dir = self.library_identity_provider_dir(target_identity_id, provider)
        dest = dest_dir / src.name
        unchanged = False
        if dest.exists():
            if is_bundle and dest.is_dir():
                try:
                    unchanged = load_bundle_manifest(dest, verify=True).bundle_sha256 == source_bundle.bundle_sha256
                except BundleError:
                    unchanged = False
            elif not is_bundle and dest.is_file():
                unchanged = sha256_file(dest) == sha256_file(src)
            if not unchanged:
                stem, suffix = dest.stem, dest.suffix; n = 2
                while dest.exists():
                    dest = dest_dir / f"{stem} ({n}){suffix}"; n += 1
        if not unchanged:
            if is_bundle:
                shutil.copytree(src, dest)
            else:
                try:
                    os.link(src, dest)
                except OSError:
                    shutil.copy2(src, dest)

        artifact_id = str(source.get("artifact_id") or "").strip()
        item_id = str(source.get("source_item_id") or source_entry.get("item_id") or "").strip() or None
        if artifact_id:
            stat_target = (dest / ".cifereca-bundle.json") if is_bundle else dest
            stat = stat_target.stat()
            try:
                relative = str(dest.resolve().relative_to(self.library_dir().resolve()))
            except ValueError:
                relative = dest.name
            placement_meta = {
                "relative_path": relative, "mtime_ns": int(stat.st_mtime_ns),
                "materialized_from": placement_id, "artifact_kind": "bundle" if is_bundle else "file",
            }
            if is_bundle:
                placement_meta["manifest_size"] = int(stat.st_size)
            else:
                placement_meta["file_size"] = int(stat.st_size)
            new_placement_id = self.storage.placement_put({
                "artifact_id": artifact_id, "path": str(dest), "identity_id": target_identity_id,
                "provider_id": provider, "source_item_id": item_id, "state": "present",
                "last_seen_at": int(time.time()), "metadata": placement_meta,
            })
            metadata = dict(source_entry.get("metadata") or {})
            metadata["path"] = str(dest.resolve())
            metadata["materialized_from"] = placement_id
            self.storage.catalog_entry_put({
                "artifact_id": artifact_id, "placement_id": new_placement_id,
                "title": source_entry.get("title") or src.stem, "author": source_entry.get("author"),
                "media_format": source_entry.get("media_format") or src.suffix.lstrip(".").lower(),
                "identity_id": target_identity_id, "provider_id": provider, "item_id": item_id,
                "local_state": "local", "metadata": metadata,
            })
        else:
            self.catalog_index_path(
                dest, title=str(source_entry.get("title") or src.stem), item_id=item_id,
                identity_id=target_identity_id, provider_id=provider,
            )
        return {
            "status": "unchanged" if unchanged else "complete", "path": str(dest),
            "target_identity_id": target_identity_id, "provider_id": provider,
        }

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

    def return_digital(self, loan: Loan) -> dict[str, Any]:
        account = self._account_for_loan(loan)
        session = self._session_for_source(loan.source_provider, loan.source_credential)
        result = session.return_digital(loan, account)
        self.storage.cache_delete_namespace("account")
        return result

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
        reference = self.storage.adept_profile_binding_get("identity", identity_id)
        if reference:
            return reference
        try:
            rows = [r for r in self.kobo_profiles() if self._adept_profile_name(r) == profile_name]
        except Exception:
            return None
        return self._adept_profile_ref(rows[0]) if len(rows) == 1 else None

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
        if not staged.exists() or sha256_file(staged) != source_sha:
            shutil.copy2(source, staged)
        self.storage.acquisition_put({
            "acquisition_id": acquisition_id, "provider_id": provider_id, "credential_id": "manual",
            "identity_id": identity.identity_id, "item_id": source_sha, "title": title or source.stem,
            "status": "acquiring", "strategy": "manual_acsm", "executor": "drm.ade",
            "metadata": {"source_acsm_sha256": source_sha, "staging": str(staged)},
        })
        try:
            media_format = detect_acsm_format(staged) or "epub"
            suffix = ".pdf" if media_format == "pdf" else ".epub"
            destination = self.library_identity_provider_dir(identity.identity_id, provider_id) / self._loan_destination_name(title or source.stem, suffix)
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
            if produced.resolve() != destination.resolve():
                raise ProviderConfigurationError("Chemin d’artefact ADEPT inattendu")
            if not produced.is_file():
                raise ProviderConfigurationError("Le runtime DRM n'a produit aucun Artifact")
            inspected = inspect_artifact(produced)
            if not inspected.valid or inspected.media_format not in {"epub", "pdf"}:
                raise ProviderConfigurationError(f"Artifact ACSM invalide: {inspected.reason or 'format'}")
            expected_suffix = ".pdf" if inspected.media_format == "pdf" else ".epub"
            if produced.suffix.casefold() != expected_suffix:
                corrected = produced.with_suffix(expected_suffix)
                corrected.unlink(missing_ok=True)
                produced.replace(corrected)
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
            result = self._import_acsm_impl(source, identity_id=identity_id, provider_id=provider_id, title=title)
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
        return str(row.get("profile_id") or row.get("id") or row.get("name") or row.get("profile") or "").strip()

    @staticmethod
    def _adept_profile_name(row: dict[str, Any]) -> str:
        return str(row.get("name") or row.get("profile") or row.get("profile_id") or row.get("id") or "").strip()

    def _adept_profile_row(self, reference: str | None, profiles: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        ref = str(reference or "").strip()
        if not ref:
            return None
        rows = profiles if profiles is not None else self.kobo_profiles()
        matches = [row for row in rows if ref in {str(row.get(key) or "").strip() for key in ("profile_id", "id", "name", "profile")}]
        return matches[0] if len(matches) == 1 else None

    def _adept_profile_cli_name(self, reference: str | None, profiles: list[dict[str, Any]] | None = None) -> str | None:
        ref = str(reference or "").strip()
        if not ref:
            return None
        row = self._adept_profile_row(ref, profiles)
        return self._adept_profile_name(row) if row else ref

    def can_direct_download(self, item: SearchResult) -> bool:
        """Whether Paris can execute an acquisition with the published contracts."""
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
        inspection = inspect_artifact(path)
        if not inspection.valid or not inspection.media_format or inspection.protected is True:
            raise ProviderConfigurationError("Artefact de sauvegarde invalide ou encore protégé")
        return {"format": inspection.media_format.upper(), "size": path.stat().st_size, "sha256": sha256_file(path)}

    @staticmethod
    def _kobo_artifact_plausible(path: Path, fmt: str) -> bool:
        """Perform a conservative local sanity check without interpreting DRM."""
        try:
            if not path.is_file() or path.stat().st_size < 4:
                return False
            with path.open("rb") as handle:
                head = handle.read(8)
        except OSError:
            return False
        if str(fmt).lower() == "pdf":
            return head.startswith(b"%PDF-")
        return head.startswith(b"PK\x03\x04")

    def _kobo_record_candidate_paths(self, record: dict[str, Any], target_dir: Path) -> list[Path]:
        fmt = "pdf" if str(record.get("format") or "").lower() == "pdf" else "epub"
        ext = ".pdf" if fmt == "pdf" else ".epub"
        author = str(record.get("author") or "").strip()
        title = str(record.get("title") or record.get("item_id") or "Kobo").strip()
        stem = f"{author} - {title}" if author else title
        safe = self.acquisition._safe_filename(stem or str(record.get("item_id") or "kobo"))
        base = target_dir / f"{safe}{ext}"
        suffix = re.sub(r"[^A-Za-z0-9]", "", str(record.get("item_id") or ""))[-8:] or "kobo"
        suffixed = target_dir / f"{safe} [{suffix}]{ext}"
        paths: list[Path] = []
        for path in (base, suffixed):
            if path not in paths and self._kobo_artifact_plausible(path, fmt):
                paths.append(path)
        return paths

    def _kobo_destination(self, item: SearchResult, target_dir: Path, fmt: str) -> Path:
        ext = ".pdf" if fmt.lower() == "pdf" else ".epub"
        author = (item.author or "").strip()
        stem = f"{author} - {item.title}" if author else item.title
        safe = self.acquisition._safe_filename(stem or item.record_id)
        path = target_dir / f"{safe}{ext}"
        # Avoid an unrelated existing file blocking a different Kobo product.
        if path.exists():
            current = self.storage.backup_get("kobo", item.source_credential, item.record_id)
            if not current or Path(str(current.get("local_path") or "")) != path:
                suffix = re.sub(r"[^A-Za-z0-9]", "", item.record_id)[-8:] or "kobo"
                path = target_dir / f"{safe} [{suffix}]{ext}"
        return path

    def _execute_kobo_acquisition(self, item: SearchResult, target_dir: Path) -> Acquisition:
        """Compatibility façade; Kobo execution lives in the provider adapter."""
        return execute_kobo_acquisition(self, item, target_dir)

    def _acquire_direct_item_impl(self, item: SearchResult) -> Acquisition:
        session = self._session_for_source(item.source_provider, item.source_credential)
        content_type = self.acquisition.content_type_for(item)

        if item.source_provider == "kobo":
            if not item.owned:
                raise ProviderConfigurationError("Kobo : ce titre n'est pas possédé par le compte")
            if item.downloadable is False:
                raise ProviderConfigurationError("Kobo : ce titre n'est pas marqué téléchargeable")
            identity = self.identity_for_connection(item.source_provider, item.source_credential)
            return self._execute_kobo_acquisition(item, self._kobo_backup_target_dir(identity.identity_id))

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

    def acquire_direct_item(self, item: SearchResult) -> Acquisition:
        identity_id = None
        try:
            identity_id = self.identity_for_connection(item.source_provider, item.source_credential).identity_id
        except Exception:
            identity_id = self.current_identity.identity_id if self.current_identity else None
        try:
            result = self._acquire_direct_item_impl(item)
            self._record_action(identity_id, "acquisition", "success", provider_id=item.source_provider, facts={"record_id": item.record_id})
            return result
        except Exception as exc:
            self._record_action(identity_id, "acquisition", "failed", provider_id=item.source_provider, facts={"record_id": item.record_id, "error": type(exc).__name__})
            raise

    def obtain_search_item(self, item: SearchResult):
        from .core.materialization import MaterializationCoordinator

        return MaterializationCoordinator(
            acquire=self.acquire_direct_item,
            index_path=self.catalog_index_path,
            resolve_identity=self.identity_for_connection,
        ).obtain(item)

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
        result = LocalCatalog(self.storage, self.library_dir()).scan(repair_misnamed=repair_misnamed, force_covers=force_covers)
        self.diagnostics.add(
            "CATALOG", "local scan", "ok" if not result.invalid else "warning",
            detail=result.as_dict(),
        )
        return {**result.as_dict(), "storage_schema_version": self.storage.schema_version()}

    def catalog_entries(self, *, state: str | None = None) -> list[dict[str, Any]]:
        return LocalCatalog(self.storage, self.library_dir()).entries(state=state)

    def catalog_index_path(
        self, path: Path, *, title: str | None = None, item_id: str | None = None,
        identity_id: str | None = None, provider_id: str | None = None,
    ) -> dict[str, Any]:
        return LocalCatalog(self.storage, self.library_dir()).index_path(
            path, title=title, item_id=item_id, identity_id=identity_id, provider_id=provider_id,
        )

    def library_entries(self, *, identity_id: str | None = None, scan: bool = False) -> list[dict[str, Any]]:
        """Canonical Library projection.

        Local rows are grouped by content identity (SHA-256/Artifact) rather than
        by provider/item. Remote-only rights remain distinct until reconciliation
        can bind them to a local Artifact.
        """
        _lib_started = time.monotonic()
        identity_id = identity_id or (self.current_identity.identity_id if self.current_identity else None)
        _TRACE_LOG.info("TRACE library-entries begin identity=%s filesystem_scan=%s", identity_id, "requested" if scan else "skipped")
        if scan:
            self.catalog_scan()
        merged: dict[str, dict[str, Any]] = {}
        source_alias: dict[tuple[str, str], str] = {}
        default_identity_id = self.identities[0].identity_id if self.identities else None
        artifacts = {str(row.get("artifact_id") or ""): row for row in self.storage.artifacts()}
        placements_by_artifact: dict[str, list[dict[str, Any]]] = {}
        for placement in self.storage.placements(state="present"):
            placements_by_artifact.setdefault(str(placement.get("artifact_id") or ""), []).append(placement)

        def placement_view(row: dict[str, Any]) -> dict[str, Any]:
            meta = dict(row.get("metadata") or {})
            return {
                "placement_id": str(row.get("placement_id") or ""),
                "identity_id": row.get("identity_id"),
                "provider_id": str(row.get("provider_id") or "local"),
                "source_item_id": str(row.get("source_item_id") or ""),
                "kind": str(meta.get("placement_kind") or ("storage" if meta.get("storage_target_id") else "library")),
                "storage_target_id": meta.get("storage_target_id"),
                "storage_plugin_id": meta.get("storage_plugin_id"),
                "filename": Path(str(row.get("path") or "")).name,
                "state": str(row.get("state") or "present"),
            }

        def add_unique(values: list[Any], value: Any) -> None:
            if value not in {None, ""} and value not in values:
                values.append(value)

        for row in self.storage.catalog_entries():
            if row.get("local_state") != "local":
                continue
            row_identity = row.get("identity_id")
            if identity_id and row_identity != identity_id:
                if row_identity not in {None, ""} or identity_id != default_identity_id:
                    continue
            provider_id = str(row.get("provider_id") or "local")
            artifact_id = str(row.get("artifact_id") or "")
            item_id = str(row.get("item_id") or artifact_id or row.get("catalog_entry_id"))
            artifact = artifacts.get(artifact_id, {})
            sha256 = str(artifact.get("sha256") or "")
            if not sha256 and artifact_id.startswith("sha256:"):
                sha256 = artifact_id.split(":", 1)[1]
            canonical = f"sha256:{sha256}" if sha256 else (artifact_id or f"source:{provider_id}:{item_id}")
            key = f"artifact:{canonical}" if not canonical.startswith("source:") else canonical
            source_alias[(provider_id, item_id)] = key
            metadata = dict(row.get("metadata") or {})
            all_placements = [placement_view(p) for p in placements_by_artifact.get(artifact_id, ())]
            current_placement = next((p for p in placements_by_artifact.get(artifact_id, ()) if str(p.get("placement_id") or "") == str(row.get("placement_id") or "")), None)
            existing = merged.get(key)
            source_ref = {"provider_id": provider_id, "item_id": item_id, "identity_id": row_identity or default_identity_id}
            if existing is None:
                existing = {
                    "key": key, "book_id": item_id, "title": str(row.get("title") or "Sans titre"),
                    "author": row.get("author"), "provider_id": provider_id, "providers": [provider_id],
                    "identity_id": row_identity or default_identity_id, "identity_ids": [row_identity or default_identity_id] if (row_identity or default_identity_id) else [],
                    "item_id": item_id, "source_refs": [source_ref], "state": "local", "local": True,
                    "local_path": metadata.get("path") or (current_placement or {}).get("path"),
                    "media_format": artifact.get("media_format") or row.get("media_format"),
                    "artifact_kind": metadata.get("artifact_kind") or artifact.get("kind") or "file",
                    "bundle_members": metadata.get("bundle_members") or [], "playlist": metadata.get("playlist"),
                    "entitlement_type": None, "due_at": None, "source": "catalog",
                    "thumbnail_url": cached_cover_url(metadata) or metadata.get("cover_url") or metadata.get("thumbnail_url"),
                    "artifact_id": artifact_id or None, "sha256": sha256 or None,
                    "placement_id": row.get("placement_id"), "placements": all_placements, "possessions": [],
                    "metadata": metadata,
                }
                merged[key] = existing
            else:
                add_unique(existing.setdefault("providers", []), provider_id)
                add_unique(existing.setdefault("identity_ids", []), row_identity or default_identity_id)
                if source_ref not in existing.setdefault("source_refs", []): existing["source_refs"].append(source_ref)
                known_ids={p.get("placement_id") for p in existing.setdefault("placements", [])}
                existing["placements"].extend(p for p in all_placements if p.get("placement_id") not in known_ids)
                if not existing.get("author") and row.get("author"): existing["author"] = row.get("author")
                if existing.get("title") in {None, "", "Sans titre"} and row.get("title"): existing["title"] = row.get("title")
                if not existing.get("thumbnail_url"):
                    existing["thumbnail_url"] = cached_cover_url(metadata) or metadata.get("cover_url") or metadata.get("thumbnail_url")
                existing["metadata"] = {**metadata, **dict(existing.get("metadata") or {})}

        backup_by_key = {(str(row.get("credential_id") or ""), str(row.get("item_id") or "")): row for row in self.storage.backup_list("kobo")}
        loans_by_key: dict[tuple[str, str, str | None, str], Loan] = {}
        for loan in self.last_loans:
            loans_by_key[(loan.source_provider, loan.source_credential, loan.source_account_id, str(loan.record_id or loan.id))] = loan

        for entitlement in self.storage.entitlements(state="active"):
            entitlement_type = str(entitlement.get("entitlement_type") or "unknown")
            if entitlement_type not in {"owned", "loan", "license"}: continue
            entitlement_identity = str(entitlement.get("identity_id") or "").strip() or default_identity_id
            if identity_id and entitlement_identity != identity_id: continue
            provider_id = str(entitlement.get("provider_id") or ""); item_id = str(entitlement.get("item_id") or "")
            if not provider_id or not item_id: continue
            source_key=(provider_id,item_id); canonical_key=source_alias.get(source_key, f"source:{provider_id}:{item_id}")
            existing = merged.get(canonical_key, {})
            meta = dict(entitlement.get("metadata") or {})
            backup = backup_by_key.get((str(entitlement.get("credential_id") or ""), item_id), {}) if provider_id == "kobo" else {}
            loan = loans_by_key.get((provider_id, str(entitlement.get("credential_id") or ""), entitlement.get("account_id"), item_id))
            loan_extra = dict((loan.raw or {}).get("_cifereca") or {}) if loan is not None and isinstance((loan.raw or {}).get("_cifereca"), dict) else {}
            title = str(meta.get("title") or backup.get("title") or (loan.title if loan else "") or existing.get("title") or item_id)
            author = meta.get("author") or backup.get("author") or (loan.author if loan else None) or loan_extra.get("author") or existing.get("author")
            from .core.search import file_format
            source_media_format = meta.get("media_format") or backup.get("format") or (loan.media_format if loan else None) or loan_extra.get("format")
            local_format = file_format(existing.get("media_format")) if existing.get("local") else ""
            media_format = local_format or file_format(source_media_format) or file_format(existing.get("media_format"))
            meta["source_media_format"] = source_media_format
            thumbnail = cached_cover_url(existing.get("metadata") or {}) or meta.get("cover_url") or backup.get("cover_url") or (loan.thumbnail_url if loan else None) or existing.get("thumbnail_url")
            remote_state = "remote_owned" if entitlement_type == "owned" else "remote_loan_active" if entitlement_type == "loan" else "remote_entitled"
            possession={"identity_id": entitlement_identity, "provider_id": provider_id, "item_id": item_id, "kind": entitlement_type, "account_id": entitlement.get("account_id"), "entitlement_id": entitlement.get("entitlement_id"), "expires_at": entitlement.get("expires_at"), "state": entitlement.get("state") or "active"}
            if existing:
                add_unique(existing.setdefault("providers", []), provider_id)
                add_unique(existing.setdefault("identity_ids", []), entitlement_identity)
                if possession not in existing.setdefault("possessions", []): existing["possessions"].append(possession)
                source_ref={"provider_id":provider_id,"item_id":item_id,"identity_id":entitlement_identity}
                if source_ref not in existing.setdefault("source_refs", []): existing["source_refs"].append(source_ref)
                existing.update({"title":title,"author":author,"state":"local" if existing.get("local") else remote_state,"media_format":str(media_format or "").lower() or None,"entitlement_type":entitlement_type,"entitlement_id":entitlement.get("entitlement_id"),"due_at":entitlement.get("expires_at"),"loan_id":entitlement.get("loan_id"),"thumbnail_url":thumbnail,"metadata":{**dict(existing.get("metadata") or {}),**meta}})
            else:
                merged[canonical_key]={"key":canonical_key,"book_id":item_id,"title":title,"author":author,"provider_id":provider_id,"providers":[provider_id],"identity_id":entitlement_identity,"identity_ids":[entitlement_identity] if entitlement_identity else [],"item_id":item_id,"source_refs":[{"provider_id":provider_id,"item_id":item_id,"identity_id":entitlement_identity}],"state":remote_state,"local":False,"local_path":None,"media_format":str(media_format or "").lower() or None,"artifact_kind":None,"bundle_members":[],"playlist":None,"entitlement_type":entitlement_type,"entitlement_id":entitlement.get("entitlement_id"),"due_at":entitlement.get("expires_at"),"source":"entitlement","loan_id":entitlement.get("loan_id"),"thumbnail_url":thumbnail,"artifact_id":None,"sha256":None,"placement_id":None,"placements":[],"possessions":[possession],"metadata":meta}

        result=[]
        for row in merged.values():
            row["providers"] = sorted(dict.fromkeys(str(x) for x in row.get("providers", []) if x))
            row["identity_ids"] = list(dict.fromkeys(x for x in row.get("identity_ids", []) if x))
            row["placement_count"] = len(row.get("placements") or [])
            row["source_count"] = len(row.get("source_refs") or [])
            result.append(row)
        result.sort(key=lambda row: (str(row.get("title") or "").casefold(), str(row.get("provider_id") or "")))
        _TRACE_LOG.info("TRACE library-entries end identity=%s count=%s dt=%.3fs", identity_id, len(result), time.monotonic()-_lib_started)
        return result

    def _persist_active_loans(self, identity_id: str, loans: list[Loan], *, reconcile_missing: bool = True) -> dict[str, int]:
        """Persist current digital loans and realize rights no longer reported."""
        active_ids: set[str] = set()
        persisted = 0
        for loan in loans:
            if loan.medium != "digital":
                continue
            source = SourceIdentity(
                provider_id=loan.source_provider,
                credential_id=loan.source_credential,
                account_id=loan.source_account_id,
                identity_id=identity_id,
            )
            entitlement = Entitlement(
                source=source,
                item_id=str(loan.record_id or loan.id),
                entitlement_type="loan",
                state="active",
                loan_id=loan.id,
                expires_at=loan.due_at,
                metadata={
                    "title": loan.title,
                    "author": loan.author or (dict((loan.raw or {}).get("_cifereca") or {}).get("author") if isinstance((loan.raw or {}).get("_cifereca"), dict) else None),
                    "media_format": loan.media_format or (dict((loan.raw or {}).get("_cifereca") or {}).get("format") if isinstance((loan.raw or {}).get("_cifereca"), dict) else None),
                    "cover_url": loan.thumbnail_url,
                    "ebook_url": loan.ebook_url,
                    "document_type": loan.document_type,
                },
            )
            active_ids.add(entitlement.entitlement_id)
            self.storage.entitlement_put(entitlement)
            persisted += 1
        returned = self.storage.entitlement_realize_missing_loans(active_ids, identity_id=identity_id) if reconcile_missing else 0
        expired = self.storage.entitlement_realize_expired()
        return {"persisted": persisted, "returned_realized": returned, "expired_realized": expired}

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
        identity = self.identity_for_connection(loan.source_provider, loan.source_credential)
        entitlements = {r["entitlement_id"] for r in self.storage.entitlements(provider_id=loan.source_provider)
                        if r.get("identity_id") == identity.identity_id and r.get("loan_id") == loan.id
                        and r.get("credential_id") == loan.source_credential
                        and str(r.get("account_id") or "") == str(loan.source_account_id or "")}
        for row in self.storage.acquisitions(provider_id=loan.source_provider, status="complete"):
            if (row.get("identity_id") != identity.identity_id or row.get("credential_id") != loan.source_credential
                    or str(row.get("account_id") or "") != str(loan.source_account_id or "")
                    or str(row.get("item_id") or "") != str(loan.record_id or loan.id)):
                continue
            metadata = row.get("metadata") or {}
            if row.get("entitlement_id") not in entitlements and metadata.get("provider_loan_id") != loan.id:
                continue
            binding = metadata.get("adept_binding") or {}
            state = "returned" if binding.get("loan_state") == "returned" else ("loan_id_missing" if not binding.get("adept_loan_id") else ("profile_missing" if not binding.get("adept_profile_id") else "ready"))
            if state == "returned" and (binding.get("return_verification") != "confirmed" or binding.get("token_present") is not False):
                state = "returned_unverified"
            if binding.get("return_verification") in {"pending", "verification_failed"}:
                state = "verification_failed"
            elif binding.get("loan_state") == "expired":
                state = "expired"
            elif binding.get("inventory_status") in {"unresolved", "ambiguous", "inventory_error", "binding_conflict"} and state != "returned":
                state = binding["inventory_status"]
            elif binding.get("token_present") is False and state != "returned":
                state = "token_missing"
            if state in {'returned', 'returned_unverified'} and self._provider_loan_active_for_acquisition(row):
                state = 'provider_conflict'
            return {"status": state, "acquisition_id": row["acquisition_id"],
                    "adept_loan_id": binding.get("adept_loan_id"), "profile_name": binding.get("adept_profile_name"),
                    "return_available": bool(state == "ready" and binding.get("return_available") and self._adept_return_capability())}
        return {"status": "no_acquisition", "return_available": False}

    def reacquire_digital_loan(self, loan: Loan) -> dict[str, Any]:
        """Explicit full recovery, serialized with automatic acquisitions.

        Preserve a usable previous acquisition/binding on failed re-acquisition.
        A successful re-acquisition publishes a distinct file if one exists.
        """
        if not self.can_download_loan(loan):
            raise ProviderConfigurationError("Ce prêt ne permet pas une acquisition numérique")
        try:
            with InterProcessLock(acquisition_lock_file()):
                identity = self.identity_for_connection(loan.source_provider, loan.source_credential)
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

    def _acquire_active_loan_automatically(self, loan: Loan, *, force_refresh: bool = False) -> dict[str, Any]:
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
        identity = self.identity_for_connection(loan.source_provider, loan.source_credential)
        _TRACE_LOG.info("TRACE download identity resolved loan_id=%s identity=%s", loan.id, identity.identity_id)
        entitlement_rows = [
            row for row in self.storage.entitlements(provider_id=loan.source_provider, state="active")
            if row.get("loan_id") == loan.id and row.get("identity_id") == identity.identity_id
        ]
        entitlement_id = entitlement_rows[0]["entitlement_id"] if entitlement_rows else None
        existing = [
            row for row in self.storage.catalog_entries()
            if row.get("identity_id") == identity.identity_id
            and row.get("provider_id") == loan.source_provider
            and str(row.get("item_id") or "") == str(loan.record_id or loan.id)
            and row.get("local_state") == "local"
        ]
        # Inventory is a projection. Validate its files before deciding to skip.
        valid_existing = []
        for entry in existing:
            path_text = str((entry.get("metadata") or {}).get("path") or "")
            path = Path(path_text) if path_text else None
            if path is not None and path.is_file():
                inspection = inspect_artifact(path)
                if inspection.valid and inspection.protected is not True:
                    valid_existing.append(entry)
                    continue
            self.storage.catalog_entry_put({**entry, "local_state": "missing" if path is None or not path.is_file() else "invalid"})
            _TRACE_LOG.info("TRACE download local-recheck provider_loan_id=%s catalog_state=%s", loan.id, "missing" if path is None or not path.is_file() else "invalid")
        existing = valid_existing
        previous_rows = [
            row for row in self.storage.acquisitions(provider_id=loan.source_provider)
            if row.get("identity_id") == identity.identity_id
            and row.get("credential_id") == loan.source_credential
            and str(row.get("account_id") or "") == str(loan.source_account_id or "")
            and str(row.get("item_id") or "") == str(loan.record_id or loan.id)
            and row.get("strategy") == "loan_auto"
        ]
        previous = previous_rows[0] if previous_rows else None
        previous_meta = dict((previous or {}).get("metadata") or {})
        previous_path = Path(str(previous_meta.get("path") or "")) if previous_meta.get("path") else None
        previous_inspection = None
        if previous_path is not None and previous_path.is_file():
            expected_previous = "pdf" if previous_path.suffix.casefold() == ".pdf" else "epub" if previous_path.suffix.casefold() == ".epub" else None
            previous_inspection = inspect_artifact(previous_path, expected_previous)
        legacy_acsm_artifact = bool(
            loan.source_provider == "paris"
            and (previous or {}).get("executor") == "kobo-adept"
            and previous_path is not None
            and previous_path.is_file()
            and (
                not previous_meta.get("post_processed")
                or previous_inspection is None
                or not previous_inspection.valid
                or previous_inspection.protected is True
            )
        )
        # A completed ledger row with a validated local artifact is sufficient
        # proof that this loan has already been materialized. Do not depend on
        # catalog item_id alone: older/full scans may leave the catalog
        # projection incomplete even though the canonical acquisition is valid.
        previous_complete_local = bool(
            previous
            and previous.get("status") == "complete"
            and previous_path is not None
            and previous_path.is_file()
            and previous_inspection is not None
            and previous_inspection.valid
            and previous_inspection.protected is not True
            and (
                (previous or {}).get("executor") != "kobo-adept"
                or bool(previous_meta.get("post_processed"))
            )
        )
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
        if (existing or previous_complete_local) and not legacy_acsm_artifact and not force_refresh:
            _TRACE_LOG.info("TRACE download loan_auto skip-local loan_id=%s identity=%s source=%s dt=%.3fs", loan.id, identity.identity_id, "ledger" if previous_complete_local else "catalog", time.monotonic()-started)
            return {"status": "unchanged", "loan_id": loan.id}
        if legacy_acsm_artifact:
            _TRACE_LOG.info(
                "TRACE download loan_auto repair-legacy loan_id=%s reason=%s", loan.id,
                "artifact_validation" if previous_meta.get("post_processed") else "missing_post_process_marker"
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
        self.storage.acquisition_put({**ledger_base, "status": "acquiring", "strategy": "loan_auto", "executor": "provider"})
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
                try:
                    profile_name, profile_source = self.resolve_adept_profile_for_identity(identity.identity_id)
                except AdeptProfileResolutionError as exc:
                    self.storage.acquisition_put({
                        **ledger_base, "status": "attention_required", "strategy": "loan_auto", "executor": "kobo-adept",
                        "attention_code": exc.code, "error_message": str(exc),
                    })
                    _TRACE_LOG.info(
                        "TRACE download acsm cleanup loan_id=%s action=keep reason=%s file=%s",
                        loan.id, exc.code, retained_acsm,
                    )
                    return {"status": "attention_required", "loan_id": loan.id, "code": exc.code, "error": str(exc)}
                _TRACE_LOG.info(
                    "TRACE download adept profile-resolved loan_id=%s identity=%s profile=%s source=%s",
                    loan.id, identity.identity_id, profile_name, profile_source,
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
                    provisioning = profile_row.get("provisioning") if isinstance(profile_row.get("provisioning"), dict) else {}
                    readiness = provisioning.get("acsm_fetch_ready") if "acsm_fetch_ready" in provisioning else profile_row.get("fetch_capable")
                    if readiness is False:
                        code = "adept_profile_acsm_not_ready"
                        message = "Le profil Kobo-Adept lié à cette identité n'est pas prêt pour les acquisitions ACSM"
                        self.storage.acquisition_put({
                            **ledger_base, "status": "attention_required", "strategy": "loan_auto", "executor": "kobo-adept",
                            "attention_code": code, "error_message": message,
                        })
                        _TRACE_LOG.info(
                            "TRACE download acsm cleanup loan_id=%s action=keep reason=%s profile=%s file=%s",
                            loan.id, code, profile_name, retained_acsm,
                        )
                        return {"status": "attention_required", "loan_id": loan.id, "code": code, "error": message}

                acsm_format = detect_acsm_format(acquisition.acsm_path)
                if acsm_format is None:
                    # Contract v8 requires an exact caller-owned destination.
                    # Retain the historical EPUB fallback only when the ACSM
                    # itself provides no usable format metadata.
                    acsm_format = "epub"
                    format_source = "fallback"
                else:
                    format_source = "acsm"
                suffix = ".pdf" if acsm_format == "pdf" else ".epub"
                destination = self.library_identity_provider_dir(identity.identity_id, loan.source_provider) / self._loan_destination_name(loan.title, suffix)
                repair_mode = bool(not force_refresh and legacy_acsm_artifact and previous_path is not None and previous_path.is_file())
                # Explicit re-acquisition never replaces a preserved local file.
                # A title collision is not evidence that a file belongs to this loan.
                if not repair_mode and destination.exists():
                    destination = destination.with_name(destination.stem + ".reacquired-" + uuid.uuid4().hex + destination.suffix)
                adept_destination = destination.with_name(destination.stem + ".repairing" + destination.suffix) if repair_mode else destination
                try:
                    adept_destination.unlink(missing_ok=True)
                except OSError:
                    pass
                request = {
                    "schema_version": 2,
                    "source": {"type": "acsm", "provider": loan.source_provider, "path": str(acquisition.acsm_path), "headers": {}},
                    "destination": str(adept_destination),
                    "format": acsm_format,
                    "profile": profile_name,
                    "metadata": {"title": loan.title, "loan_id": loan.id},
                    # Kobo-Adept owns its post-download policy. Do not override
                    # run_hook; its public schema defaults it to true.
                    "options": {"force": False},
                }
                _TRACE_LOG.info(
                    "TRACE download adept begin provider_loan_id=%s profile=%s source_file=%s destination=%s format=%s format_source=%s",
                    loan.id, profile_name, acquisition.acsm_path.name, adept_destination, acsm_format, format_source,
                )
                adept_started = time.monotonic()
                originating_profile_id = self._originating_adept_profile_id(identity.identity_id, profile_name)
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
                if produced_path.suffix.casefold() != actual_suffix:
                    corrected = produced_path.with_suffix(actual_suffix)
                    if corrected.exists():
                        corrected = corrected.with_name(corrected.stem + ".reacquired-" + uuid.uuid4().hex + corrected.suffix)
                    produced_path.replace(corrected)
                    produced_path = corrected
                    _TRACE_LOG.info("TRACE download adept rename-format loan_id=%s format=%s path=%s", loan.id, actual_format, produced_path)
                if destination.suffix.casefold() != actual_suffix:
                    destination = destination.with_suffix(actual_suffix)
                    if destination.exists() and not repair_mode:
                        destination = destination.with_name(destination.stem + ".reacquired-" + uuid.uuid4().hex + destination.suffix)
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
            return {"status": "complete", "loan_id": loan.id, "path": str(final_path),
                    "adept_return_available": bool(acquisition.kind == "acsm" and adept_binding.get("return_available"))}
        except (KoboAdeptError, UserFacingError) as exc:
            if retained_acsm is not None and retained_acsm.exists():
                _TRACE_LOG.info("TRACE download acsm cleanup loan_id=%s action=keep reason=adept_error file=%s", loan.id, retained_acsm)
            code = str(getattr(exc, "code", "") or "adept_error")
            process = {}
            if failure_executor == "kobo-adept":
                raw = self.runtime_diagnostic("runtime.kobo-adept")
                for key in ("returncode", "duration_ms", "stdout_bytes", "stderr_bytes"):
                    value = raw.get(key)
                    if isinstance(value, int) and not isinstance(value, bool):
                        process[key] = value
                exit_code = getattr(exc, "exit_code", None)
                if isinstance(exit_code, int):
                    process["returncode"] = exit_code
                candidate = locals().get("adept_destination")
                if isinstance(candidate, Path):
                    try:
                        process["artifact_present"] = candidate.is_file()
                        if process["artifact_present"]:
                            process["artifact_bytes"] = candidate.stat().st_size
                    except OSError:
                        process["artifact_present"] = False
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
            retryable = bool(getattr(exc, "retryable", False))
            _TRACE_LOG.warning(
                "Acquisition failed loan_id=%s executor=%s code=%s retryable=%s exit_code=%s message=%s dt=%.3fs process=%s",
                loan.id, failure_executor, code, retryable, getattr(exc, "exit_code", None), str(exc), time.monotonic()-started, process,
            )
            status = "retryable_error" if retryable else "attention_required"
            self.storage.acquisition_put({
                **ledger_base, "status": status, "strategy": "loan_auto", "executor": failure_executor,
                "error_code": code, "error_message": message, "metadata": {"process": process},
                "attention_code": None if status == "retryable_error" else code,
            })
            return {"status": status, "loan_id": loan.id, "error": message, "code": code, "process": process}
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
            metadata = {"provider_condition": attention_code} if attention_code else {}
            if validation_failure:
                metadata.update({"repairable": True, "failure_stage": "post_adept_validation"})
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

    def synchronize_account_source(self, account: Account, *, deep: bool = False, control: Any = None) -> dict[str, Any]:
        try:
            result = self._synchronize_account_source_impl(account, deep=deep, control=control)
            self._record_action(account.identity_id, "synchronization", "success", provider_id=account.provider_id, facts={"deep": bool(deep)})
            return result
        except Exception as exc:
            self._record_action(account.identity_id, "synchronization", "failed", provider_id=account.provider_id, facts={"deep": bool(deep), "error": type(exc).__name__})
            raise

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
        lock = InterProcessLock(acquisition_lock_file())
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

    def _opening_sync_locked(self, progress: Callable[[dict[str, Any]], None] | None = None, *, deep: bool = False) -> dict[str, Any]:
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
                        _TRACE_LOG.info(
                            "TRACE loans sync begin identity=%s provider=%s credential=%s account=%s",
                            identity.identity_id, provider, account.credential_id, account.unique_id or "primary",
                        )
                        account_loans = self._loans_for_account(account, force=True)
                        _TRACE_LOG.info(
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
                            _TRACE_LOG.warning(
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
                        _TRACE_LOG.exception(
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
            emit("identity_end", **summary)

        try:
            mirror = self.mirror_personal_libraries()
            emit("catalog_mirror", result=mirror)
        except Exception as exc:
            _TRACE_LOG.warning("TRACE catalog mirror opening error=%s", exc)

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
                _TRACE_LOG.info("TRACE kobo sync skipped reason=fresh-cache interval_minutes=%s", self.config.get("kobo_backup.interval_minutes", 60))
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

    def _record_action(self, identity_id: str | None, action_type: str, outcome: str, *, provider_id: str = "", plugin_id: str = "", facts: Mapping[str, Any] | None = None) -> None:
        try:
            self.storage.action_event_put(identity_id=identity_id, action_type=action_type, outcome=outcome, provider_id=provider_id, plugin_id=plugin_id, facts=facts)
        except Exception as exc:
            self.diagnostics.add("STATISTICS", "action event", "error", detail={"action": action_type, "error": type(exc).__name__})

    def action_statistics(self, identity_id: str, *, window_seconds: int = 2592000):
        from .plugin_api import ActionStatistics
        window = max(60, int(window_seconds or 2592000))
        until = int(time.time()); since = until - window
        counts = {(row["action_type"], row["outcome"]): int(row["count"]) for row in self.storage.action_event_counts(identity_id=identity_id, since=since, until=until)}
        def count(action: str, outcome: str) -> int:
            return counts.get((action, outcome), 0)
        return ActionStatistics(
            window, since, until,
            count("acquisition", "success"), count("acquisition", "failed"),
            count("import", "success"), count("import", "failed"),
            count("synchronization", "success"), count("synchronization", "failed"),
            count("transformation", "success"), count("transformation", "failed"),
            count("transfer", "success"), count("transfer", "failed"),
        )

    def _record_loan_statistics(self, account, rows, *, from_cache=False):
        key='loan_statistics:'+str(account.identity_id)
        source=hashlib.sha256(f'{account.provider_id}:{account.credential_id}:{account.unique_id}'.encode()).hexdigest()
        value={'loan_keys':sorted({hashlib.sha256(f'{source}:{row.id}'.encode()).hexdigest() for row in rows}),
               'observed_at':None if from_cache else int(time.time()),'origin':'cache' if from_cache else 'provider'}
        self.storage.metadata_json_put_item(key,source,value,only_if_missing=from_cache)

    def identity_statistics(self, identity_id, *, window_seconds: int = 2592000):
        """Persisted inventory facts only; no scan, authentication or download."""
        placements=[r for r in self.storage.placements(state='present') if r.get('identity_id')==identity_id]
        artifacts={r['artifact_id']:r for r in self.storage.artifacts()}
        ids={r['artifact_id'] for r in placements if r['artifact_id'] in artifacts}
        formats=Counter();sources=Counter();covers=Counter()
        for aid in ids:
            artifact=artifacts[aid];formats[str(artifact.get('media_format') or 'unknown')]+=1
            meta=artifact.get('metadata') or {}
            if artifact.get('media_format') in {'epub','kepub'}:
                covers['extracted' if meta.get('epub_cover') else str(meta.get('epub_cover_status') or 'not_inspected')]+=1
        for provider in {r.get('provider_id') or 'local' for r in placements}:
            sources[provider]=len({r['artifact_id'] for r in placements if (r.get('provider_id') or 'local')==provider})
        try:observations=json.loads(self.storage.metadata_get('loan_statistics:'+str(identity_id)) or '{}')
        except (ValueError,TypeError):observations={}
        if not isinstance(observations,dict):observations={}
        selected=[]
        for account in self.accounts_for_identity(identity_id):
            key=hashlib.sha256(f'{account.provider_id}:{account.credential_id}:{account.unique_id}'.encode()).hexdigest()
            if isinstance(observations.get(key),dict):selected.append(observations[key])
        loan_keys={key for row in selected for key in row.get('loan_keys',[])}
        owned={r['entitlement_id'] for r in self.storage.entitlements(state='active') if r.get('identity_id')==identity_id and r.get('entitlement_type')=='owned'}
        return {'identity_id':identity_id,'local_books':len(ids),'local_placements':len(placements),'local_files':sum((r.get('metadata') or {}).get('artifact_kind')!='bundle' for r in placements),
                'known_loans':len(loan_keys) if selected else None,'loan_sources_observed':len(selected),
                'loans_observed_at':min((r.get('observed_at') for r in selected),default=None) if all(isinstance(r.get('observed_at'),int) for r in selected) else None,
                'remote_owned_rights':len(owned),'formats':dict(formats),'sources':dict(sources),'epub_covers':dict(covers),
                'actions':asdict(self.action_statistics(identity_id, window_seconds=window_seconds)),
                'scope':'persisted_inventory_last_observed_provider_loans_and_action_window'}

    def dashboard_state(self) -> list[dict[str, Any]]:
        """Return dashboard cards without triggering network I/O."""
        cards: list[dict[str, Any]] = []
        for identity in self.identities:
            providers = sorted({a.provider_id for a in self.accounts_for_identity(identity.identity_id)})
            latest = self.storage.latest_sync_run(identity.identity_id)
            profile_id = self.storage.adept_profile_binding_get("identity", identity.identity_id)
            try:
                deep_last_at = int(self.storage.metadata_get("catalog_deep_last_at") or 0) or None
            except (TypeError, ValueError):
                deep_last_at = None
            labels = {"search": "Recherche", "loans": "Emprunts", "history": "Historique",
                      "bookings": "Réservations", "borrow": "Emprunter", "digital_download": "Récupérer les prêts",
                      "direct_download": "Téléchargement direct", "personal_library": "Bibliothèque du compte",
                      "return_loan": "Restituer un prêt", "renew_loan": "Prolonger un prêt"}
            capabilities = set(); sources = []
            credentials = {(account.provider_id, account.credential_id) for account in self.accounts_for_identity(identity.identity_id)}
            for session in self._sessions.values():
                descriptor = session.descriptor
                public = getattr(descriptor, "instantiation", "multiple") == "singleton_public"
                if not self._provider_enabled_for_identity(session.provider_id, identity.identity_id):
                    continue
                if not public and (session.provider_id, session.credential_id) not in credentials:
                    continue
                sources.append({"provider_id": session.provider_id, "label": descriptor.display_name,
                                "connected": bool(session.connected)})
                if session.connected:
                    capabilities.update(key for key in labels if bool(getattr(session.capabilities, key, False)))
            cards.append({
                "sources": sources,
                "statistics": self.identity_statistics(identity.identity_id),
                "selected": bool(self.current_identity and self.current_identity.identity_id == identity.identity_id),
                "capabilities": [{"id": key, "label": labels[key]} for key in labels if key in capabilities],
                "identity_id": identity.identity_id, "label": identity.label, "directory_name": identity.directory_name,
                "providers": providers, "adept_profile_id": profile_id,
                "sync": latest, "deep_last_at": deep_last_at,
            })
        return cards

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
        return self.storage.backup_list("kobo")

    def kobo_backup_enabled(self) -> bool:
        return bool(self.config.get("kobo_backup.enabled", False))

    def kobo_backup_interval_minutes(self) -> int:
        try:
            return max(15, int(self.config.get("kobo_backup.interval_minutes", 60)))
        except (TypeError, ValueError):
            return 60

    def _kobo_backup_target_dir(self, identity_id: str | None = None) -> Path:
        # 0.8.1 makes the visible layout identity/provider mandatory for new
        # materializations. The historical Kobo override remains a base root;
        # existing flat files are never moved automatically.
        identity_id = identity_id or (self.current_identity.identity_id if self.current_identity else "default")
        configured = os.path.expandvars(os.path.expanduser(str(self.config.get("kobo_backup.directory", "") or "").strip()))
        if configured:
            base = Path(configured)
            if not base.is_absolute():
                base = self.library_dir() / base
            identity = self.identity_by_id(identity_id)
            directory_name = identity.directory_name if identity and identity.directory_name else identity_id
            path = base / safe_library_component(directory_name, "default") / "kobo"
            path.mkdir(parents=True, exist_ok=True)
            return path
        return self.library_identity_provider_dir(identity_id, "kobo")

    @staticmethod
    def _manifest_author(item: dict[str, Any]) -> str | None:
        authors = item.get("authors")
        if isinstance(authors, list):
            text = ", ".join(str(x) for x in authors if str(x).strip())
            return text or None
        text = str(item.get("author") or "").strip()
        return text or None

    @staticmethod
    def _kobo_backup_error_info(exc: Exception) -> dict[str, Any]:
        """Return a compact, non-secret diagnostic classification for a backup error."""
        message = str(exc) or exc.__class__.__name__
        code: str | None = None
        status_code: int | None = None
        phase = "integration"
        fatal = False

        if isinstance(exc, KoboVaultError):
            phase = "vault"
            code = exc.code
            status_code = exc.status_code
            fatal = status_code in {401, 403} or str(code or "").lower() in {
                "missing_token", "invalid_token", "invalid_bearer", "unauthorized",
                "forbidden", "insufficient_scope", "invalid_api_token",
            }
        elif isinstance(exc, (KoboAdeptError, UserFacingError)):
            phase = "adept"
            code = str(getattr(exc, "code", "") or "adept_error")
        else:
            lower = message.lower()
            if "bearer token invalide" in lower or "token api refus" in lower:
                phase = "vault"
                code = "invalid_token"
                fatal = True
            elif "scope insuffisant" in lower:
                phase = "vault"
                code = "insufficient_scope"
                fatal = True
            elif "candidat epub/pdf http direct" in lower or "candidat http epub/pdf" in lower:
                phase = "integration"
                code = "no_compatible_http_candidate"
            elif "aucun artefact téléchargeable compatible" in lower:
                phase = "vault"
                code = "no_downloadable_artifact"
            elif "library_sync/kdrm exige" in lower:
                phase = "integration"
                code = "missing_pinned_delivery_capability"

        if code is None:
            code = exc.__class__.__name__
        return {
            "phase": phase,
            "code": str(code),
            "status_code": status_code,
            "message": message,
            "fatal": fatal,
        }

    def kobo_backup_diagnostic(self) -> dict[str, Any]:
        """Summarise the persisted Kobo backup state without network access."""
        rows = self.storage.backup_list(provider_id="kobo")
        statuses = Counter(str(row.get("status") or "unknown") for row in rows)
        errors = Counter(
            str(row.get("error") or "").strip()
            for row in rows
            if str(row.get("status") or "") == "error" and str(row.get("error") or "").strip()
        )
        examples: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            message = str(row.get("error") or "").strip()
            if str(row.get("status") or "") != "error" or not message or message in seen:
                continue
            seen.add(message)
            examples.append({
                "item_id": row.get("item_id"),
                "title": row.get("title"),
                "error": message,
            })
            if len(examples) >= 10:
                break
        reconcile_examples = [
            {
                "item_id": row.get("item_id"),
                "title": row.get("title"),
                "status": row.get("status"),
                "local_path": row.get("local_path"),
                "message": row.get("error"),
            }
            for row in rows
            if str(row.get("status") or "") in {"reconcile_required", "reconcile_conflict"}
        ][:20]
        attention_examples = [
            {
                "item_id": row.get("item_id"),
                "title": row.get("title"),
                "attention_code": row.get("attention_code"),
                "message": row.get("error"),
            }
            for row in rows
            if str(row.get("status") or "") == "attention_required"
        ][:20]
        return {
            "items": len(rows),
            "statuses": dict(statuses),
            "error_summary": [
                {"message": message, "count": count}
                for message, count in errors.most_common(10)
            ],
            "error_examples": examples,
            "reconcile_examples": reconcile_examples,
            "attention_examples": attention_examples,
            "storage_schema_version": self.storage.schema_version(),
        }

    def _kobo_backup_due(self) -> bool:
        try:
            interval_minutes = max(1, int(self.config.get("kobo_backup.interval_minutes", 60) or 60))
        except (TypeError, ValueError):
            interval_minutes = 60
        raw = self.storage.metadata_get("kobo_backup_last_success_at")
        try:
            last = int(raw or 0)
        except (TypeError, ValueError):
            last = 0
        return last <= 0 or (int(time.time()) - last) >= interval_minutes * 60

    def sync_kobo_backup(self, progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        """Compatibility entry point for the Kobo local synchronization.

        0.8.0 keeps the validated cross-process serialization for acquisition work. The
        same global lock is intentionally named for local acquisition rather
        than Kobo so the 0.8.x loan synchronizer can share it.
        """
        lock = InterProcessLock(acquisition_lock_file())
        try:
            with lock:
                return self._sync_kobo_backup_locked(progress=progress)
        except LockBusyError as exc:
            raise ProviderConfigurationError(
                "Une synchronisation locale est déjà en cours dans une autre instance de cifereca"
            ) from exc

    def kobo_unlinked_local_count(self, identity_id: str) -> int:
        """Conservative guard, never infer a Kobo link from a filename/title."""
        known = {str(Path(r["local_path"]).resolve()) for r in self.storage.backup_list("kobo")
                 if r.get("identity_id") == identity_id and r.get("local_path")}
        default_id = self.identities[0].identity_id if self.identities else None
        paths = set()
        for row in self.storage.catalog_entries():
            owner = row.get("identity_id") or default_id
            raw = (row.get("metadata") or {}).get("path")
            if (owner == identity_id and row.get("local_state") == "local"
                    and str(row.get("provider_id") or "local") in {"local", "kobo"} and raw):
                path = Path(raw)
                if path.is_file() and str(path.resolve()) not in known:
                    paths.add(str(path.resolve()))
        return len(paths)

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
        def emit(event: str, **detail: Any) -> None:
            if progress is None:
                return
            try:
                progress({"event": event, **detail})
            except Exception:
                # Progress reporting must never break the backup itself.
                pass

        # Refresh filesystem membership before deciding whether acquisition is safe.
        self.catalog_scan()
        sessions = [
            session for session in self._sessions.values()
            if session.connected and session.provider_id == "kobo" and session.capabilities.backup
        ]
        target_dir = self._kobo_backup_target_dir()
        target_dirs: dict[str, str] = {}
        preferred = str(self.config.get("kobo_backup.format", "epub") or "epub").lower()
        vault_versions = sorted({
            str(getattr(session, "api_version", "") or "2.x") for session in sessions
        })
        vault_contract = ",".join(vault_versions) if vault_versions else "2.x"
        emit("adept_probe", message="Vérification de Kobo-Adept")
        adept_state = self.kobo_adept_state(force=True)
        emit(
            "adept_ready",
            compatible=bool(adept_state.get("compatible")),
            operational=bool(adept_state.get("operational")),
            software_version=adept_state.get("software_version"),
            contract_version=adept_state.get("contract_version"),
        )
        adapter_available = bool(adept_state.get("available") and adept_state.get("compatible") and adept_state.get("operational"))
        summary: dict[str, Any] = {
            "sessions": len(sessions), "manifest_items": 0, "scanned": 0, "attempted": 0, "pending": 0,
            "completed": 0, "downloaded": 0, "unchanged": 0, "metadata_refreshed": 0,
            "skipped": 0, "attention_required": 0, "errors": 0, "remaining": 0,
            "vault_errors": 0, "integration_errors": 0, "adept_errors": 0,
            "recovered_interrupted": 0, "recovered_unparsed": 0,
            "reconcile_pending": 0, "reconcile_conflicts": 0, "reconcile_skipped": 0,
            "legacy_pruned": 0, "legacy_local_preserved": 0, "legacy_entitlements_inactivated": 0,
            "aborted": False, "abort_reason": None,
            "target_dir": str(target_dir),
            "target_dirs": target_dirs,
            "storage_schema_version": self.storage.schema_version(),
            "vault_contract": vault_contract,
            "vault_display_profile": str(self.config.get("kobo_backup.display_profile", "Android") or "Android"),
            "vault_acsm_supported": any(bool(getattr(session, "supports_acquisition_acsm", False)) for session in sessions),
            "adept_acsm_available": bool(adept_state.get("acsm_available")),
            "adept_pinned_delivery_candidate": bool(adept_state.get("kobo_pinned_delivery_candidate")),
            "adept_separate_content_keys_replay": bool(adept_state.get("kobo_separate_content_keys_replay")),
            "adept_kdrm_available": bool(adept_state.get("kobo_kdrm_available")),
            "adept_kobo_formats": list(adept_state.get("kobo_formats") or []),
            "adept_profile_configured": any(
                bool(self.storage.adept_profile_binding_get("identity", identity.identity_id))
                for identity in self.identities
            ),
            "adept_contract": adept_state.get("contract_version") or 8,
            "adept_contract_min": 8,
            "adept_contract_preferred": 8,
            "adept_available": adapter_available,
            "adept_software_version": adept_state.get("software_version"),
            "adept_error": adept_state.get("error"),
        }

        # Reconcile local remnants from interrupted / previously-unparseable
        # acquisitions before contacting Vault again. Never delete a file here:
        # one plausible artifact is quarantined for verification; several
        # plausible artifacts become an explicit conflict. This prevents the
        # historical ``Title [deadbeef].epub`` duplication loop.
        for previous in self.storage.backup_list("kobo"):
            previous_status = str(previous.get("status") or "")
            previous_error = str(previous.get("error") or "")
            recoverable = (
                previous_status in {"acquiring", "reconcile_required", "reconcile_conflict"}
                or (previous_status == "error" and "réponse JSON invalide" in previous_error)
            )
            if not recoverable:
                continue
            if previous_status == "acquiring":
                summary["recovered_interrupted"] += 1
            if previous_status == "error" and "réponse JSON invalide" in previous_error:
                summary["recovered_unparsed"] += 1

            previous_identity = str(previous.get("identity_id") or "").strip() or self.identity_for_connection(
                str(previous.get("provider_id") or "kobo"), str(previous.get("credential_id") or "")
            ).identity_id
            previous_target_dir = self._kobo_backup_target_dir(previous_identity)
            target_dirs[previous_identity] = str(previous_target_dir)
            candidates = self._kobo_record_candidate_paths(previous, previous_target_dir)
            # Compatibility scan: legacy flat files may still exist after an interrupted
            # reclassification; include them to avoid duplicate downloads.
            legacy_target_dir = self.library_dir()
            if legacy_target_dir.resolve() != previous_target_dir.resolve():
                for legacy_candidate in self._kobo_record_candidate_paths(previous, legacy_target_dir):
                    if legacy_candidate not in candidates:
                        candidates.append(legacy_candidate)
            if len(candidates) == 1:
                artifact = candidates[0]
                self.storage.backup_put({
                    **previous,
                    "status": "reconcile_required",
                    "local_path": str(artifact),
                    "size": artifact.stat().st_size,
                    "sha256": try_sha256_file(artifact),
                    "updated_at": int(time.time()),
                    "error": (
                        "Artefact local présent après une acquisition Adept non réconciliée ; "
                        "vérification post-hook requise avant de le considérer complet"
                    ),
                    "job_id": None,
                })
                emit("reconcile_required", item_id=previous.get("item_id"), title=previous.get("title"), path=str(artifact))
            elif len(candidates) > 1:
                self.storage.backup_put({
                    **previous,
                    "status": "reconcile_conflict",
                    "local_path": None,
                    "updated_at": int(time.time()),
                    "error": (
                        f"{len(candidates)} artefacts locaux candidats détectés ; "
                        "aucune suppression automatique"
                    ),
                    "job_id": None,
                })
                emit("reconcile_conflict", item_id=previous.get("item_id"), title=previous.get("title"), count=len(candidates))
            elif previous_status == "acquiring":
                self.storage.backup_put({
                    **previous,
                    "status": "pending_acquisition",
                    "updated_at": int(time.time()),
                    "error": "Acquisition locale interrompue avant finalisation ; nouvelle tentative nécessaire",
                    "job_id": None,
                })
            elif previous_status in {"reconcile_required", "reconcile_conflict"}:
                # The user may have manually moved/removed the suspect file(s).
                # With no candidate left, allow a clean retry on the next pass.
                self.storage.backup_put({
                    **previous,
                    "status": "pending_acquisition",
                    "local_path": None,
                    "size": None,
                    "sha256": None,
                    "updated_at": int(time.time()),
                    "error": "Artefact à réconcilier absent ; nouvelle tentative autorisée",
                    "job_id": None,
                })

        recovery_rows = self.storage.backup_list("kobo")
        summary["reconcile_pending"] = sum(
            1 for row in recovery_rows
            if str(row.get("status") or "") in {"reconcile_required", "reconcile_conflict"}
        )
        summary["reconcile_conflicts"] = sum(
            1 for row in recovery_rows
            if str(row.get("status") or "") == "reconcile_conflict"
        )
        if summary["recovered_interrupted"]:
            emit("recovered", count=summary["recovered_interrupted"])
        if summary["recovered_unparsed"]:
            emit("recovered_unparsed", count=summary["recovered_unparsed"])
        error_counts: Counter[tuple[str, str, int | None, str]] = Counter()
        error_examples: list[dict[str, Any]] = []

        def record_error(exc: Exception, *, title: str | None = None, item_id: str | None = None) -> dict[str, Any]:
            info = self._kobo_backup_error_info(exc)
            phase = str(info.get("phase") or "integration")
            phase_counter = {"vault": "vault_errors", "integration": "integration_errors", "adept": "adept_errors"}.get(phase)
            if phase_counter:
                summary[phase_counter] += 1
            key = (phase, str(info["code"]), info.get("status_code"), str(info["message"]))
            error_counts[key] += 1
            if len(error_examples) < 10:
                example = {
                    "phase": info["phase"], "code": info["code"],
                    "status_code": info.get("status_code"), "message": info["message"],
                }
                if item_id:
                    example["item_id"] = item_id
                if title:
                    example["title"] = title
                error_examples.append(example)
            return info

        abort_all = False
        authoritative_manifest_ids: dict[str, set[str]] = {}
        for session in sessions:
            session_identity = self.identity_for_connection("kobo", session.credential_id)
            unlinked_local_count = self.kobo_unlinked_local_count(session_identity.identity_id)
            session_target_dir = self._kobo_backup_target_dir(session_identity.identity_id)
            target_dirs[session_identity.identity_id] = str(session_target_dir)
            try:
                emit("manifest_fetch", credential_id=session.credential_id, message="Lecture du manifest Kobo-Vault")
                manifest = session.backup_manifest()
                summary["manifest_items"] += len(manifest)
                authoritative_manifest_ids[session.credential_id] = {
                    str(raw.get("product_id") or raw.get("id") or "").strip()
                    for raw in manifest if isinstance(raw, dict)
                    if str(raw.get("product_id") or raw.get("id") or "").strip()
                }
                emit("manifest", credential_id=session.credential_id, total=len(manifest))
            except Exception as exc:
                summary["errors"] += 1
                info = record_error(exc, title=session.label)
                if info["fatal"]:
                    summary["aborted"] = True
                    summary["abort_reason"] = info
                    abort_all = True
                    break
                continue

            for index, raw in enumerate(manifest):
                if not isinstance(raw, dict):
                    continue
                summary["scanned"] += 1
                progress_title = str(raw.get("title") or raw.get("product_id") or raw.get("id") or "Kobo")
                emit(
                    "scan",
                    current=index + 1,
                    total=len(manifest),
                    title=progress_title,
                    downloaded=summary["downloaded"],
                    errors=summary["errors"],
                    unchanged=summary["unchanged"],
                    skipped=summary["skipped"],
                )
                product_id = str(raw.get("product_id") or raw.get("id") or "").strip()
                if not product_id:
                    summary["skipped"] += 1
                    continue
                content_type = str(raw.get("content_type") or "ebook").lower()
                if content_type not in {"ebook", "book", "epub"}:
                    summary["skipped"] += 1
                    continue
                revision_id = str(raw.get("revision_id") or "").strip() or None
                title = str(raw.get("title") or product_id)
                author = self._manifest_author(raw)
                fmt = str(raw.get("format") or "").upper() or None
                current = self.storage.backup_get("kobo", session.credential_id, product_id)
                account_id = session.accounts[0].unique_id if len(session.accounts) == 1 else None
                fresh_cover_url = session.cover_url(raw) if hasattr(session, "cover_url") else None

                base_record = {
                    "provider_id": "kobo",
                    "credential_id": session.credential_id,
                    "account_id": account_id,
                    "identity_id": session_identity.identity_id,
                    "entitlement_type": "owned",
                    "item_id": product_id,
                    "revision_id": revision_id,
                    "title": title,
                    "author": author,
                    "format": fmt,
                    "cover_url": fresh_cover_url or (current.get("cover_url") if current else None),
                    "local_path": current.get("local_path") if current else None,
                    "size": current.get("size") if current else None,
                    "sha256": current.get("sha256") if current else None,
                    "updated_at": int(time.time()),
                    "job_id": None,
                    "attention_code": None,
                }

                # Metadata refresh is independent from acquisition. Historical
                # backup rows may have been persisted before Vault exposed a
                # normalized cover_url. Refresh the durable projection before any
                # fast-path continue so title/author/format/cover metadata can heal
                # without re-downloading an unchanged local artifact.
                if current:
                    refreshed = {
                        **current,
                        **base_record,
                        "status": current.get("status") or "pending",
                        "error": current.get("error"),
                        "job_id": current.get("job_id"),
                        "attention_code": current.get("attention_code"),
                        "attempt_count": current.get("attempt_count"),
                        "last_attempt_at": current.get("last_attempt_at"),
                        "acquisition_strategy": current.get("acquisition_strategy"),
                        "executor": current.get("executor"),
                        "adept_profile": current.get("adept_profile"),
                        "loan_id": current.get("loan_id"),
                        "expires_at": current.get("expires_at"),
                    }
                    before_cover = str(current.get("cover_url") or "").strip()
                    after_cover = str(refreshed.get("cover_url") or "").strip()
                    if (
                        before_cover != after_cover
                        or current.get("title") != refreshed.get("title")
                        or current.get("author") != refreshed.get("author")
                        or current.get("format") != refreshed.get("format")
                        or current.get("identity_id") != refreshed.get("identity_id")
                    ):
                        summary["metadata_refreshed"] += 1
                    self.storage.backup_put(refreshed)
                    current = self.storage.backup_get("kobo", session.credential_id, product_id) or refreshed

                if current and str(current.get("status") or "") in {"reconcile_required", "reconcile_conflict"}:
                    summary["reconcile_skipped"] += 1
                    emit(
                        "reconcile_waiting",
                        current=index + 1, total=len(manifest), title=title, item_id=product_id,
                        status=current.get("status"), path=current.get("local_path"),
                    )
                    continue

                if current and current.get("status") in {"ok", "complete"} and current.get("revision_id") == revision_id:
                    local = Path(str(current.get("local_path") or ""))
                    if local.is_file():
                        try:
                            observed = self._observed_backup_artifact(local)
                        except ProviderConfigurationError:
                            # A stale/invalid file must not retain a complete state.
                            self.storage.backup_put({**current, "status": "reconcile_required", "error": "Artefact local invalide"})
                            summary["reconcile_skipped"] += 1
                            continue
                        self.storage.backup_put({**current, **observed})
                        summary["unchanged"] += 1
                        continue

                # Vault 2.3.1 exposes archived items explicitly. Keep an already
                # acquired local file (handled above), otherwise persist a user
                # action instead of treating the title as permanently unavailable.
                if bool(raw.get("archived")):
                    message = "Livre archivé sur Kobo ; restaurez-le dans votre bibliothèque Kobo avant de réessayer"
                    self.storage.backup_put({
                        **base_record,
                        "status": "attention_required",
                        "attention_code": "archived_book",
                        "error": message,
                    })
                    summary["attention_required"] += 1
                    emit(
                        "attention_required", current=index + 1, total=len(manifest),
                        title=title, item_id=product_id, code="archived_book", message=message,
                    )
                    continue

                if raw.get("downloadable") is False:
                    summary["skipped"] += 1
                    continue

                if unlinked_local_count and not bool(self.config.get("kobo_backup.allow_unlinked_acquisition", False)):
                    message = f"Acquisition suspendue : {unlinked_local_count} livre(s) local(aux) sans liaison Kobo. Rapprochez les fichiers ou autorisez explicitement l’acquisition dans les paramètres."
                    self.storage.backup_put({**base_record, "status": "attention_required", "attention_code": "unlinked_local_books", "error": message})
                    summary["attention_required"] += 1
                    emit("attention_required", current=index + 1, total=len(manifest), title=title, item_id=product_id, code="unlinked_local_books", message=message)
                    _TRACE_LOG.info("TRACE kobo acquisition decision=blocked reason=unlinked-local-books count=%s", unlinked_local_count)
                    continue

                if not adapter_available:
                    adept_error = adept_state.get("error") if isinstance(adept_state.get("error"), dict) else {}
                    reason = str(adept_error.get("message") or "Kobo-Adept indisponible")
                    self.storage.backup_put({**base_record, "status": "pending_acquisition", "error": reason})
                    summary["pending"] += 1
                    continue

                summary["attempted"] += 1
                attempt_count = int(current.get("attempt_count") or 0) + 1 if current else 1
                attempted_at = int(time.time())
                self.storage.backup_put({
                    **base_record,
                    "status": "acquiring",
                    "error": None,
                    "attempt_count": attempt_count,
                    "last_attempt_at": attempted_at,
                    "executor": "kobo-adept",
                })
                emit("acquiring", current=index + 1, total=len(manifest), title=title, item_id=product_id)
                try:
                    normalizer = getattr(session, "normalize_item", None)
                    if normalizer is None:
                        raise ProviderConfigurationError("Kobo provider sans normalisation LibraryItem")
                    search_item = normalizer(raw, default_collection="library")
                    result = self._execute_kobo_acquisition(search_item, session_target_dir)
                    artifact = result.path
                    if artifact is None or not artifact.is_file():
                        raise ProviderConfigurationError("Acquisition Kobo sans fichier local")
                    observed = self._observed_backup_artifact(artifact)
                    size = observed["size"]
                    sha256 = observed["sha256"]
                    result_metadata = result.metadata if isinstance(result.metadata, dict) else {}
                    self.storage.backup_put({
                        **base_record,
                        "account_id": result.account_id or account_id,
                        "format": observed["format"],
                        "status": "complete",
                        "local_path": str(artifact),
                        "size": size if size is not None else artifact.stat().st_size,
                        "sha256": sha256,
                        "updated_at": int(time.time()),
                        "error": None,
                        "attempt_count": attempt_count,
                        "last_attempt_at": attempted_at,
                        "acquisition_strategy": result_metadata.get("acquisition_mode"),
                        "executor": "kobo-adept",
                        "adept_profile": result_metadata.get("adept_profile"),
                    })
                    summary["downloaded"] += 1
                    summary["completed"] += 1
                    emit(
                        "downloaded", current=index + 1, total=len(manifest), title=title,
                        item_id=product_id, path=str(artifact), downloaded=summary["downloaded"],
                        errors=summary["errors"],
                    )
                except Exception as exc:
                    if isinstance(exc, (KoboAdeptError, UserFacingError)) and getattr(exc, "code", "") == "result_unparseable_artifact_present":
                        details = dict(getattr(exc, "details", {}) or {})
                        artifact_path = Path(str(details.get("artifact_path") or "")) if details.get("artifact_path") else None
                        self.storage.backup_put({
                            **base_record,
                            "status": "reconcile_required",
                            "local_path": str(artifact_path) if artifact_path else None,
                            "size": details.get("artifact_size"),
                            "sha256": details.get("artifact_sha256"),
                            "error": str(exc),
                            "updated_at": int(time.time()),
                        })
                        summary["reconcile_pending"] += 1
                        emit(
                            "reconcile_required",
                            current=index + 1, total=len(manifest), title=title, item_id=product_id,
                            path=str(artifact_path) if artifact_path else None,
                        )
                        continue
                    info = self._kobo_backup_error_info(exc)
                    if str(info.get("code") or "") == "archived_book":
                        message = "Livre archivé sur Kobo ; restaurez-le dans votre bibliothèque Kobo avant de réessayer"
                        self.storage.backup_put({
                            **base_record,
                            "status": "attention_required",
                            "attention_code": "archived_book",
                            "error": message,
                            "attempt_count": attempt_count,
                            "last_attempt_at": attempted_at,
                            "executor": "kobo-adept",
                            "updated_at": int(time.time()),
                        })
                        summary["attention_required"] += 1
                        emit(
                            "attention_required", current=index + 1, total=len(manifest),
                            title=title, item_id=product_id, code="archived_book", message=message,
                        )
                        continue
                    message = str(exc)
                    _TRACE_LOG.warning(
                        "TRACE kobo acquisition failed identity=%s credential=%s item_id=%s exception=%s code=%s phase=%s status=%s error=%s",
                        session_identity.identity_id, session.credential_id, product_id, type(exc).__name__,
                        info.get("code"), info.get("phase"), info.get("status_code"), message,
                    )
                    self.storage.backup_put({
                        **base_record, "status": "error", "error": message,
                        "attempt_count": attempt_count, "last_attempt_at": attempted_at,
                        "executor": "kobo-adept", "updated_at": int(time.time()),
                    })
                    summary["errors"] += 1
                    info = record_error(exc, title=title, item_id=product_id)
                    emit(
                        "item_error", current=index + 1, total=len(manifest), title=title,
                        item_id=product_id, code=info.get("code"), phase=info.get("phase"),
                        message=info.get("message"), downloaded=summary["downloaded"], errors=summary["errors"],
                    )
                    if info["fatal"]:
                        summary["aborted"] = True
                        summary["abort_reason"] = info
                        summary["remaining"] += max(0, len(manifest) - index - 1)
                        abort_all = True
                        break
            if abort_all:
                break

        # ``backup_items`` is a current-manifest compatibility projection.
        # Prune it only after the complete Kobo run succeeded: a failed/partial
        # sync must never make a remote title disappear merely because its
        # manifest could not be observed.  Artifacts/placements/catalog rows are
        # deliberately preserved by Storage.backup_prune_missing().
        if not summary.get("errors") and not summary.get("aborted"):
            for credential_id, seen_ids in authoritative_manifest_ids.items():
                pruned = self.storage.backup_prune_missing("kobo", credential_id, seen_ids)
                summary["legacy_pruned"] += int(pruned.get("pruned") or 0)
                summary["legacy_local_preserved"] += int(pruned.get("local_preserved") or 0)
                summary["legacy_entitlements_inactivated"] += int(pruned.get("entitlements_inactivated") or 0)
                if pruned.get("pruned"):
                    emit(
                        "legacy_pruned", credential_id=credential_id,
                        count=pruned.get("pruned"), local_preserved=pruned.get("local_preserved"),
                    )

        summary["error_summary"] = [
            {
                "phase": phase, "code": code, "status_code": status_code,
                "message": message, "count": count,
            }
            for (phase, code, status_code, message), count in error_counts.most_common(10)
        ]
        summary["error_examples"] = error_examples
        # Backward-compatible concise text list for existing callers.
        summary["error_messages"] = [x["message"] for x in error_examples[:10]]
        self.diagnostics.add(
            "KOBO", "backup sync", "ok" if not summary["errors"] else "error",
            detail={k: v for k, v in summary.items() if k != "error_messages"},
        )
        emit("done", **{k: v for k, v in summary.items() if k not in {"error_examples", "error_messages"}})
        if not summary.get("errors") and not summary.get("aborted"):
            self.storage.metadata_set("kobo_backup_last_success_at", int(time.time()))
        return summary

    def _account_for_loan(self, loan: Loan) -> Account:
        """Resolve the exact borrower recorded on a loan.

        0.3.2 inferred this from the currently selected account and could pick
        the primary account for a linked-account loan. New loans carry
        ``source_account_id``; old cached loans keep a conservative fallback.
        """
        candidates = [
            a for a in self.accounts
            if a.provider_id == loan.source_provider and a.credential_id == loan.source_credential
        ]
        if loan.source_account_id is not None:
            # "primary" is Paris' stable provenance sentinel for the primary
            # borrower. The provider API itself still expects an empty UID for
            # that account, so return the real Account object (unique_id="").
            if loan.source_account_id == "primary":
                primary = next((a for a in candidates if a.is_primary), None)
                if primary is not None:
                    return primary
            for account in candidates:
                if account.unique_id == loan.source_account_id:
                    return account
            raise ProviderConfigurationError(
                f"Compte emprunteur introuvable pour le prêt {loan.id}: "
                f"{loan.source_provider}/{loan.source_credential}/{loan.source_account_id!r}"
            )
        # Compatibility for cache/data produced before 0.3.3. Prefer the
        # current account only when it belongs to the same provider identity.
        if self.current_account in candidates:
            return self.current_account
        primary = next((a for a in candidates if a.is_primary), None)
        if primary is not None:
            return primary
        if candidates:
            return candidates[0]
        raise ProviderConfigurationError(
            f"Aucun compte connecté pour {loan.source_provider}/{loan.source_credential}"
        )

    def _acquisition_work_dir(self, identity_id: str, provider_id: str, loan_id: str | None = None) -> Path:
        """Return a mutable staging directory outside the user library tree."""
        base = self.storage.path.parent / "acquisition-work" / safe_library_component(identity_id, "default") / safe_library_component(provider_id, "provider")
        if loan_id:
            base = base / safe_library_component(loan_id, "loan")
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _cleanup_acsm_after_success(self, path: Path | None, *, loan_id: str) -> None:
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
            acsm_cache.forget(path)
            _TRACE_LOG.info("TRACE download acsm cleanup loan_id=%s action=deleted file=%s", loan_id, path)
            # Remove now-empty per-loan staging directories only.
            parent = path.parent
            try:
                parent.rmdir()
            except OSError:
                pass
        except OSError as exc:
            _TRACE_LOG.warning("TRACE download acsm cleanup loan_id=%s action=keep reason=delete_failed error=%s file=%s", loan_id, type(exc).__name__, path)

    def _cleanup_old_acsm_work(self, *, max_age_seconds: int | None = None) -> dict[str, int]:
        root = self.storage.path.parent / "acquisition-work"
        if max_age_seconds is None:
            try:
                retention_days = max(1, int(self.config.get("acquisition.acsm_retention_days", 7) or 7))
            except (TypeError, ValueError):
                retention_days = 7
            max_age_seconds = retention_days * 24 * 3600
        now = time.time()
        deleted = kept = 0
        if not root.is_dir():
            return {"deleted": 0, "kept": 0}
        for path in root.rglob("*.acsm"):
            try:
                age = now - path.stat().st_mtime
                if age >= max_age_seconds:
                    path.unlink()
                    acsm_cache.forget(path)
                    deleted += 1
                else:
                    kept += 1
            except OSError:
                kept += 1
        if deleted:
            _TRACE_LOG.info("TRACE download acsm cleanup-old deleted=%s kept=%s max_age_seconds=%s", deleted, kept, max_age_seconds)
        return {"deleted": deleted, "kept": kept}

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
        hook_item = SearchResult(
            id=loan.id, record_id=str(loan.record_id or loan.id), docbase=loan.provider,
            medium="digital", title=loan.title, author=None, type=loan.document_type,
            format="ACSM", publisher=None, published_at=None, description=None,
            thumbnail_url=loan.thumbnail_url, friendly_url=loan.ebook_url, scenario="LOAN",
            source_provider=loan.source_provider, source_credential=loan.source_credential,
            download_url=url, content_type="ebook",
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
        return Acquisition(
            provider_id=loan.source_provider,
            credential_id=loan.source_credential,
            account_id=account_key,
            item_id=str(loan.record_id or loan.id),
            loan_id=loan.id,
            kind="acsm" if is_acsm else "already_local",
            source_url=url or None,
            local_path=path,
            mime_type=self.acquisition.mime_type_for(path, hook_item),
            drm="adept" if is_acsm else "unknown",
            expires_at=loan.due_at,
            metadata={
                "title": loan.title,
                "cover_url": loan.thumbnail_url,
                "content_type": "ebook",
                "format": path.suffix.lstrip(".").upper() or None,
            },
            acsm_path=path if is_acsm else None,
            artifact_path=None if is_acsm else path,
            target_dir=library_target_dir,
            action="download",
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
