Warning: truncated output (original token count: 53173)
Total output lines: 4059

from __future__ import annotations

from .core import acsm_staging, loan_failure_evidence
from .core import adept_artifact_publication
from .core import loan_acquisition_admission, loan_local_preparation
from .core import loan_provider_delivery
from .core import loan_completion_receipt, loan_initial_receipt
from .core import loan_adept_profile_guard
from .core import manual_acsm_import
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


def _cached_loans_for_exact_account(
    value: object, account: Account, *, descriptor=None,
) -> list[Loan] | None:
    """Treat cached rows as untrusted: never rewrite their source/identity.

    A cache hit is only usable if every row still identifies the same
    provider, credential, account and identity. Corrupt/legacy rows trigger
    an ordinary fresh provider read, never a cross-account display.
    """
    if type(value) is not list or len(value) > 10_000:
        return None
    from .plugin_api.loan_scope import loan_matches_account
    # Exact account IDs need no alias convention. A different observed ID
    # requires an attestation from this installed provider descriptor, not
    # an implicit Paris/default-primary fallback encoded in Core.
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
            and (row.source_account_id == account.unique_id
                 or loan_matches_account(row, account, descriptor))
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
        # Runtime-only boundary: an old opening run is history, not a
        # current-startup incident. Set before any initialization work.
        self._dashboard_launch_at = int(time.time())
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
        # Baseline by identity and run ID avoids same-second restart ambiguity.
        # A persisted run present at launch remains historic even if its
        # started_at equals the first run of this process.
        self._dashboard_initial_run_ids = {
            identity.identity_id: (
                (row or {}).get("sync_run_id")
                if (row := self.storage.latest_sync_run(identity.identity_id)) else None
            )
            for identity in self.identities
        }
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

    def manual_borrow_policy(self, item: SearchResult):
        """Static installed-plugin manual-borrow ABI; never a permission."""
        from .core.manual_borrow_contract import resolved_manual_borrow_policy
        return resolved_manual_borrow_policy(self, item)

    def can_borrow_manual(self, item: SearchResult) -> bool:
        """Generic inert availability, with exact provider-owned scope."""
        from .core.manual_borrow_contract import manual_borrow_action_available
        return manual_borrow_action_available(self, item)

    def borrow_manual(
        self, item: SearchResult, *, expected_identity_id: str,
        confirmed: bool = False, expected_context_epoch: int | None = None,
        expected_account: Account | None = None,
    ) -> dict[str, object]:
        """Only the durable, one-shot Core borrower may issue a provider POST."""
        from .core.manual_borrow_contract import manual_borrow_action
        return manual_borrow_action(
            self, item, expected_identity_id=expected_identity_id,
            confirmed=confirmed, expected_context_epoch=expected_context_epoch,
            expected_account=expected_account,
        )

    def resolve_cross_profile_lender_placement(
        self, *, requesting_identity_id: str,
        borrowing_account: Account, exact_notice: SearchResult,
    ):
        """#4987: read-only exact completed placement owned by lender A."""
        from .core.borrow_for_identity_placement_resolution import (
            resolve_cross_profile_lender_placement,
        )
        return resolve_cross_profile_lender_placement(
            self, requesting_identity_id=requesting_identity_id,
            borrowing_account=borrowing_account, exact_notice=exact_notice,
        )

    def send_confirmed_lender_local_copy(
        self, *, requesting_identity_id: str,
        borrowing_account: Account, exact_notice: SearchResult,
        confirmed: bool = False,
    ) -> dict[str, object]:
        """#4987: explicit A-to-B copy from an attested completed local loan."""
        from .core.borrow_for_identity_send_composition import (
            send_confirmed_lender_local_copy,
        )
        return send_confirmed_lender_local_copy(
            self, requesting_identity_id=requesting_identity_id,
            borrowing_account=borrowing_account, exact_notice=exact_notice,
            confirmed=confirmed,
        )

    def preflight_cross_profile_loan_transfer(
        self, *, requesting_identity_id: str,
        borrowing_account: Account, exact_notice: SearchResult,
        lender_placement_id: str,
    ):
        """#4987: read-only local placement check for explicit 'Envoyer à'."""
        from .core.borrow_for_identity_transfer_preflight import (
            preflight_cross_profile_loan_transfer,
        )
        return preflight_cross_profile_loan_transfer(
            self, requesting_identity_id=requesting_identity_id,
            borrowing_account=borrowing_account, exact_notice=exact_notice,
            lender_placement_id=lender_placement_id,
        )

    def send_cross_profile_borrowed_placement(
        self, *, requesting_identity_id: str,
        borrowing_account: Account, exact_notice: SearchResult,
        lender_placement_id: str, confirmed: bool = False,
    ) -> dict[str, object]:
        """#4987: consented A-to-B copy through canonical 'Envoyer à'."""
        from .core.borrow_for_identity_transfer_executor import (
            send_cross_profile_borrowed_placement,
        )
        return send_cross_profile_borrowed_placement(
            self, requesting_identity_id=requesting_identity_id,
            borrowing_account=borrowing_account, exact_notice=exact_notice,
            lender_placement_id=lender_placement_id, confirmed=confirmed,
        )

    def existing_lender_loan_candidates(
        self, *, exact_notice: SearchResult,
    ):
        """#4988: previous exact lender loans, no fresh borrow/GET/POST.

        A confirmed loan can finish delivery later. An already-spent loan
        claim is NOT a new borrower-eligibility offer; this read-only view
        exposes no source path, private account identifier or permission.
        """
        from .core.borrow_for_identity_existing_loan import (
            existing_lender_loan_candidates,
        )
        return existing_lender_loan_candidates(self, exact_notice=exact_notice)

    def cross_profile_borrow_progress(
        self, *, requesting_identity_id: str,
        borrowing_account: Account, exact_notice: SearchResult,
    ):
        """#4662: observe only the lender's durable loan claim.

        A confirmed loan is separate from an unverified transfer; this
        accessor never borrows again, fulfills ADEPT or sends a local copy.
        """
        from .core.borrow_for_identity_progress import (
            observed_cross_profile_borrow_progress,
        )
        return observed_cross_profile_borrow_progress(
            self, requesting_identity_id=requesting_identity_id,
            borrowing_account=borrowing_account, exact_notice=exact_notice,
        )

    def preview_borrow_for_current_identity(
        self, *, expected_edition_id: str, observations: object,
        now: int | None = None,
    ):
        """#4662: exact lender opportunities from observed facts, read-only.

        No account switch, provider I/O, loan, copy, or action authorization.
        Callers must not render executable buttons from this advisory DTO.
        """
        from .core.borrow_for_identity_live import (
            preview_live_borrow_for_current_identity,
        )
        return preview_live_borrow_for_current_identity(
            self, expected_edition_id=expected_edition_id,
            observations=observations, now=now,
        )

    def revalidate_borrow_lender_choice(
        self, *, captured_preview, selected_option_id: str,
        expected_edition_id: str, observations: object,
        now: int | None = None,
    ):
        """#4662: revoke a stale explicit lender choice, without lending.

        The returned selection is never an executor ticket or a permission
        to copy a loan to a different reader; consent/claims remain separate.
        """
        from .core.borrow_for_identity_live import (
            revalidate_live_borrow_lender_selection,
        )
        return revalidate_live_borrow_lender_selection(
            self, captured_preview=captured_preview,
            selected_option_id=selected_option_id,
            expected_edition_id=expected_edition_id,
            observations=observations, now=now,
        )

    def prepare_cross_profile_lender_action_plan(
        self, *, captured_preview, selected_option_id: str,
        expected_edition_id: str, observations: object,
        now: int | None = None,
    ):
        """#4987: prepare an exact *inert* lender plan for a later consent.

        The plan cannot borrow, transfer, switch identities or authorize a
        copy. Never treat its status as sufficient authority for a POST.
        """
        from .core.borrow_for_identity_action_plan import (
            prepare_cross_profile_lender_action_plan,
        )
        return prepare_cross_profile_lender_action_plan(
            self, captured_preview=captured_preview,
            selected_option_id=selected_option_id,
            expected_edition_id=expected_edition_id,
            observations=observations, now=now,
        )

    def execute_cross_profile_borrow(
        self, *, captured_preview, selected_option_id: str,
        expected_edition_id: str, observations: object,
        confirmed: bool = False, now: int | None = None,
    ) -> dict[str, object]:
        """#4987: exact one-shot lender loan; no implicit identity switch/copy."""
        from .core.borrow_for_identity_executor import (
            execute_cross_profile_borrow,
        )
        return execute_cross_profile_borrow(
            self, captured_preview=captured_preview,
            selected_option_id=selected_option_id,
            expected_edition_id=expected_edition_id,
            observations=observations, confirmed=confirmed, now=now,
        )

    def manual_borrow_diagnostic(self, item: SearchResult) -> dict[str, object]:
        """Read-only scoped admission from the installed provider policy."""
        from .core.manual_borrow_contract import manual_borrow_action_diagnostic
        return manual_borrow_action_diagnostic(self, item)

    def manual_borrow_followup_available(self, item: SearchResult) -> bool:
        """A spent claim may be inspected, never submitted again."""
        from .core.borrow_later_verification import borrow_followup_available
        return borrow_followup_available(
            self, item, readiness=self.manual_borrow_diagnostic,
        )

    def verify_manual_borrow_followup(self, item: SearchResult) -> dict[str, object]:
        """Independently observe a spent claim without mutation or entitlement."""
        from .core.borrow_later_verification import verify_pending_borrow_followup
        return verify_pending_borrow_followup(
            self, item, readiness=self.manual_borrow_diagnostic,
        )

    def verify_borrow_without_submission(
        self, item: SearchResult, *, expected_identity_id: str,
        expected_account: Account, expected_operation_id: str,
        expected_provider_id: str, expected_docbase: str,
    ) -> dict[str, object]:
        """Read-only follow-up of one uncertain provider manual borrow claim.

        Does not submit, import or confirm an entitlement; never frees claim.
        """
        from .core.borrow_later_verification import (
            verify_borrow_without_submission,
        )
        return verify_borrow_without_submission(
            self, item, expected_identity_id=expected_identity_id,
            expected_account=expected_account,
            expected_operation_id=expected_operation_id,
            expected_provider_id=expected_provider_id,
            expected_docbase=expected_docbase,
        )

    def stage_explicit_loan_acsm(
        self, loan: Loan, *, confirmed: bool = False,
        expected_identity_id: str = "",
    ) -> dict[str, str]:
        """Deliberate provider-selected ACSM candidate, never automatic download.

        Caller must capture the exact identity before its UI confirmation and
        pass it back. The Core revalidates this owner before and after HTTP.
        No entitlement, acquisition, ADEPT or borrowed-state mutation.
        """
        from inspect import getattr_static, isfunction
        from re import fullmatch
        from .core.diagnostic_explicit_loan_action import (
            ActionObservation, LoanActionError, valid_stage_code,
        )
        from .core.explicit_loan_acsm import stage_explicit_loan_acsm

        # Derive only a *bounded* diagnostic vocabulary from the exact Loan.
        # Provider capability, not a Core provider-ID dispatch, grants this path.
        provider_id = loan.source_provider if type(loan) is Loan else None
        valid_provider = (type(provider_id) is str and
                          fullmatch(r"[a-z][a-z0-9-]{0,63}", provider_id) is not None)
        prefix = provider_id.split("-", 1)[0] if valid_provider else None
        def reason(suffix):
            return prefix + "_" + suffix if prefix else "loan_action_failed"

        observation = ActionObservation(
            self, 'stage_loan_token', provider_id=provider_id,
        )
        try:
            # The Core validates the exact origin and consent before dispatch:
            # an invalid scope never invokes a private session getter.
            # The plugin supplies the branch/reasons;
            # the shared Core still owns the lock, lease and ACSM inspection.
            if confirmed is not True:
                raise LoanActionError(reason("explicit_confirmation_required"))
            if (type(loan) is not Loan or not valid_provider
                    or loan.medium != "digital" or loan.state != "active"
                    or type(loan.source_credential) is not str
                    or not loan.source_credential
                    or type(loan.source_account_id) is not str
                    or not loan.source_account_id
                    or type(loan.identity_id) is not str or not loan.identity_id
                    or type(loan.id) is not str or not loan.id
                    or type(loan.record_id) is not str or not loan.record_id
                    or type(expected_identity_id) is not str
                    or expected_identity_id != loan.identity_id):
                raise LoanActionError(reason("loan_scope_unproven"))
            try:
                session = self._session_for_source(
                    loan.source_provider, loan.source_credential,
                )
            except Exception:
                raise LoanActionError(reason("loan_scope_unproven")) from None
            profile = getattr_static(
                type(session), "loan_acsm_stage_profile", None,
            )
            activity_reason = getattr_static(
                type(session), "loan_stage_activity_reason", None,
            )
            candidate_reason = getattr_static(
                type(session), "loan_stage_candidate_reason", None,
            )
            if (type(profile) is not tuple or len(profile) != 2
                    or type(profile[0]) is not str or not profile[0]
                    or type(profile[1]) is not tuple or len(profile[1]) != 5
                    or any(type(code) is not str or not valid_stage_code(code, loan.source_provider)
                           for code in profile[1])
                    or not isfunction(activity_reason)
                    or not isfunction(candidate_reason)):
                raise LoanActionError(reason("loan_token_unavailable"))
            reason_codes = dict(zip(
                ("confirmation", "scope", "unavailable", "invalid", "busy"),
                profile[1],
            ))
            result = stage_explicit_loan_acsm(
                self, loan, confirmed=confirmed,
                expected_identity_id=expected_identity_id,
                interprocess_lock=InterProcessLock,
                lock_path=acquisition_lock_file, busy_error=LockBusyError,
                inspect_artifact=inspect_artifact,
                expected_provider_id=loan.source_provider,
                selected_branch=profile[0],
                action_error_type=LoanActionError,
                error_codes=reason_codes,
                classify_activity_error=lambda error: activity_reason(
                    session, error,
                ),
                classify_candidate_error=lambda error: candidate_reason(
                    session, error,
                ),
                observe_phase=observation.at,
            )
        except LoanActionError as exc:
            observation.emit('failed', exc.code)
            raise
        except Exception as exc:
            code = reason('local_staging_failed' if isinstance(exc, OSError) else 'action_failed')
            observation.emit('failed', code)
            raise LoanActionError(code) from None
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
        from .core.diagnostic_explicit_loan_action import ActionObservation, LoanActionError
        observation = ActionObservation(self, 'import_acsm', provider_id='bnf-pret')
        try:
            if (type(expected_identity_id) is not str or not expected_identity_id
                    or self.current_identity is None
                    or self.current_identity.identity_id != expected_identity_id):
                raise LoanActionError('bnf_loan_scope_unproven')
            if (type(loan) is not Loan or loan.source_provider != 'bnf-pret'
                    or loan.identity_id != expected_identity_id
                    or loan.medium != 'digital' or loan.state != 'active'
                    or not loan.id or not loan.record_id or not loan.source_credential
                    or not loan.source_account_id
                    or self.storage.identity_for_connection(
                        'bnf-pret', loan.source_credential) != expected_identity_id):
                raise LoanActionError('bnf_loan_scope_unproven')
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
                    raise LoanActionError('bnf_loan_scope_unproven')
            except LoanActionError:
                raise
            except Exception:
                raise LoanActionError('bnf_loan_scope_unproven') from None
            from .core.observed_loan_scope import observed_loan
            from .core.observed_import_provenance import remember_observed_import
            display_scope = observed_loan(loan)
            observation.at('adept_import')
            result = self.import_acsm(source, identity_id=expected_identity_id,
                                      provider_id='manual', title=title,
                                      placement_provider_id='bnf-pret')
            remember_observed_import(
                self, result, display_scope, provider_id='bnf-pret',
                provenance_field='bnf_observed_loan',
            )
        except LoanActionError as exc:
            observation.emit('failed', exc.code)
            raise
        except Exception:
            observation.emit('failed', 'bnf_import_failed')
            raise LoanActionError('bnf_import_failed') from None
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

        # Reusable for legacy tests and migration hosts without service methods.
        LibraryService._project_configured_provider_connections(self)

    def _project_configured_provider_connections(self, *, descriptors=()) -> None:
        """Publish only account bindings admitted at the present plugin stage.

        Bootstrap has no descriptors. Ambiguous scopes stay in Config but
        receive no *new* SQLite connection binding before plugin validation.
        Reattachment validates against the exact future provider descriptors.
        """
        from .core.provider_account_limits import (
            ProviderAccountLimitError, projectable_provider_connections,
            validate_provider_account_limits,
        )
        default_identity = self.identities[0].identity_id
        known = tuple(identity.identity_id for identity in self.identities)
        configured = (
            self.config.provider_accounts()
            if hasattr(self.config, "provider_accounts")
            else self.config.get("providers.accounts", [])
        )
        try:
            registry = getattr(self, "registry", None)
            descriptors = tuple(descriptors) or (
                tuple(registry.descriptors()) if registry is not None else ()
            )
            if descriptors:
                validate_provider_account_limits(
                    configured, descriptors,
                    default_identity=default_identity,
                    known_identities=known,
                )
            rows = projectable_provider_connections(
                configured,
                default_identity=default_identity,
                known_identities=known,
                admitted_provider_ids=(
                    descriptor.id for descriptor in descriptors
                    if getattr(descriptor, "state", None) == "ready"
                ),
            )
        except ProviderAccountLimitError as exc:
            raise ProviderConfigurationError(str(exc)) from None
        limits = {
            descriptor.id.strip().lower(): descriptor.max_accounts_per_identity
            for descriptor in descriptors
 …33173 tokens truncated… code = "unclassified_error"
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
                    "adobe_loan_state_disagreement": False,
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
                # The repaired previous artifact is still needed when
                # indexing or the completed SQLite ledger write fails.
                # Cleanup is noncritical and runs only after both succeed.
                adept_artifact_publication.cleanup_after_commit(
                    previous_path=previous_path, final_path=final_path,
                    repair_mode=repair_mode, Path=Path, loan=loan,
                    identity_id=identity.identity_id, _TRACE_LOG=_TRACE_LOG,
                )
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
        except UserFacingError as exc:
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
        """Synchronize one exact account/source without changing account selection."""
        return sync_coordination._synchronize_account_source_impl(
            self, account, deep=deep, control=control,
        )

    def synchronize_account_source(self, account: Account, *, deep: bool = False, control: Any = None) -> dict[str, Any]:
        return sync_coordination.synchronize_account_source(
            self, account, deep=deep, control=control,
        )

    def _synchronize_account_source_inventory_impl(self, account: Account, *, deep: bool = False, control: Any = None) -> dict[str, Any]:
        """Core-only, strictly metadata/rights observation; never materialize."""
        return sync_coordination._synchronize_account_source_impl(
            self, account, deep=deep, control=control, inventory_only=True,
        )

    def _synchronize_scheduled_account_inventory(self, account: Account, *, deep: bool = False, control: Any = None) -> dict[str, Any]:
        """Dedicated scheduled path; never expose inventory-only as public Sync ABI."""
        return sync_coordination.synchronize_account_source(
            self, account, deep=deep, control=control, inventory_only=True,
        )

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

    def synchronize_selected_edition(
        self, *, provider_id: str, credential_id: str, edition_id: str,
        explicit_user_action: bool = False,
        cancelled: Callable[[], bool] = lambda: False,
        result_schema: str = "core.selected-canonical/1",
    ) -> dict[str, object]:
        """One exact selected edition through the provider-neutral A→B engine.

        This explicitly consented operation never derives an executable offer
        from a catalogue flag, backup row or legacy direct GET.
        """
        from .core.canonical_selected_edition import synchronize_selected_edition
        return synchronize_selected_edition(
            self, provider_id=provider_id, credential_id=credential_id,
            edition_id=edition_id, explicit_user_action=explicit_user_action,
            cancelled=cancelled, result_schema=result_schema,
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
        def forget_receipts(used_path):
            acsm_cache.forget(used_path)
            acsm_cache.forget_initial(used_path)
        return acsm_staging.cleanup_acsm_after_success(
            path, loan_id=loan_id, forget=forget_receipts, logger=_TRACE_LOG,
        )

    def _cleanup_old_acsm_work(self, *, max_age_seconds: int | None = None) -> dict[str, int]:
        def forget_receipts(expired_path):
            acsm_cache.forget(expired_path)
            acsm_cache.forget_initial(expired_path)
        return acsm_staging.cleanup_old_acsm_work(
            self.storage.path, max_age_seconds=max_age_seconds,
            config_get=self.config.get, clock=time.time,
            forget=forget_receipts, logger=_TRACE_LOG,
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
        normal_cached = None if force_refresh else acsm_cache.reuse(
            target_dir, loan, identity.identity_id, account_key,
            claim_state=self.storage.loan_borrow_state)
        initial_cached = (None if force_refresh or normal_cached is not None
                          else acsm_cache.reuse_initial(
                              target_dir, loan, identity.identity_id, account_key,
                              claim_state=self.storage.loan_borrow_state))
        cached = normal_cached or initial_cached
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
        if cached is None or initial_cached is not None:
            self.acquisition.run_post_download(hook_item, path, url, target_dir)
            acsm_cache.remember(
                path, target_dir, loan, identity.identity_id, account_key,
                initial_claim_bound=initial_cached is not None,
                claim_state=self.storage.loan_borrow_state,
            )
        is_acsm = path.suffix.casefold() == ".acsm"
        if not is_acsm and path.is_file():
            # #5225: historical direct loans have not yet migrated to the
            # full canonical A->B receipt pipeline. At minimum never allow a
            # supplier-chosen filename to overwrite an unrelated library
            # artifact, or escape this Core-owned private staging directory.
            # The common publication helper creates an exclusive destination
            # on the library filesystem, independently of the source mount.
            from .core.loan_direct_publication import (
                publish_direct_loan_candidate,
            )
            path = publish_direct_loan_candidate(
                path, work_dir=target_dir,
                library_target_dir=library_target_dir,
            )
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
