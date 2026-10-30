from __future__ import annotations

from .core import acsm_staging, loan_failure_evidence, adept_acquisition_return
from .core import adept_artifact_publication
from .core import loan_acquisition_admission, loan_local_preparation
from .core import loan_provider_delivery
from .core import loan_completion_receipt, loan_initial_receipt
from .core import loan_adept_profile_guard
from .core import manual_acsm_import
from .core import provider_loan_return
from .core import return_adept_orchestrator

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
from .core import loan_return_verification
from .core import adept_inventory_reconciliation
from .core import kobo_backup
from .core import sync_coordination, sync_projection
from .core import library_projection, library_mutation
from .core.source_context import source_context_epoch
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
from .core.loan_safe_trace import loan_trace_ref, trace_integer, trace_duration_ms
from .paths import acquisition_lock_file, data_dir
from .models import Account, Booking, HistoryItem, Holding, ItemDetail, Loan, SearchResult
from .core import loan_accounts, loan_download_projection, loan_adept_fulfillment
from .providers import ProviderConfigurationError, ProviderRegistry, ProviderSearchCapabilities, ProviderSession
from .plugin_api import AttentionRequired, Cancelled, BundleError, DrmMutationRequest, UserFacingError, load_bundle_manifest, ProviderSearchEvent, ProviderSearchStream
from .storage import Storage
from .core.provider_stream import consume_provider_stream, SearchStreamFailure, SearchStreamCancelled
from .core.provider_collection_cache import ProviderCollectionSnapshotStore

def _loan_cache_key(account: Account) -> str:
    """Opaque, unambiguous identity/provider/credential/borrower cache scope.

    The legacy `provider:credential:uid:loans` alias omitted identity, and
    `:` can occur inside a provider credential or borrower identifier. Do
    not read old aliases: they cannot be safely attributed retroactively.
    """
    if type(account) is not Account:
        raise ProviderConfigurationError("loan_cache_scope_invalid")
    parts = (
        account.identity_id, account.provider_id,
        account.credential_id, account.unique_id,
    )
    if any(type(value) is not str or len(value) > 1024
           or chr(0) in value for value in parts):
        raise ProviderConfigurationError("loan_cache_scope_invalid")
    serialized = json.dumps(parts, ensure_ascii=True, separators=(',', ':'))
    return "loans.v2:" + hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def _cached_loans_for_exact_account(value: object, account: Account) -> list[Loan] | None:
    """Treat cached rows as untrusted: never rewrite their source/identity.

    A cache hit is only usable if every row still identifies the same
    provider, credential, account and identity. Corrupt/legacy rows trigger
    an ordinary fresh provider read, never a cross-account display.
    """
    if type(value) is not list or len(value) > 10_000:
        return None
    expected_accounts = {account.unique_id}
    if account.provider_id == 'paris' and account.unique_id == '':
        expected_accounts.add('primary')
    loans = []
    for value_row in value:
        if type(value_row) is not dict:
            return None
        try:
            row = Loan(**value_row)
        except (TypeError, ValueError):
            return None
        if not (
            type(row) is Loan
            and type(row.id) is str and bool(row.id)
            and type(row.source_provider) is str
            and row.source_provider == account.provider_id
            and type(row.source_credential) is str
            and row.source_credential == account.credential_id
            and type(row.source_account_id) is str
            and row.source_account_id in expected_accounts
            and type(row.identity_id) is str
            and row.identity_id == account.identity_id
            and type(row.medium) is str
            and row.medium in ('digital', 'physical', 'unknown')
        ):
            return None
        loans.append(row)
    return loans


SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._()\- àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ]+")

_TRACE_LOG = logging.getLogger("cifereca")
_SEARCH_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="cifereca-search")
# Serialize automatic loan check-and-record within this process. Opening sync
# additionally holds the existing cross-process acquisition lock.
_AUTOMATIC_LOAN_GUARD = Lock()


class _LoanDownloadAdmissionRefused(ProviderConfigurationError):
    """One typed local eligibility refusal, preserving the legacy exception kind."""

    def __init__(self, code: str):
        super().__init__("Ce prêt ne permet pas une acquisition numérique")
        self.code = code


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
        # Source operations survive UI selection changes. Reconfiguration
        # and disconnect revoke their admission independently of UI searches.
        self._source_context_epoch = 0
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
        except Exception:
            # Directory migration failures can include absolute library paths,
            # personal profile names and SQLite locations. None of their raw
            # text or chained exception context belongs in startup feedback.
            raise ProviderConfigurationError(
                "Migration de répertoire à reprendre"
            ) from None

    def save_diagnostic_preferences(self, *, enabled: bool, level: str) -> None:
        """Persist the immediate diagnostic controls, independently of drafts."""
        from copy import deepcopy
        from .logging_utils import set_diagnostics_level
        level = "verbose" if level == "detailed" else str(level)
        if level not in {"errors", "normal", "verbose", "trace"}:
            raise ValueError("Niveau de diagnostic inconnu")
        draft = deepcopy(self.config)
        draft.set("diagnostics.enabled", True)
        draft.set("diagnostics.level", level)
        draft.save()
        self.config.data = draft.data
        self.diagnostics.enabled = True
        self.diagnostics.level = level
        set_diagnostics_level(level)

    def loan_download_eligibility(self, loan: Loan) -> dict[str, object]:
        """Read-only explanation of a digital-loan action's availability.

        No refresh, provider GET, remote download, or log emission; the
        connected session and its announced capability are the only facts.
        Never publish account, loan, credential, URL or provider exception.
        This is NOT evidence of Adobe/PNB borrower authorization: the real
        acquisition pipeline still validates identity, rights, DRM and leases.
        """
        if loan.medium != "digital":
            return {"available": False, "code": "not_digital"}
        try:
            session = self._session_for_source(
                loan.source_provider, loan.source_credential,
            )
        except Exception:
            return {"available": False, "code": "provider_unavailable"}
        try:
            if not bool(session.connected):
                return {"available": False, "code": "provider_disconnected"}
            if not bool(session.capabilities.digital_download):
                return {"available": False, "code": "download_unsupported"}
        except Exception:
            return {"available": False, "code": "provider_unavailable"}
        return {"available": True, "code": "available"}

    def can_download_loan(self, loan: Loan) -> bool:
        return self.loan_download_eligibility(loan)["available"] is True

    def stage_bnf_loan_acsm(
        self, loan: Loan, *, confirmed: bool = False,
        expected_identity_id: str = "",
    ) -> dict[str, str]:
        """Deliberate BnF ACSM candidate only; never the automatic download path.

        Caller must capture the exact identity before its UI confirmation and
        pass it back. The Core revalidates this owner before and after HTTP.
        No entitlement, acquisition, ADEPT or borrowed-state mutation.
        """
        from .core.bnf_explicit_acsm import stage_bnf_loan_acsm
        from .core.diagnostic_bnf_loan_action import ActionObservation, BnfLoanActionError
        observation = ActionObservation(self, 'stage_loan_token')
        try:
            result = stage_bnf_loan_acsm(
                self, loan, confirmed=confirmed,
                expected_identity_id=expected_identity_id,
                interprocess_lock=InterProcessLock,
                lock_path=acquisition_lock_file, busy_error=LockBusyError,
                inspect_artifact=inspect_artifact, observe_phase=observation.at,
            )
        except BnfLoanActionError as exc:
            observation.emit('failed', exc.code)
            raise
        except Exception as exc:
            code = 'bnf_local_staging_failed' if isinstance(exc, OSError) else 'bnf_action_failed'
            observation.emit('failed', code)
            raise BnfLoanActionError(code) from None
        observation.emit('success', 'candidate_acsm')
        return result

    def import_bnf_loan_acsm(
        self, source: Path, *, expected_identity_id: str, loan: Loan,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Import the exact staged loan token with manual ADEPT attestation.

        Physical placement identifies BnF as the observed source. The ledger
        and DRM execution remain manual; neither establishes a BnF right.
        """
        from .core.diagnostic_bnf_loan_action import ActionObservation, BnfLoanActionError
        observation = ActionObservation(self, 'import_acsm')
        try:
            if (type(expected_identity_id) is not str or not expected_identity_id
                    or self.current_identity is None
                    or self.current_identity.identity_id != expected_identity_id):
                raise BnfLoanActionError('bnf_loan_scope_unproven')
            if (type(loan) is not Loan or loan.source_provider != 'bnf-pret'
                    or loan.identity_id != expected_identity_id
                    or loan.medium != 'digital' or loan.state != 'active'
                    or not loan.id or not loan.record_id or not loan.source_credential
                    or not loan.source_account_id
                    or self.storage.identity_for_connection(
                        'bnf-pret', loan.source_credential) != expected_identity_id):
                raise BnfLoanActionError('bnf_loan_scope_unproven')
            try:
                account = self._account_for_loan(loan)
                expected = self._acquisition_work_dir(expected_identity_id, 'bnf-pret', loan.id)
                candidate = Path(source)
                if (account.provider_id != 'bnf-pret'
                        or account.unique_id != loan.source_account_id
                        or account.credential_id != loan.source_credential
                        or account.identity_id != expected_identity_id
                        or expected.is_symlink() or candidate.is_symlink()
                        or candidate.parent.resolve() != expected.resolve()
                        or not candidate.name.startswith('bnf-loan-')
                        or candidate.suffix.casefold() != '.acsm'):
                    raise BnfLoanActionError('bnf_loan_scope_unproven')
            except BnfLoanActionError:
                raise
            except Exception:
                raise BnfLoanActionError('bnf_loan_scope_unproven') from None
            from .core.bnf_import_provenance import observed_loan, remember_import
            display_scope = observed_loan(loan)
            observation.at('adept_import')
            result = self.import_acsm(source, identity_id=expected_identity_id,
                                      provider_id='manual', title=title,
                                      placement_provider_id='bnf-pret')
            remember_import(self, result, display_scope)
        except BnfLoanActionError as exc:
            observation.emit('failed', exc.code)
            raise
        except Exception:
            observation.emit('failed', 'bnf_import_failed')
            raise BnfLoanActionError('bnf_import_failed') from None
        status = result.get('status') if type(result) is dict else None
        if status in {'complete', 'unchanged'}:
            observation.emit('success', status)
        elif status == 'attention_required' and type(result.get('code')) is str and result['code'] in {
            'adept_profile_required', 'adept_profile_ambiguous',
        }:
            observation.emit('attention_required', result['code'])
        else:
            observation.emit('failed', 'bnf_import_unconfirmed')
        return result

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
                self._source_context_epoch += 1
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

    def set_canonical_acquisition_consent(self, account: Account, source, *, allowed: bool) -> None:
        """Explicit exact-right preference; never performs a provider request."""
        from .core.source_acquisition_consent import set_source_acquisition_consent
        set_source_acquisition_consent(self, account, source, allowed=allowed)

    def _canonical_persisted_consent(self, account: Account):
        """Fresh Core-owned consent route used by the existing source Sync."""
        from .core.source_acquisition_consent import read_source_acquisition_consent
        return read_source_acquisition_consent(
            self, account, configured_policy=LibraryService._canonical_configured_consent_current)

    def _canonical_opening_persisted_consent(self, account: Account):
        # Configured source acquisition is dispatched by its durable startup
        # claim, which then calls this same source Sync. Do not launch a second
        # automatic batch from the independent opening inventory task.
        from .core.source_acquisition_consent import read_source_acquisition_consent
        return read_source_acquisition_consent(self, account)

    @staticmethod
    def _canonical_configured_consent_current(connection, source) -> bool:
        from .core.numilog_materialization_consent import canonical_consent_current
        return canonical_consent_current(connection, source)

    def _canonical_consent_epoch(self, account):
        from .core.numilog_materialization_consent import canonical_consent_epoch
        return canonical_consent_epoch(self, account)

    @staticmethod
    def _canonical_consent_epoch_current(connection, account, expected):
        from .core.numilog_materialization_consent import canonical_consent_epoch_current
        return canonical_consent_epoch_current(connection, account, expected)

    def _canonical_reconcile_existing(self, account, snapshot, *, context_current, shared_lock):
        from .core.numilog_materialization_startup import reconcile_existing
        return reconcile_existing(self, account, snapshot, context_current=context_current,
                                  shared_lock=shared_lock)

    def _canonical_publication_consent_current(self, connection, source) -> bool:
        """Lock-safe check on the publisher's own SQLite transaction."""
        from .core.source_acquisition_consent import consent_current_in_transaction
        return consent_current_in_transaction(
            connection, source, configured_policy=LibraryService._canonical_configured_consent_current)

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
            # The Loans page reads this projection synchronously. Retire the
            # old profile's loans before any asynchronous refresh can arrive.
            self.last_loans = []
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
        """Public facade with its stable, no-argument compatibility contract."""
        return provider_sessions.connect_configured(self, logger=_TRACE_LOG)

    def connect_configured_with_caller(self, *, caller_category: str) -> dict[str, Any]:
        """Core-owned GUI entrypoint carrying an attested, closed caller kind.

        A separate name avoids silently changing the public API used by
        CLI, legacy plugins and service facade reflection contracts.
        """
        return provider_sessions.connect_configured(
            self, logger=_TRACE_LOG, caller_category=caller_category,
        )

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
        key = _loan_cache_key(account)
        ttl = int(self.config.get("cache.dashboard_seconds", 300))
        if not force:
            cached = self.storage.cache_get("account", key)
            rows = _cached_loans_for_exact_account(cached, account)
            if rows is not None:
                self._record_loan_statistics(account, rows, from_cache=True)
                return rows
        session = self._session_for_account(account)
        if not session.capabilities.loans:
            return []
        # Capture immutable read coordinates before *either* provider I/O
        # path. A mutable legacy Account must not move an observation to a
        # different identity, borrower or cache key while loans() is running.
        request_scope = (
            account.identity_id, account.provider_id,
            account.credential_id, account.unique_id,
        )
        request_epoch = source_context_epoch(self)
        request_session_fields = (
            getattr(session, "provider_id", None),
            getattr(session, "credential_id", None),
            getattr(session, "connected", None),
        )
        session_key = self._session_key(
            request_scope[1], request_scope[2],
        )
        registered_at_read = self._sessions.get(session_key) is session
        # Prefer the atomic, scoped v2 observation where published. The v1
        # list reader stays available for old installed plugin packages; it
        # is never consulted as a second read of an opt-in snapshot.
        # A provider can expose a property whose getter performs a remote
        # request. Capability discovery must not execute that property or
        # silently fall back to the v1 reader when the typed declaration is
        # malformed; read_loan_inventory validates it without a second GET.
        raw_reader = inspect.getattr_static(
            session, "loan_inventory_snapshot", None,
        )
        if raw_reader is not None:
            from .plugin_api.loan_inventory import read_loan_inventory
            snapshot = read_loan_inventory(session, account)
            if snapshot.status in ("failed", "cancelled"):
                raise ProviderConfigurationError("loan_inventory_read_unavailable")
            # The reader already checks the account and mutable session
            # fields; additionally protect against an ABA source rebind or
            # replacement of the registered provider during the I/O.
            if (source_context_epoch(self) != request_epoch
                    or self._session_for_account(account) is not session):
                raise ProviderConfigurationError("loan_inventory_scope_changed")
            rows = list(snapshot.loans)
        else:
            rows = session.loans(account)
            # Legacy v1 has no immutable envelope or post-I/O scope guard.
            # Close that gap before any normalization or cache publication,
            # including in-place account edits, session swaps and ABA epoch
            # changes. Preserve non-registered legacy test/plugin shims:
            # only assert a registry lease if it existed at admission.
            if (
                source_context_epoch(self) != request_epoch
                or (
                    account.identity_id, account.provider_id,
                    account.credential_id, account.unique_id,
                ) != request_scope
                or (
                    getattr(session, "provider_id", None),
                    getattr(session, "credential_id", None),
                    getattr(session, "connected", None),
                ) != request_session_fields
                or (registered_at_read and self._sessions.get(session_key) is not session)
            ):
                raise ProviderConfigurationError("loan_inventory_scope_changed")
        # Never silently rewrite a supplier-provided different identity into
        # the selected account. Validate the entire batch before mutation,
        # caching or entitlement persistence; provider exceptions remain closed.
        if type(rows) is not list or any(
            type(row) is not Loan
            or (row.identity_id is not None and row.identity_id != account.identity_id)
            or row.source_provider != account.provider_id
            or row.source_credential != account.credential_id
            or (row.source_account_id is not None
                and row.source_account_id not in (
                    account.unique_id,
                    "primary" if account.provider_id == "paris" and not account.unique_id
                    else account.unique_id,
                ))
            for row in rows
        ):
            raise ProviderConfigurationError("Inventaire de prêts : périmètre de compte non conforme")
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
        account, epoch = self.current_account, self._account_context_epoch
        rows = self._loans_for_account(account, force=force)
        # Scoped reads may finish after a UI selection (including A→B→A).
        # Cache the exact source, but publish only to the original UI context.
        with self._search_generation_lock:
            current = (self._account_context_epoch == epoch
                       and self.current_account is account)
            if current:
                self.last_loans = rows
        self.diagnostics.add("CACHE", "loans", detail={"count": len(rows), "identity": account.identity_id})
        if force and current:
            self.refresh_adept_inventory(account.identity_id)
        return rows

    def refresh_identity_loans(self, force: bool = False, identity_id: str | None = None) -> list[Loan]:
        identity_id = identity_id or (self.current_identity.identity_id if self.current_identity else None)
        request_epoch = self._account_context_epoch
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
        # A worker for Alice may finish after switching Alice -> Bob -> Alice.
        # Its private result is returned to the guarded caller, but must never
        # overwrite the current profile projection after an epoch transition.
        with self._search_generation_lock:
            if (self._account_context_epoch == request_epoch
                    and getattr(self.current_identity, "identity_id", None) == identity_id):
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

    def catalogue_source_capabilities(self, identity_id: str | None = None):
        """Read-only inventory; search is not a declaration of catalogue sync."""
        from .core.catalogue_source_inventory import catalogue_source_capabilities
        return catalogue_source_capabilities(self, identity_id=identity_id)

    def capability_matrices(self, identity_id: str | None = None):
        """Read-only per-source capability matrices for an exact known identity.

        Code and connection state are not live provider proof or permission
        to borrow, acquire, transfer or refresh a catalogue.
        """
        from .core.capability_inventory import capability_matrices_for_identity
        return capability_matrices_for_identity(self, identity_id=identity_id)

    def capability_matrices_with_precollected_evidence(
        self, identity_id: str | None = None, *,
        sync_sources=(), loan_snapshots=(), rights_snapshots=(),
    ):
        """Project precollected Sync/loan/rights read states without provider I/O.

        Snapshots must have been bound to the exact active identity/credential
        by the caller. A displayed capability never grants an action.
        """
        from .core.capability_snapshot_inventory import (
            capability_matrices_with_precollected_evidence,
        )
        return capability_matrices_with_precollected_evidence(
            self, identity_id=identity_id,
            sync_sources=sync_sources, loan_snapshots=loan_snapshots,
            rights_snapshots=rights_snapshots,
        )

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

    @staticmethod
    def filter_attested_search_results(
        rows: list[dict[str, Any]], age: int | None = None, *,
        age_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        """Opt-in offline youth-age filter without changing historical signatures.

        Call on the loaded rows from search_known_catalog or library search;
        records with unknown ages remain visible if age is None.
        """
        from .core.attested_age_filter import filter_attested_age
        return list(filter_attested_age(rows, age, age_kind=age_kind))

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

    def enrich_numilog_search_result(self, item: SearchResult, *, expected_identity_id: str) -> SearchResult:
        """User-triggered metadata-only Numilog enrichment; never auto-sync."""
        from .core.numilog_explicit_enrichment import enrich_numilog_result_once
        return enrich_numilog_result_once(
            self, item, expected_identity_id=expected_identity_id,
        )

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

    def renew(self, loan: Loan, *, confirmed: bool = False) -> dict[str, Any]:
        """Manual Paris renewal; at-most-once remote POST per exact loan.

        A GUI button, a cached CanRenew flag or a truthy confirmation is not
        sufficient. Preserve the existing cross-process loan mutation lock
        shared with provider/ADEPT return; no operation is retried on timeout.
        """
        if confirmed is not True:
            return {
                "status": "blocked", "code": "renewal_confirmation_required",
                "provider_request_accepted": False, "provider_verified": False,
                "retry": "explicit_only",
            }
        if (type(loan) is not Loan
                or type(loan.source_provider) is not str
                or type(loan.id) is not str or not loan.id
                or type(loan.source_credential) is not str
                or type(loan.source_account_id) is not str):
            return {
                "status": "blocked", "code": "loan_scope_unverified",
                "provider_request_accepted": False, "provider_verified": False,
                "retry": "explicit_only",
            }
        from .core.provider_loan_renewal import renew as scoped_renew
        try:
            with InterProcessLock(self._return_operation_lock_file(loan)):
                return scoped_renew(self, loan)
        except LockBusyError:
            return {
                "status": "blocked", "code": "loan_action_busy",
                "provider_request_accepted": False, "provider_verified": False,
                "retry": "explicit_only",
            }

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
        return loan_return_verification.verify_return_digital(self, loan, _TRACE_LOG=_TRACE_LOG)

    def verify_paris_return_inventory(self, loan: Loan) -> dict[str, str]:
        """Fresh exact-account read after ADEPT; never send ReturnDigitalLoan."""
        return loan_return_verification.verify_paris_return_inventory(self, loan, AttentionRequired=AttentionRequired, _TRACE_LOG=_TRACE_LOG)

    def _adobe_return_inventory_confirmed(self, acquisition: dict[str, Any]) -> bool:
        """Crash recovery: ADEPT inventory check ONLY, never replay mutation."""
        return loan_return_verification.adobe_return_inventory_confirmed(self, acquisition)

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
        """Adobe-first, then Paris inventory; never automatically send PNB POST."""
        import uuid
        from .core.loan_return_guard import return_key, return_ref
        from .core.return_adept_orchestrator import adept_return_key, public_state
        return return_adept_orchestrator.return_loan_prefer_adept(
            self, loan, uuid=uuid, return_ref=return_ref,
            adept_return_key=adept_return_key, public_state=public_state,
            AttentionRequired=AttentionRequired, _TRACE_LOG=_TRACE_LOG,
        )

    def verify_loan_return(self, loan: Loan) -> dict[str, Any]:
        """Read-only first inspection; restart recovery never mutates ADEPT."""
        from .core.return_adept_orchestrator import adept_return_key
        if str(loan.source_provider).lower() != "paris" or loan.medium != "digital":
            return {"status": "not_requested", "scope": "adobe_first_paris"}
        try:
            account, _session, _key = self._return_loan_identity(loan)
        except AttentionRequired as exc:
            return {
                "status": "failed", "scope": "adobe_first_paris",
                "code": return_adept_orchestrator.return_preflight_code(exc),
            }
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
        """PNB provider return with an at-most-once POST until verified."""
        return provider_loan_return.return_digital(
            self, loan, explicit_retry=explicit_retry, _TRACE_LOG=_TRACE_LOG,
        )



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
        return adept_inventory_reconciliation.reconcile_adept_acquisition(
            self, row, profile_references=profile_references, cache=cache,
            match_local_book=match_local_book, AttentionRequired=AttentionRequired, time=time,
        )

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
        return adept_acquisition_return.return_adept_acquisition(
            self, acquisition_id, AttentionRequired=AttentionRequired,
            ProviderConfigurationError=ProviderConfigurationError,
            DrmMutationRequest=DrmMutationRequest, time=time,
        )

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
        placement_provider_id: str | None = None,
    ) -> dict[str, Any]:
        """Import a user ACSM without deleting the original file."""
        return manual_acsm_import.import_acsm(
            self, source, identity_id=identity_id, provider_id=provider_id, title=title,
            placement_provider_id=placement_provider_id,
            Path=Path, ProviderConfigurationError=ProviderConfigurationError,
            sha256_file=sha256_file, hashlib=hashlib, data_dir=data_dir, shutil=shutil,
            detect_acsm_format=detect_acsm_format, AdeptProfileResolutionError=AdeptProfileResolutionError,
            inspect_artifact=inspect_artifact, os=os,
        )

    def import_acsm(
        self, source: Path, *, identity_id: str | None = None, provider_id: str = "manual", title: str | None = None,
        placement_provider_id: str | None = None,
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
                    from .core.numilog_startup_diagnostics import adept_stage
                    with adept_stage(provider_id) as startup_trace:
                        result = self._import_acsm_impl(source, identity_id=iid, provider_id=provider_id,
                                                        title=title, placement_provider_id=placement_provider_id)
                        if startup_trace is not None and result.get("status") not in {"complete", "unchanged"}:
                            startup_trace.fail("adept_failed")
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
        except ProviderConfigurationError:
            # Adapter discovery can fail with executable paths, profile names
            # or provider-controlled messages. Public runtime state must not
            # echo raw exception text to Settings, Diagnostics or cache.
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
                "error": {"code": "runtime_plugin_unavailable", "message": "Runtime Kobo-ADEPT indisponible"},
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
        """Can this *exact* remote offer show a consented direct-download action?

        An unauthenticated Kobo catalogue owned=True flag is not a purchase.
        Require active, identity/credential/item-bound Core rights before
        inspecting the provider's executable acquisition capabilities.
        This read-only check never refreshes, downloads or writes rights.
        """
        if isinstance(item.metadata, dict) and item.metadata.get("search_provenance") == "known_catalog_stale":
            return False
        try:
            if item.source_provider in {"numilog", "numilog-catalog"}:
                # A private Numilog session has direct_download=True, but that
                # capability is NOT a per-notice download offer. Public search
                # results (including those returned from a logged-in account's
                # scope=catalog) have no purchase/variant authority.
                if item.source_provider != "numilog":
                    return False
                if (item.source_collection != "library"
                        or item.scenario != "NUMILOG_LIBRARY"
                        or item.owned is not True
                        or item.downloadable is not True
                        or item.download_url is not None
                        or item.medium != "digital"
                        or type(item.source_credential) is not str
                        or not item.source_credential
                        or item.source_credential == "__public__"):
                    return False
                from .core.numilog_manifest_rights import _scoped_record_id
                if _scoped_record_id(item.record_id, item.source_credential) is None:
                    return False
                # A private notice from another family member's credentials
                # must not show a download on the selected profile, even when
                # the old query is still loaded during a profile transition.
                identity = getattr(getattr(self, "current_identity", None), "identity_id", None)
                if type(identity) is not str or not identity:
                    return False
                matching_accounts = [
                    account for account in self.accounts
                    if getattr(account, "provider_id", None) == "numilog"
                    and getattr(account, "credential_id", None) == item.source_credential
                ]
                if (len(matching_accounts) != 1
                        or matching_accounts[0].identity_id != identity):
                    return False
                # This is only a read-only visibility hint; the locked
                # acquisition still re-attests the exact account and edition.
            if item.source_provider == "kobo":
                identity_id = getattr(getattr(self, "current_identity", None), "identity_id", None)
                if (type(identity_id) is not str or not identity_id
                        or type(item.source_credential) is not str
                        or not item.source_credential
                        or item.source_credential == "__public__"
                        or type(item.record_id) is not str or not item.record_id
                        or item.owned is not True or item.downloadable is False):
                    return False
                if (self.kobo_personal_right_states(identity_id).get(
                    (item.source_credential, item.record_id)
                ) != "active"):
                    return False
            session = self._session_for_source(item.source_provider, item.source_credential)
            if item.source_provider == "kobo":
                # A verified right still needs an explicit Vault offer and a
                # working Adept runtime; a local file is never implied.
                if not hasattr(session, "resolve_acquisition"):
                    return False
                state = self.kobo_adept_state()
                return bool(state.get("available") and state.get("compatible") and state.get("operational"))
            if item.source_provider == "ebooksgratuits":
                # Public COPS catalogue rows can precede OPDS enrichment:
                # a generic remote/distant notice is not an EPUB offer.
                # The provider exposes the exact acquisition link and an
                # explicit downloadable flag only for qualifying Atom books.
                # Never enable a misleading action merely because the
                # provider *supports* direct downloads in general.
                link = item.download_url
                return bool(
                    session.capabilities.direct_download
                    and item.downloadable is True
                    and type(link) is str
                    and bool(link)
                    and link == link.strip()
                )
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
        # Every supported targeted admission is replayed under the same lock
        # as its provider I/O. Unknown providers must never silently ignore it.
        if _admission is not None and (
            not callable(_admission) or item.source_provider not in {"kobo", "numilog"}
        ):
            raise ProviderConfigurationError(
                "Acquisition ciblée indisponible : autorisation du compte non vérifiable"
            )
        # Numilog's account/credential is untrusted until the dedicated
        # source guard validates its persistent owner under the global lock.
        # Do not look up the session using an untrusted credential first.
        session = (
            None if item.source_provider == "numilog"
            else self._session_for_source(item.source_provider, item.source_credential)
        )
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
            from .core.numilog_direct_acquisition import acquire_numilog_direct
            return acquire_numilog_direct(
                self, item, _admission=_admission, session=session,
                InterProcessLock=InterProcessLock,
                acquisition_lock_file=acquisition_lock_file,
                LockBusyError=LockBusyError,
                ProviderConfigurationError=ProviderConfigurationError,
                hashlib=hashlib,
            )

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
        # Do not trust a persisted connection owner to attribute private
        # Numilog activity before the locked source admission succeeds.
        if item.source_provider != "numilog":
            try:
                identity_id = self.identity_for_connection(
                    item.source_provider, item.source_credential,
                ).identity_id
            except Exception:
                identity_id = self.current_identity.identity_id if self.current_identity else None
        try:
            result = (self._acquire_direct_item_impl(item, _admission=_admission) if _admission is not None
                      else self._acquire_direct_item_impl(item))
            if item.source_provider == "numilog":
                identity_id = result.metadata.get("identity_id")
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

        if item.source_provider == "numilog":
            from .core.numilog_materialization_checkpoint import index_delivered
            return index_delivered(self, self.acquire_direct_item(item), title=item.title)
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

    def resume_numilog_materialization(self, acquisition_id: str):
        """Explicitly resume indexing a delivered Numilog file, with zero HTTP."""
        from .core.numilog_materialization_checkpoint import resume
        return resume(self, acquisition_id)

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
        """Persist observed loans; never treat unverified absence as a return."""
        return library_mutation._persist_active_loans(
            self, identity_id, loans, reconcile_missing=reconcile_missing,
        )

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
        from .core.loan_safe_trace import unbound_loan_trace_ref
        detail = {"request_ref": unbound_loan_trace_ref(provider_loan_id),
                  "adept_loan_id_present": bool(binding.get("adept_loan_id")),
                  "origin_profile_id_present": bool(binding.get("adept_profile_id")),
                  "return_available": bool(binding.get("return_available"))}
        _TRACE_LOG.info("TRACE adept binding request_ref=%s adept_loan_id_present=%s origin_profile_id_present=%s return_available=%s", *detail.values())
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
            metadata = row.get("metadata")
            if type(metadata) is not dict:
                # A malformed acquisition payload must not be promoted into
                # a different same-account ADEPT return transaction.
                return {"status": "adept_return_binding_required", "return_available": False}
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
        binding = row["metadata"].get("adept_binding")
        # Historical or damaged JSON may contain an array/string instead of
        # an ADEPT binding. This is never evidence for a return action.
        if type(binding) is not dict:
            binding = {}
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
        raw_verification = binding.get("return_verification")
        raw_inventory = binding.get("inventory_status")
        raw_loan_state = binding.get("loan_state")
        if any(value is not None and type(value) is not str
               for value in (raw_verification, raw_inventory, raw_loan_state)):
            # JSON arrays/maps are unhashable; a corrupt checkpoint should
            # require explicit reconciliation rather than crash the detail UI.
            state = "verification_failed"
        elif raw_verification in {"pending", "verification_failed"}:
            state = "verification_failed"
        elif raw_loan_state == "expired":
            state = "expired"
        elif raw_inventory in {
            "unresolved", "ambiguous", "inventory_error", "binding_conflict"
        } and state != "returned":
            state = raw_inventory
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
                state == "ready" and binding.get("return_available") is True
                and self._adept_return_capability()
            ),
        }

    def reacquire_digital_loan(self, loan: Loan) -> dict[str, Any]:
        """One terminal, closed diagnostic for each *explicit* user recovery.

        This wrapper never turns a failed action into success, retries it
        or creates an acquisition. The closed terminal is stable while raw
        observed exceptions remain available in the Core journal. The
        existing guarded Core orchestration remains the single executor.
        """
        from .core.diagnostic_loan_download import CODES
        from .core.public_provider_diagnostics import registered_public_provider_id
        started = time.monotonic()

        def emit(status: str, code: str, *, phase: str = "terminal") -> None:
            try:
                provider = registered_public_provider_id(
                    self.registry, getattr(loan, "source_provider", None),
                )
                self.diagnostics.add_core_loan_download({
                    "schema_version": 1, "provider_id": provider,
                    "operation_kind": "download_loan", "status": status,
                    "code": code if type(code) is str and code in CODES
                            else "unclassified_error",
                    # An explicit local eligibility refusal is observed at
                    # admission. Other outcomes remain terminal because their
                    # precise phase is not universally attested.
                    "phase": phase,
                    "duration_ms": trace_duration_ms(time.monotonic() - started) or 0,
                })
            except Exception:
                # Diagnostics must never change the underlying download error,
                # especially when a plugin supplies hostile properties.
                pass

        try:
            result = self._reacquire_digital_loan_impl(loan)
        except Cancelled:
            # The provider or task stopped cooperatively. Keep the original
            # cancellation signal and use the existing terminal journal code,
            # never misreport a provider failure or a completed download.
            emit("cancelled", "operation_cancelled")
            raise
        except AttentionRequired as exc:
            # The typed terminal event remains a stable business outcome.
            # Preserve the original observed exception in the raw journal;
            # truncating it to a closed reason destroys provider evidence.
            _TRACE_LOG.warning(
                "TRACE explicit loan recovery attention required provider=%s loan_id=%s",
                getattr(loan, "source_provider", None), getattr(loan, "id", None),
                exc_info=True,
            )
            try:
                code = exc.code
            except Exception:
                code = None
            emit("attention_required", code)
            raise
        except _LoanDownloadAdmissionRefused as exc:
            # Only the explicit rejected command is observed here, not a
            # provider GET. Keep the actual loan scope in the raw journal;
            # the typed business terminal remains a closed, single event.
            _TRACE_LOG.warning(
                "TRACE explicit loan recovery admission refused provider=%s loan_id=%s code=%s",
                getattr(loan, "source_provider", None),
                getattr(loan, "id", None), exc.code,
                exc_info=True,
            )
            emit("refused", exc.code, phase="admission")
            raise
        except ProviderConfigurationError:
            _TRACE_LOG.error(
                "TRACE explicit loan recovery provider failure provider=%s loan_id=%s",
                getattr(loan, "source_provider", None), getattr(loan, "id", None),
                exc_info=True,
            )
            emit("failed", "provider_failure")
            raise
        except Exception:
            _TRACE_LOG.error(
                "TRACE explicit loan recovery unexpected failure provider=%s loan_id=%s",
                getattr(loan, "source_provider", None), getattr(loan, "id", None),
                exc_info=True,
            )
            emit("failed", "unclassified_error")
            raise
        else:
            status = result.get("status") if type(result) is dict else None
            if status == "complete":
                emit("success", "completed")
            elif status == "attention_required":
                candidate = result.get("code")
                emit("attention_required", candidate)
            elif status == "retryable_error":
                candidate = result.get("code")
                emit("retryable_error", candidate)
            else:
                emit("failed", "unclassified_error")
            return result

    def _reacquire_digital_loan_impl(self, loan: Loan) -> dict[str, Any]:
        """Explicit full recovery, serialized with automatic acquisitions.

        Preserve a usable previous acquisition/binding on failed re-acquisition.
        A successful re-acquisition publishes a distinct file if one exists.
        """
        # Keep the existing can_download_loan admission contract: other
        # real callers/test fixtures may override it to authorize an explicit
        # action. Only AFTER that boolean rejects the action do we inspect
        # the read-only eligibility explanation. This is local state only,
        # never an extra provider GET or a new eligibility veto.
        if not self.can_download_loan(loan):
            try:
                eligibility = self.loan_download_eligibility(loan)
            except Exception:
                # The diagnostic explanation must never change the existing
                # business refusal, including legacy overrides/mocks.
                eligibility = None
            code = eligibility.get("code") if type(eligibility) is dict else None
            if (
                type(eligibility) is not dict
                or eligibility.get("available") is not False
                or type(code) is not str
                or code not in {
                    "not_digital", "provider_unavailable",
                    "provider_disconnected", "download_unsupported",
                }
            ):
                # State can change between the boolean decision and its
                # explanation. Do not invent a more specific provider cause.
                code = "unclassified_error"
            raise _LoanDownloadAdmissionRefused(code)
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
                    "profile_disagreement": False,
                    "adept_loan_reference_disagreement": False,
                    "origin_resolution": "unknown", "sources": [], "origins": []}
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
        # A Web-observed BnF loan is not approval to fetch an ACSM. This
        # second boundary protects other callers of the automatic acquisition
        # facade even when they do not use opening_sync (#2055).
        if type(loan) is Loan and loan.source_provider == "bnf-pret":
            return {
                "status": "attention_required",
                "code": "bnf_web_explicit_only", "retry": "explicit_only",
            }
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
        # The entrypoint is also called directly by existing integrations.
        # A truthy non-bool must never act like a deliberate reacquisition
        # (especially when an ACSM may be one-use). Mirror the strict ADEPT
        # retry predicate before planning local reuse or provider staging.
        force_refresh = force_refresh is True
        started = time.monotonic()
        trace_ref = loan_trace_ref(loan, None)
        _TRACE_LOG.info(
            "TRACE download loan_auto begin request_ref=%s direct_url_present=%s",
            trace_ref, bool(loan.ebook_url),
        )
        # DRM must never inherit the currently selected identity when a provider
        # connection has no persisted owner. Keep this fail-closed at the
        # automatic acquisition boundary; unrelated UI navigation may still
        # use the historical identity_for_connection fallback.
        admission = loan_acquisition_admission.resolve(self, loan, _TRACE_LOG=_TRACE_LOG)
        if admission.refusal is not None:
            return admission.refusal
        identity, entitlement_id = admission.identity, admission.entitlement_id
        trace_ref = loan_trace_ref(loan, identity.identity_id)
        local = loan_local_preparation.prepare(
            self, loan, identity=identity, entitlement_id=entitlement_id,
            force_refresh=force_refresh, started=started,
            inspect_artifact=inspect_artifact, decide_artifact_reuse=decide_artifact_reuse,
            blocked_automatic_adept_retry=blocked_automatic_adept_retry,
            time=time, _TRACE_LOG=_TRACE_LOG,
        )
        if local.outcome is not None:
            return local.outcome
        previous = local.previous_state.row
        previous_meta = local.previous_state.metadata
        previous_path = local.previous_state.path
        legacy_acsm_artifact = local.previous_state.legacy_acsm_artifact
        decision = local.decision

        ledger_base = loan_initial_receipt.ledger_base(
            loan, entitlement_id=entitlement_id, identity_id=identity.identity_id,
        )
        self.storage.acquisition_put(
            loan_initial_receipt.acquiring_receipt(ledger_base, loan, previous_meta)
        )
        _TRACE_LOG.info("TRACE download ledger acquiring loan_ref=%s", trace_ref)
        retained_acsm: Path | None = None
        failure_executor = "provider"
        try:
            acquisition = loan_provider_delivery.acquire_provider_loan(
                self, loan, force_refresh=force_refresh, identity_id=identity.identity_id,
                time=time, _TRACE_LOG=_TRACE_LOG,
            )
            final_path = acquisition.path
            artifact_drm = "unknown"
            artifact_format: str | None = None
            artifact_protected: bool | None = None
            hook_info: Any = None
            if acquisition.kind == "acsm" and acquisition.acsm_path is not None:
                retained_acsm = acquisition.acsm_path
                failure_executor = "kobo-adept"
                preflight = loan_adept_profile_guard.check(
                    self, loan, identity=identity, ledger_base=ledger_base,
                    previous_meta=previous_meta, retained_acsm=retained_acsm,
                    trace_ref=trace_ref,
                    AdeptProfileResolutionError=AdeptProfileResolutionError,
                    adept_acsm_plan=adept_acsm_plan, _TRACE_LOG=_TRACE_LOG,
                )
                if isinstance(preflight, dict):
                    return preflight
                adept_borrower_identity_id, profile_name = preflight

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
                result, adept_binding = loan_adept_fulfillment.fulfill(
                    self, request, profile_name=profile_name,
                    adept_borrower_identity_id=adept_borrower_identity_id,
                    trace_ref=trace_ref, time=time, _TRACE_LOG=_TRACE_LOG,
                    trace_integer=trace_integer,
                )
                final_path, artifact_drm, artifact_format, artifact_protected, hook_info = adept_artifact_publication.publish(
                    result=result, adept_destination=adept_destination, identity_id=identity.identity_id,
                    acsm_format=acsm_format, loan=loan, repair_mode=repair_mode,
                    destination=destination, previous_path=previous_path,
                    Path=Path, ProviderConfigurationError=ProviderConfigurationError,
                    inspect_artifact=inspect_artifact, adept_acsm_plan=adept_acsm_plan,
                    uuid=uuid, _TRACE_LOG=_TRACE_LOG,
                )
            final_inspection, scan = loan_provider_delivery.index_local_loan_artifact(
                self, loan, identity, final_path,
                time=time, _TRACE_LOG=_TRACE_LOG,
                inspect_artifact=inspect_artifact,
                ProviderConfigurationError=ProviderConfigurationError,
            )
            # Receipt payload is now a pure projection; retain the storage
            # write, cleanup, diagnostics and response in the original order.
            self.storage.acquisition_put(
                loan_completion_receipt.completed_receipt(
                    ledger_base=ledger_base, scan=scan,
                    acquisition_kind=acquisition.kind, final_path=final_path,
                    loan=loan, force_refresh=force_refresh, decision=decision,
                    previous_path=previous_path,
                    adept_binding=adept_binding if acquisition.kind == "acsm" else None,
                    artifact_drm=artifact_drm, artifact_format=artifact_format,
                    artifact_protected=artifact_protected,
                    hook_info=hook_info, final_inspection=final_inspection,
                )
            )
            if acquisition.kind == "acsm":
                self._cleanup_acsm_after_success(retained_acsm, loan_id=loan.id)
                retained_acsm = None
            if acquisition.kind == "acsm":
                self._diagnose_adept_binding(adept_binding, provider_loan_id=loan.id)
            _TRACE_LOG.info("TRACE download loan_auto complete loan_ref=%s dt=%.3fs", trace_ref, time.monotonic()-started)
            return loan_completion_receipt.completed_outcome(
                loan=loan, final_path=final_path,
                acquisition_kind=acquisition.kind,
                adept_binding=adept_binding if acquisition.kind == "acsm" else None,
                decision=decision,
            )
        except (KoboAdeptError, UserFacingError) as exc:
            if retained_acsm is not None and retained_acsm.exists():
                _TRACE_LOG.info("TRACE download acsm cleanup loan_ref=%s action=keep reason=adept_error", trace_ref)
            # A plugin's error code is not a public contract by itself.
            # Reuse the bounded diagnostic vocabulary for UI and SQLite.
            try:
                raw_code = getattr(exc, "code", None)
            except Exception:
                raw_code = None
            from .core.loan_failure_diagnostics import failure_fields
            classified = failure_fields(code=raw_code, process={}, loan_ref=None)["code"]
            code = classified if classified != "unclassified_error" else "adept_error"
            process = {}
            if failure_executor == "kobo-adept":
                raw = self.runtime_diagnostic("runtime.kobo-adept")
                process = loan_failure_evidence.process_failure_facts(
                    raw, exc=exc,
                    candidate=locals().get("adept_destination"), path_type=Path,
                )
                self.diagnostics.add("KOBO-ADEPT", "acquisition failed", "error",
                    detail=failure_fields(code=code, process=process, loan_ref=trace_ref), level="errors")
            # Never persist a provider/libgourou exception's raw text,
            # signed ACSM URL or even a plugin-controlled version string.
            # The untrusted text is inspected locally only to preserve the
            # historical named Adobe licensing classification.
            try:
                private_message = str(exc)
            except Exception:
                private_message = ""
            message = (
                "Acquisition ADEPT indisponible" if failure_executor == "kobo-adept"
                else "Acquisition du prêt indisponible"
            )
            if code == "pnb_redirect_refused":
                message = "Redirection PNB refusée ; consulter les traces PNB target_scheme / target_port"
                # Keep a genuinely numeric plugin version for remote debugging,
                # but never interpolate a plugin-controlled version string.
                try:
                    if self._plugin_manager is not None:
                        version = self._plugin_manager.status(
                            "provider." + loan.source_provider,
                        ).descriptor.version
                        if type(version) is str and len(version) <= 19 and re.fullmatch(
                            r"[0-9]{1,3}(?:\.[0-9]{1,3}){1,3}", version,
                        ):
                            message += f" (plugin {version})"
                except Exception:
                    pass
                self.diagnostics.add("PNB", "redirect refused", "warning", detail={"loan_ref": trace_ref, "code": "pnb_redirect_refused"}, level="errors")
            # A bounded subset of real process counters is useful even when
            # the runtime response text is untrusted and cannot be echoed.
            public_process = failure_fields(code=code, process=process, loan_ref=None)
            message += loan_failure_evidence.public_process_suffix(public_process)
            # This is a decoder/transport-contract failure, *not* an Adobe
            # licensing decision. The next opening may retry it automatically.
            # Never infer a named Adobe refusal from a generic CLI exit code.
            retryable = loan_failure_evidence.safe_retryable_flag(exc) or code in {
                "kobo_adept_invalid_json",
            }
            _TRACE_LOG.warning(
                "Acquisition failed loan_ref=%s executor=%s retryable=%s exit_code=%s dt=%.3fs process_present=%s",
                trace_ref, failure_executor, retryable,
                loan_failure_evidence.safe_exit_code(exc),
                time.monotonic()-started, bool(process),
            )
            status = "retryable_error" if retryable else "attention_required"
            attention_code = adept_attention_code(code, private_message, executor=failure_executor)
            metadata = loan_failure_evidence.adept_failure_metadata(
                previous_meta, loan_id=loan.id, process=process,
                retained_acsm=retained_acsm, executor=failure_executor,
                attempt_profile=locals().get("profile_name"),
            )
            self.storage.acquisition_put(
                loan_failure_evidence.adept_failure_receipt(
                    ledger_base=ledger_base, status=status, executor=failure_executor,
                    code=code, message=message, metadata=metadata,
                    attention_code=attention_code,
                )
            )
            return loan_failure_evidence.adept_failure_outcome(
                loan_id=loan.id, status=status, message=message,
                attention_code=attention_code, code=code, process=process,
            )
        except Exception as exc:
            if retained_acsm is not None and retained_acsm.exists():
                _TRACE_LOG.info("TRACE download acsm cleanup loan_ref=%s action=keep reason=acquisition_error", trace_ref)
            _TRACE_LOG.warning(
                "TRACE download provider error loan_ref=%s reason=provider_or_artifact_failure dt=%.3fs",
                trace_ref, time.monotonic()-started,
            )
            try:
                # Classification uses the provider's message only to detect a
                # known *bounded* quota code; the original text must never
                # enter a persistent receipt or a public error.
                attention_code = self._provider_attention_code(exc)
            except Exception:
                # A plugin may supply an exception with a hostile __str__.
                # Optional classification cannot break ledger finalization.
                attention_code = None
            # "Quota dépassé" from DownloadDigitalDocument is not a reliable
            # indication that the already-active loan or its ACSM is unavailable
            # (manual ACSM retrieval can still succeed). Keep it observable but
            # retryable instead of permanently blocking the loan.
            validation_failure = failure_executor == "kobo-adept" and isinstance(exc, ProviderConfigurationError)
            metadata = loan_failure_evidence.provider_failure_metadata(
                previous_meta, loan_id=loan.id, retained_acsm=retained_acsm,
                attention_code=attention_code, validation_failure=validation_failure,
            )
            self.storage.acquisition_put(
                loan_failure_evidence.provider_failure_receipt(
                    ledger_base=ledger_base, executor=failure_executor,
                    validation_failure=validation_failure, metadata=metadata,
                )
            )
            return loan_failure_evidence.provider_failure_outcome(
                loan_id=loan.id, attention_code=attention_code,
            )

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
            "TRACE download acquire provider-session request_ref=%s upstream_uid_present=%s",
            loan_trace_ref(loan, None), bool(account.unique_id),
        )
        # Digital provider payloads are staged outside ~/EBooks. In particular,
        # ACSM files are transient inputs and must not become user-library items.
        identity = self.identity_for_connection(loan.source_provider, loan.source_credential)
        trace_ref = loan_trace_ref(loan, identity.identity_id)
        _TRACE_LOG.info("TRACE download acquire admitted request_ref=%s loan_ref=%s",
                        loan_trace_ref(loan, None), trace_ref)
        library_target_dir = self.library_identity_provider_dir(identity.identity_id, loan.source_provider)
        target_dir = self._acquisition_work_dir(identity.identity_id, loan.source_provider, loan.id)
        cached = None if force_refresh else acsm_cache.reuse(target_dir, loan, identity.identity_id, account_key)
        if cached is not None:
            path, url = cached, loan.ebook_url or ""
            self.diagnostics.add("PNB", "reuse artifact", detail={"loan_ref": trace_ref, "cached": True})
        else:
            _TRACE_LOG.info("TRACE download provider-download begin loan_ref=%s", trace_ref)
            path, url = session.download_digital(loan, account, target_dir)
        _TRACE_LOG.info(
            "TRACE download provider-download end loan_ref=%s suffix=%s bytes=%s duration_ms=%s",
            trace_ref, path.suffix.casefold() if path.suffix.casefold() in {".acsm", ".epub", ".pdf"} else "unknown",
            trace_integer(path.stat().st_size if path.is_file() else 0, minimum=0, maximum=2**63-1),
            trace_duration_ms(time.monotonic()-started),
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
            detail={"loan_ref": trace_ref, "bytes": trace_integer(size, minimum=0, maximum=2**63-1)},
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
