"""#3171: Core-owned completion of a provider-selected *candidate* delivery.

Only the already held source Sync may call this. Provider plugins select one
exact signed offer and return candidate bytes, never CompletedDelivery,
SQLite authority, or an ADEPT receipt. This bridge independently validates
the final file within the current profile and witnesses a completed callback.
"""
from __future__ import annotations

from .source_context import source_context_epoch

import hashlib
from inspect import getattr_static
from typing import Callable
import json
import logging
import os
from pathlib import Path
import secrets
import stat
import shutil

from ..adept_binding import capture_binding
from ..artifact_inspection import detect_artifact_format, detect_acsm_format, inspect_artifact
from ..catalog import LocalCatalog
from ..locking import InterProcessLock
from ..models import Account
from ..paths import acquisition_lock_file
from ..plugin_api.provider_rights_observation import ProviderRightObservation
from ..plugin_api.downloadable_resources import DeliveryCandidate, SourceReference
from ..plugin_api.errors import AdeptFulfillmentNotSubmitted, RuntimeDispatchNotSubmitted
from .canonical_publication import _physical_target, _same_file
from .canonical_encryption_diagnostics import rejected_epub_encryption_profile
from .canonical_loan_borrower import pin_borrower
from .canonical_runtime_attempt import (
    begin_runtime_attempt, pending_runtime_attempt, release_unsubmitted_runtime_attempt,
)
from .canonical_adept_attempt import (
    begin_loan_adept_attempt, pending_loan_adept_attempt, release_proven_unsubmitted_attempt,
    read_historical_rearm_binding, historical_user_digest,
)
from .adept_acsm_plan import profile_is_explicitly_acsm_unready
from .adept_runtime_preflight import AdeptProfileUnavailable, require_fresh_acsm_capability
from .convergence_execution import CompletedDelivery, CanonicalDrmUnresolved
from .source_convergence import SourceKey

logger = logging.getLogger(__name__)


def _selected_runtime_recipe_source_admitted(
    service: object, recipe_source: object, expected_provider_id: str,
) -> bool:
    """Only the exact installed provider/runtime pair may execute its recipe.

    An ACSM is a technical DRM transport understood by Core; all other
    source types identify the *current* lending/purchasing provider, never an
    arbitrary second provider or a caller-named runtime. This is a negative
    gate, not permission to acquire: consent, target, runtime and bytes are
    checked separately at the execution boundary.
    """
    from .provider_identifier import valid_provider_identifier

    if type(recipe_source) is not dict:
        return False
    source_type = recipe_source.get("type")
    if type(source_type) is not str:
        return False
    if source_type == "acsm":
        return True
    if (source_type == "http"
            or not valid_provider_identifier(expected_provider_id)
            or source_type != expected_provider_id):
        return False
    # Static method lookup avoids invoking plugin properties. The Core's
    # reciprocal binding reader checks both installed manifests and their
    # current enabled/ready state; the executor repeats that check before
    # and after the runtime call. A malformed or missing proof is a veto.
    binding_reader = getattr_static(service, "runtime_acquisition_binding", None)
    if not callable(binding_reader):
        return False
    try:
        runtime_id = service.runtime_acquisition_binding(source_type)
        return type(runtime_id) is str and bool(runtime_id)
    except Exception:
        return False


def complete_selected_delivery(
    service, account: Account, session, source: SourceKey,
    offer: ProviderRightObservation, *,
    library_root: Path, shared_lock: InterProcessLock,
    consent_still_current: Callable[[], bool] | None = None,
) -> CompletedDelivery:
    """Witness candidate GET then final validated EPUB/PDF or ADEPT fulfillment.

    The executor alone admits, checks consent before/after network, journals
    this completed result and publishes B atomically. A provider response's
    (path, URL) pair is NEVER itself treated as a receipt or final Artifact.
    """
    if (type(account) is not Account or type(source) is not SourceKey
            or type(offer) is not ProviderRightObservation
            or not offer.provider_attested
            or offer.delivery != "available" or not offer.delivery_offer_id
            or (offer.edition_id, offer.right_id, offer.kind) !=
               (source.edition_id, source.right_id, source.kind)
            or not isinstance(library_root, Path)
            or type(shared_lock) is not InterProcessLock
            or shared_lock._handle is None
            or shared_lock.path.resolve() != acquisition_lock_file().resolve()):
        raise ValueError("canonical_delivery_core_admission_required")
    scope = source.scope
    coords = (scope.identity_id, scope.provider_id,
              scope.credential_id, scope.account_id)
    if (account.identity_id, account.provider_id,
            account.credential_id, account.unique_id) != coords:
        raise ValueError("canonical_delivery_scope_mismatch")
    # Optional, explicit opt-in. The existing v2/v1 candidate ABI remains
    # unchanged; only a guarded producer receives a live Core admission
    # callback, which it MUST compose at every external GET hop and chunk.
    guarded_name = "downloadable_selected_delivery_candidate_guarded"
    runtime_guarded_name = "downloadable_selected_delivery_candidate_runtime_guarded"
    guarded_reader = getattr_static(session, guarded_name, None)
    runtime_reader = getattr_static(session, runtime_guarded_name, None)
    # A declared opt-in with an invalid ABI is not an absent opt-in. Never
    # silently downgrade its source-revision-pinned GET to an older, unguarded
    # candidate method when a provider update is incomplete or malformed.
    # Passive discovery must not evaluate provider properties or issue I/O.
    if callable(consent_still_current) and any(
        value is not None and not callable(value)
        for value in (guarded_reader, runtime_reader)
    ):
        raise ValueError("canonical_delivery_guarded_contract_invalid")
    runtime_guarded = (callable(consent_still_current)
                       and callable(runtime_reader))
    guarded = (runtime_guarded or
               (callable(consent_still_current)
                and callable(guarded_reader)))
    reader_name = (
        runtime_guarded_name if runtime_guarded else
        guarded_name if guarded else
        "downloadable_selected_delivery_candidate"
        if getattr_static(session, "downloadable_selected_delivery_candidate", None) is not None
        else "rights_selected_delivery_candidate"
    )
    raw = getattr_static(session, reader_name, None)
    if raw is None or not callable(raw):
        raise ValueError("canonical_delivery_candidate_contract_unavailable")

    epoch = source_context_epoch(service)
    primary = account.is_primary
    accounts, sessions, storage = service.accounts, service._sessions, service.storage

    def bound() -> bool:
        try:
            session_accounts = session.accounts
            if (not isinstance(session_accounts, (list, tuple))
                    or sum(
                        type(x) is Account
                        and (x.identity_id, x.provider_id,
                             x.credential_id, x.unique_id) == coords
                        and x.is_primary is primary
                        for x in session_accounts
                    ) != 1):
                return False
            return (
                service.accounts is accounts and service._sessions is sessions
                and service.storage is storage
                and source_context_epoch(service) == epoch
                and (account.identity_id, account.provider_id,
                     account.credential_id, account.unique_id) == coords
                and type(primary) is bool and account.is_primary is primary
                and sum(type(x) is Account and x is account for x in accounts) == 1
                and sum(type(x) is Account and
                        (x.identity_id, x.provider_id,
                         x.credential_id, x.unique_id) == coords for x in accounts) == 1
                and sessions.get(service._session_key(scope.provider_id,
                                                       scope.credential_id)) is session
                and sum(x is session for x in sessions.values()) == 1
                and getattr(session, "connected", None) is True
                and getattr(session, "_closed", False) is False
                and storage.provider_scope_account_bound(
                    identity_id=scope.identity_id,
                    provider_id=scope.provider_id,
                    credential_id=scope.credential_id,
                    account_id=scope.account_id,
                )
                and service.sync_source_enabled(account) is True
            )
        except (AttributeError, TypeError, KeyError, ValueError):
            return False

    # Fail closed if a plugin retains this closure after completion/exception.
    admission_live = [True]

    def admission_current() -> bool:
        """Non-serializable Core permission, never created by a plugin.

        A source ON from a later OFF→ON revision cannot revive this closure;
        the caller's generation- and scope-pinned consent reader decides.
        """
        if (not admission_live[0] or shared_lock._handle is None
                or not callable(consent_still_current)):
            return False
        try:
            return bound() and consent_still_current() is True
        except Exception:
            return False

    def current() -> bool:
        # Legacy candidate adapters keep the existing Core executor's
        # post-delivery consent verdict (blocked, not transport failed).
        # Guarded adapters enforce the revocation immediately in the bridge.
        return admission_current() if guarded else bound()

    if not current():
        raise ValueError("canonical_delivery_source_rebound")
    if pending_loan_adept_attempt(storage, source):
        raise ValueError("canonical_adept_attempt_pending")
    if pending_runtime_attempt(storage, source):
        raise ValueError("canonical_runtime_attempt_pending")
    # Authenticated A/consent do not authorize a different Adobe borrower.
    # Resolve before any candidate GET (including a first claimed ACSM).
    borrower_identity, borrower_current = (
        pin_borrower(service, source) if source.kind == "loan"
        else (scope.identity_id, lambda: True)
    )

    historical_rearm = read_historical_rearm_binding(storage, source)
    if historical_rearm is not None and historical_rearm[0] != borrower_identity:
        raise AdeptProfileUnavailable("historical_rearm_profile_changed")

    def require_borrower():
        if not borrower_current() or not current():
            raise ValueError("canonical_delivery_borrower_source_changed")

    def require_historical_profile(profile, profile_id):
        if historical_rearm is None:
            return
        try:
            name, _ = service.resolve_adept_profile_for_identity(borrower_identity)
            row = service._adept_profile_row(
                name, service.adept_profiles_for_identity(borrower_identity, profile_name=name))
            if (name != profile or type(row) is not dict
                    or (row.get("name") or row.get("profile")) != name
                    or row.get("profile_id") != profile_id
                    or profile_is_explicitly_acsm_unready(row)
                    or (borrower_identity, profile_id, historical_user_digest(row.get("user_uuid")))
                        != historical_rearm
                    or read_historical_rearm_binding(storage, source) != historical_rearm):
                raise ValueError()
        except Exception:
            raise AdeptProfileUnavailable("historical_rearm_profile_changed") from None
        require_borrower()

    def acsm_profile():
        require_borrower()
        profile, _ = service.resolve_adept_profile_for_identity(borrower_identity)
        if type(profile) is not str or not profile:
            raise AdeptProfileUnavailable("canonical_delivery_adept_profile_missing")
        require_borrower()
        profile_id = service._originating_adept_profile_id(borrower_identity, profile)
        # Older runtime contracts may omit readiness, but a published
        # negative signal remains a hard refusal as in the historical path.
        if source.kind == "loan":
            # An unavailable profile inventory is NOT evidence that the
            # selected Adobe identity is usable. Failing open here used to
            # poison the durable one-use attempt before the runtime rejected
            # an UUID passed as a CLI profile name (#7161). Keep this check
            # before begin_loan_adept_attempt and any DRM subprocess.
            try:
                rows = service.adept_profiles_for_identity(
                    borrower_identity, profile_name=profile,
                )
                profile_row = service._adept_profile_row(profile, rows)
            except Exception as exc:
                raise AdeptProfileUnavailable("canonical_delivery_adept_profile_inventory_unavailable") from exc
            if (type(profile_row) is not dict
                    or (profile_row.get("name") or profile_row.get("profile")) != profile):
                raise AdeptProfileUnavailable("canonical_delivery_adept_profile_unverified")
            # A present stable reference must identify the same borrower.
            # Older rows with name-only evidence remain compatible.
            row_id = profile_row.get("profile_id")
            if row_id is not None and profile_id is not None and row_id != profile_id:
                raise AdeptProfileUnavailable("canonical_delivery_adept_profile_binding_changed")
            if profile_is_explicitly_acsm_unready(profile_row):
                raise AdeptProfileUnavailable("adept_profile_acsm_not_ready")
        require_historical_profile(profile, profile_id)
        require_borrower()
        return profile, profile_id

    def require_adept_profile(profile, profile_id):
        require_borrower()
        if (source.kind == "loan"
                and service._originating_adept_profile_id(borrower_identity, profile)
                    != profile_id):
            raise ValueError("adept_borrower_profile_changed")
        require_historical_profile(profile, profile_id)
        require_borrower()

    directory, exclusive_owner = storage.exact_profile_directory_binding(
        scope.identity_id,
    )
    if directory is None:
        raise ValueError("canonical_delivery_identity_unknown")
    if (not directory or directory in (".", "..")
            or "/" in directory or chr(92) in directory):
        raise ValueError("canonical_delivery_profile_path_invalid")

    def physical_owner_unique() -> bool:
        # Verify the same exact folder owner after GET and during the
        # selected delivery, without loading all identities per chunk.
        try:
            current_directory, unique = storage.exact_profile_directory_binding(
                scope.identity_id,
            )
            return unique and current_directory == directory
        except Exception:
            return False

    if not exclusive_owner or not physical_owner_unique():
        raise ValueError("canonical_delivery_profile_directory_ambiguous")

    # Create a new, isolated private namespace for this one request. Do not
    # adopt pre-existing provider files, matching SHA, old receipts or a
    # candidate located outside this Core-chosen profile staging directory.
    root = Path(os.path.abspath(library_root.expanduser()))
    base = root / directory / ".canonical-delivery"
    if any(p.is_symlink() for p in (root, root / directory, base)):
        raise ValueError("canonical_delivery_profile_symlink")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    attempt = base / ("run-" + secrets.token_hex(16))
    attempt.mkdir(mode=0o700)
    created = os.stat(attempt, follow_symlinks=False)
    attempt_inode = (created.st_dev, created.st_ino)

    def attempt_current() -> bool:
        # A provider callback (or another process) may rename the private
        # run and place unrelated bytes under the same pathname. The name,
        # profile root and SHA alone do not prove that THIS Core-created
        # staging namespace produced the candidate.
        try:
            current_attempt = os.stat(attempt, follow_symlinks=False)
            return (stat.S_ISDIR(current_attempt.st_mode)
                    and (current_attempt.st_dev, current_attempt.st_ino)
                        == attempt_inode)
        except OSError:
            return False

    # Optional, provider-neutral Core runtime bridge. An adapter may supply
    # an opaque DRM recipe, but only this live, already-admitted operation
    # can execute it. Neither the recipe nor the runtime result is persisted
    # as B. Legacy providers never see this callback.
    runtime_used = [False]
    adept_attempt_id = [""]
    runtime_attempt_id = [""]
    runtime_phase = [""]

    def refresh_adept_profile(profile_name, profile_id):
        require_borrower()
        # No cached UI readiness or durable marker before this exact,
        # scoped read-only installed-runtime capability veto.
        require_fresh_acsm_capability(
            service, profile_name,
            scope_current=lambda: borrower_current() is True and current() is True,
        )
        # Fresh capability reads can change the profile binding while the
        # CLI runs. Check its *name and stable reference* again before claim.
        current_name, _ = service.resolve_adept_profile_for_identity(borrower_identity)
        if current_name != profile_name:
            raise AdeptProfileUnavailable("adept_borrower_profile_changed")
        require_adept_profile(profile_name, profile_id)

    def mark_adept_attempt(profile_name: str, profile_id):
        if source.kind == "purchase":
            # A purchase can also spend a single-use ADEPT token. The
            # loan-only fence cannot be reused as it asserts a loan cycle.
            # Claim immediately before fulfillment, never on inventory/GET.
            require_adept_profile(profile_name, profile_id)
            operation = begin_runtime_attempt(
                storage, source=source, offer_id=offer.delivery_offer_id,
                shared_lock=shared_lock, phase="purchase_acsm",
            )
            try:
                require_adept_profile(profile_name, profile_id)
                if not attempt_current() or not admission_current():
                    raise ValueError("canonical_delivery_runtime_admission_changed")
            except Exception:
                release_unsubmitted_runtime_attempt(
                    storage, source=source, offer_id=offer.delivery_offer_id,
                    operation_id=operation, shared_lock=shared_lock,
                )
                raise
            runtime_attempt_id[0] = operation
            runtime_phase[0] = "purchase_acsm"
            return
        refresh_adept_profile(profile_name, profile_id)
        operation = begin_loan_adept_attempt(
            storage, source=source, offer_id=offer.delivery_offer_id,
            shared_lock=shared_lock,
        )
        try:
            # No external runtime call has been entered yet. This is the
            # point where Core can prove a local refusal independently of
            # the installed runtime's explicit non-submission contract.
            require_adept_profile(profile_name, profile_id)
        except Exception:
            release_proven_unsubmitted_attempt(
                storage, source=source, offer_id=offer.delivery_offer_id,
                operation_id=operation, shared_lock=shared_lock,
                reason="core_pre_dispatch_refusal",
            )
            raise
        adept_attempt_id[0] = operation

    def dispatch_acquisition(request):
        try:
            return service.execute_acquisition_request(request)
        except AdeptFulfillmentNotSubmitted as error:
            if type(error) is AdeptFulfillmentNotSubmitted and adept_attempt_id[0]:
                release_proven_unsubmitted_attempt(
                    storage, source=source, offer_id=offer.delivery_offer_id,
                    operation_id=adept_attempt_id[0], shared_lock=shared_lock,
                    reason="runtime_attested_not_submitted",
                )
                adept_attempt_id[0] = ""
            if (type(error) is AdeptFulfillmentNotSubmitted
                    and runtime_attempt_id[0] and runtime_phase[0] == "purchase_acsm"):
                release_unsubmitted_runtime_attempt(
                    storage, source=source, offer_id=offer.delivery_offer_id,
                    operation_id=runtime_attempt_id[0], shared_lock=shared_lock,
                )
                runtime_attempt_id[0] = ""
                runtime_phase[0] = ""
            raise
        except RuntimeDispatchNotSubmitted as error:
            if (type(error) is RuntimeDispatchNotSubmitted
                    and runtime_attempt_id[0] and runtime_phase[0] == "runtime"):
                release_unsubmitted_runtime_attempt(
                    storage, source=source, offer_id=offer.delivery_offer_id,
                    operation_id=runtime_attempt_id[0], shared_lock=shared_lock,
                )
                runtime_attempt_id[0] = ""
                runtime_phase[0] = ""
            raise

    runtime_artifact_path: list[Path | None] = [None]
    runtime_artifact_digest: list[str | None] = [None]
    runtime_artifact_stat: list[os.stat_result | None] = [None]

    def execute_selected_runtime(request):
        if not attempt_current():
            raise ValueError("canonical_delivery_staging_replaced")
        if runtime_used[0] or not admission_current():
            raise ValueError("canonical_delivery_runtime_admission_changed")
        if (type(request) is not dict or request.get("schema_version") != 2
                or not _selected_runtime_recipe_source_admitted(
                    service, request.get("source"), scope.provider_id,
                )
                or type(request.get("destination")) is not str):
            raise ValueError("canonical_delivery_runtime_recipe_invalid")
        destination = Path(request["destination"])
        # No plugin-selected target outside the exact Core-owned attempt.
        # No overwrite of an already-existing candidate or unrelated file.
        if (not destination.is_absolute() or destination.parent != attempt
                or destination.suffix.casefold() not in (".epub", ".pdf")
                or destination.is_symlink() or destination.exists()
                or type(request.get("format")) is not str
                or request["format"].casefold() != destination.suffix[1:].casefold()):
            raise ValueError("canonical_delivery_runtime_target_invalid")
        expected_adept = None
        if request["source"].get("type") == "acsm":
            expected_adept = acsm_profile()
            if (type(request.get("profile")) is not str
                    or request["profile"] != expected_adept[0]
                    or request["source"].get("provider") != scope.provider_id
                    or type(request.get("metadata")) is not dict
                    or request["metadata"].get("identity_id") != borrower_identity):
                raise ValueError("adept_borrower_profile_changed")
            require_adept_profile(*expected_adept)
        require_borrower()
        runtime_used[0] = True
        if expected_adept is not None:
            mark_adept_attempt(*expected_adept)
        elif request["source"].get("type") != "acsm":
            # A reciprocal provider/runtime binding qualifies dispatch, not
            # idempotence of an ambiguous result. Pin the exact resource
            # BEFORE entering the executor, independently of offer rotation.
            operation = begin_runtime_attempt(
                storage, source=source, offer_id=offer.delivery_offer_id,
                shared_lock=shared_lock,
            )
            try:
                require_borrower()
                if not attempt_current() or not admission_current():
                    raise ValueError("canonical_delivery_runtime_admission_changed")
                if not _selected_runtime_recipe_source_admitted(
                        service, request["source"], scope.provider_id):
                    raise ValueError("canonical_delivery_runtime_recipe_invalid")
            except Exception:
                release_unsubmitted_runtime_attempt(
                    storage, source=source, offer_id=offer.delivery_offer_id,
                    operation_id=operation, shared_lock=shared_lock,
                )
                raise
            runtime_attempt_id[0] = operation
            runtime_phase[0] = "runtime"
        result = dispatch_acquisition(request)
        require_borrower()
        if expected_adept is not None:
            require_adept_profile(*expected_adept)
            if type(result) is not dict:
                raise ValueError("canonical_delivery_adept_result_invalid")
            capture_binding(result, profile_name=expected_adept[0],
                            profile_id=expected_adept[1])
        if not attempt_current():
            raise ValueError("canonical_delivery_staging_replaced")
        if not admission_current():
            raise ValueError("canonical_delivery_runtime_admission_changed")
        if (type(result) is not dict or result.get("ok") is not True
                or type(result.get("artifact")) is not dict
                or type(result["artifact"].get("path")) is not str):
            raise ValueError("canonical_delivery_runtime_result_invalid")
        produced = _physical_target(Path(result["artifact"]["path"]), root, directory)
        if produced != destination.resolve(strict=True):
            raise ValueError("canonical_delivery_runtime_artifact_foreign")
        # A successful runtime call witnesses exactly this produced target,
        # never any other bytes the plugin might return from the same staging
        # directory. The proof stays ephemeral and cannot be forged by a DTO.
        # The provider still controls its callback after the runtime returns.
        # Bind the proof to the actual bytes and inode produced NOW, not only
        # to a path that the provider can overwrite before returning a DTO.
        witnessed = os.stat(produced, follow_symlinks=False)
        if not stat.S_ISREG(witnessed.st_mode) or witnessed.st_size <= 0:
            raise ValueError("canonical_delivery_runtime_artifact_invalid")
        digest = LocalCatalog.sha256(produced)
        if not _same_file(produced, witnessed):
            raise ValueError("canonical_delivery_runtime_artifact_changed")
        runtime_artifact_path[0] = produced
        runtime_artifact_stat[0] = witnessed
        runtime_artifact_digest[0] = digest
        return result
    # The staging namespace belongs to this failed attempt until the Core
    # issues a completed delivery receipt. Failed GET, invalid candidates and
    # rejected ADEPT results must not leave signed ACSM/partial bytes behind.
    # Once this method returns, the executor owns the verified bytes for its
    # durable crash-recovery checkpoint: never delete that successful run.
    try:
        if not current():
            raise ValueError("canonical_delivery_source_rebound")
        # A provider may have both direct HTTPS/ACSM offers and DRM-runtime
        # offers in one account. Mere presence of its runtime method is not
        # evidence that THIS exact offered resource needs DRM. The optional
        # route classifier is local, typed and must be bound to the opaque
        # selected offer; an absent classifier preserves the strict
        # runtime-required default. A malformed/raising classifier is never
        # permission to try the unguarded legacy candidate ABI.
        if runtime_guarded:
            route_name = "downloadable_selected_delivery_requires_runtime"
            route_reader = getattr_static(session, route_name, None)
            if route_reader is not None:
                if not callable(route_reader):
                    raise ValueError("canonical_delivery_runtime_route_invalid")
                try:
                    required = getattr(session, route_name)(
                        account, edition_id=source.edition_id,
                        resource_id=source.right_id, kind=source.kind,
                        branch=source.branch,
                        selected_offer_id=offer.delivery_offer_id,
                    )
                except Exception:
                    raise ValueError("canonical_delivery_runtime_route_unavailable") from None
                if type(required) is not bool:
                    raise ValueError("canonical_delivery_runtime_route_invalid")
                if required is False:
                    if not callable(getattr_static(session, guarded_name, None)):
                        raise ValueError("canonical_delivery_guarded_route_unavailable")
                    # Direct HTTPS remains guarded at each transport hop;
                    # ACSM is independently fulfilled by Core ADEPT below.
                    # KDRM remains runtime-required by the offer-bound
                    # classifier, which the provider must verify again.
                    runtime_guarded = False
                    reader_name = guarded_name
                    guarded = True
                if not current():
                    raise ValueError("canonical_delivery_source_rebound")
        # #7244: a manually released historical marker retains a durable
        # negative profile constraint across restart. Check before even
        # the candidate GET, not just before the ADEPT subprocess.
        early_adept = None
        if historical_rearm is not None:
            early_adept = acsm_profile()
            refresh_adept_profile(*early_adept)
        # #3121: a confirmed manual borrow may have already staged
        # its exact one-shot ACSM. The canonical executor must consume that
        # claim-bound token before asking the provider for another signed URL.
        # Only an exact, current, private receipt bypasses the candidate GET.
        require_borrower()
        from ..acsm_cache import stage_claimed_initial_acsm
        first_acsm = stage_claimed_initial_acsm(
            service, session, account, source, attempt=attempt,
            admission_current=current,
        )
        require_borrower()
        if (early_adept is None and first_acsm is None
                and source.kind == "loan" and not runtime_guarded):
            probe_name = "downloadable_selected_candidate_is_acsm"
            probe = getattr_static(session, probe_name, None)
            if probe is not None:
                if not callable(probe):
                    raise ValueError("canonical_delivery_acsm_route_invalid")
                is_acsm = getattr(session, probe_name)(
                    account, edition_id=source.edition_id, resource_id=source.right_id,
                    kind=source.kind, branch=source.branch,
                    selected_offer_id=offer.delivery_offer_id,
                )
                if type(is_acsm) is not bool:
                    raise ValueError("canonical_delivery_acsm_route_invalid")
                require_borrower()
                if is_acsm:
                    early_adept = acsm_profile()
                    refresh_adept_profile(*early_adept)
        if historical_rearm is not None:
            require_adept_profile(*early_adept)
        candidate = (
            (first_acsm, "") if first_acsm is not None
            else getattr(session, reader_name)(
            account, edition_id=source.edition_id,
            **({"resource_id": source.right_id,
                "selected_offer_id": offer.delivery_offer_id}
               if reader_name in {"downloadable_selected_delivery_candidate", guarded_name,
                                  runtime_guarded_name}
               else {"right_id": source.right_id,
                     "delivery_offer_id": offer.delivery_offer_id}),
            kind=source.kind, branch=source.branch,
            target_dir=attempt, explicit_user_action=True,
            # The rights v2 candidate ABI is already used by BnF and
            # Numilog. Hand it the same Core-owned revocable lease as the
            # downloadable guarded ABI; no unguarded rights GET fallback.
            **({"admission_current": admission_current}
               if guarded or reader_name == "rights_selected_delivery_candidate"
               else {}),
            **({"execute_runtime": execute_selected_runtime} if runtime_guarded else {}),
        )
        )
        if not current():
            raise ValueError("canonical_delivery_source_rebound_after_get")
        require_borrower()
        if historical_rearm is not None:
            require_adept_profile(*early_adept)
        if not attempt_current():
            raise ValueError("canonical_delivery_staging_replaced")
        if not physical_owner_unique():
            raise ValueError("canonical_delivery_profile_directory_ambiguous")
        if first_acsm is None and reader_name in {
                "downloadable_selected_delivery_candidate", guarded_name,
                runtime_guarded_name}:
            expected = SourceReference(
                scope, source.branch, source.edition_id, source.right_id, source.kind,
            )
            if (type(candidate) is not DeliveryCandidate
                    or candidate.source != expected
                    or candidate.selected_offer_id != offer.delivery_offer_id):
                raise ValueError("canonical_delivery_candidate_scope_mismatch")
            candidate = (candidate.path, candidate.provider_hint)
        if (type(candidate) is not tuple or len(candidate) != 2
                or (type(candidate[0]) is not str
                    and not isinstance(candidate[0], Path))
                or type(candidate[1]) is not str):
            raise ValueError("canonical_delivery_candidate_invalid")

        raw_path = Path(candidate[0])
        received = _physical_target(raw_path, root, directory)
        if not received.is_relative_to(attempt.resolve(strict=True)):
            raise ValueError("canonical_delivery_candidate_outside_staging")
        # The runtime-guarded producer must execute the Core's one-shot
        # runtime successfully, then return the SAME physical output. A valid
        # EPUB/PDF written separately by the plugin is not DRM fulfillment.
        if runtime_guarded and (runtime_artifact_path[0] is None
                                or received != runtime_artifact_path[0]):
            raise ValueError("canonical_delivery_runtime_artifact_mismatch")
        if runtime_guarded and (
            runtime_artifact_stat[0] is None
            or not _same_file(received, runtime_artifact_stat[0])
            or LocalCatalog.sha256(received) != runtime_artifact_digest[0]
        ):
            raise ValueError("canonical_delivery_runtime_artifact_changed")
        if received.stat().st_size <= 0:
            raise ValueError("canonical_delivery_candidate_empty")
        kind = detect_artifact_format(received)
        if runtime_guarded and kind == "acsm":
            raise ValueError("canonical_delivery_runtime_token_not_final")
        adept_receipt_ref = ""

        if kind == "acsm":
            # The ACSM token is not an Artifact. The configured ADEPT runtime,
            # selected borrower's profile and final bytes must ALL be confirmed.
            fmt = detect_acsm_format(received)
            if fmt not in ("epub", "pdf"):
                raise ValueError("canonical_delivery_adept_format_unconfirmed")
            profile, profile_id = acsm_profile()
            if early_adept is not None and early_adept != (profile, profile_id):
                raise AdeptProfileUnavailable("adept_borrower_profile_changed")
            destination = attempt / ("fulfilled." + fmt)
            if destination.exists() or destination.is_symlink():
                raise ValueError("canonical_delivery_adept_target_occupied")
            request = {
                "schema_version": 2,
                "source": {"type": "acsm", "provider": scope.provider_id,
                           "path": str(received), "headers": {}},
                "destination": str(destination), "format": fmt,
                "profile": profile,
                "metadata": {"identity_id": borrower_identity},
                "options": {"force": False},
            }
            require_adept_profile(profile, profile_id)
            mark_adept_attempt(profile, profile_id)
            result = dispatch_acquisition(request)
            require_adept_profile(profile, profile_id)
            if type(result) is not dict:
                raise ValueError("canonical_delivery_adept_result_invalid")
            capture_binding(result, profile_name=profile, profile_id=profile_id)
            artifact = result.get("artifact")
            if type(artifact) is not dict or type(artifact.get("path")) is not str:
                raise ValueError("canonical_delivery_adept_artifact_missing")
            produced = _physical_target(Path(artifact["path"]), root, directory)
            if produced != destination.resolve(strict=True):
                raise ValueError("canonical_delivery_adept_artifact_foreign")
            if not current():
                raise ValueError("canonical_delivery_source_rebound_after_adept")
            received = produced
            kind = detect_artifact_format(received)
            if kind != fmt:
                raise ValueError("canonical_delivery_adept_format_mismatch")
            adept_receipt_ref = "core-adept:" + secrets.token_hex(32)

        if kind not in ("epub", "pdf"):
            raise ValueError("canonical_delivery_final_format_unavailable")
        final = _physical_target(received, root, directory)
        if not final.is_relative_to(attempt.resolve(strict=True)):
            raise ValueError("canonical_delivery_final_outside_staging")
        before = os.stat(final, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_size == 0:
            raise ValueError("canonical_delivery_final_not_regular")
        inspection = inspect_artifact(final, strict_structure=False)
        valid, catalog_reason = LocalCatalog._validate(final, kind)
        if (not inspection.valid or inspection.media_format != kind
                or inspection.protected is True or not valid):
            # P0 #3252: the candidate is cleaned up on this path. Record
            # the independent validation facts *before* cleanup, without
            # exposing a temporary file or treating a runtime returncode=0
            # as evidence that the final EPUB/PDF is usable.
            # Classification is evidence-only. Even a font-obfuscation profile
            # cannot override the independent protected=True final veto.
            encryption_profile = (
                rejected_epub_encryption_profile(final)
                if inspection.media_format == "epub"
                and inspection.protected is True else "not_applicable"
            )
            logger.warning(
                "TRACE canonical delivery final rejected "
                "code=canonical_delivery_final_invalid provider_id=%s "
                "identity_id=%r account_id=%r edition_id=%r right_id=%r "
                "reader=%s runtime_guarded=%s expected_kind=%s "
                "inspection_valid=%s inspection_format=%s "
                "inspection_protected=%s inspection_reason=%s "
                "catalog_valid=%s catalog_reason=%s encryption_profile=%s",
                scope.provider_id, scope.identity_id, scope.account_id,
                source.edition_id, source.right_id,
                reader_name, runtime_guarded, kind,
                inspection.valid, inspection.media_format, inspection.protected,
                inspection.reason, valid, catalog_reason, encryption_profile,
            )
            # A validated EPUB can still carry content DRM after a
            # successful runtime or HTTPS delivery. Keep the historical
            # exception message for the private raw journal and the same
            # final veto; only this *pure* protected-EPUB case gets the
            # distinct closed terminal reason drm_unresolved.
            if (inspection.valid and inspection.media_format == kind == "epub"
                    and inspection.protected is True and valid):
                raise CanonicalDrmUnresolved("canonical_delivery_final_invalid")
            raise ValueError("canonical_delivery_final_invalid")
        digest = LocalCatalog.sha256(final)
        if (runtime_guarded and (
            not _same_file(final, runtime_artifact_stat[0])
            or digest != runtime_artifact_digest[0]
        )):
            raise ValueError("canonical_delivery_runtime_artifact_changed")
        if not _same_file(final, before) or not current():
            raise ValueError("canonical_delivery_final_changed")
        if not attempt_current():
            raise ValueError("canonical_delivery_staging_replaced")
        if not physical_owner_unique():
            raise ValueError("canonical_delivery_profile_directory_ambiguous")
        # This receipt is a Core witness issued AFTER the verified, in-scope
        # candidate callback. It is deliberately distinct from provider A's
        # inventory evidence and signed-offer ID, and contains no private URL.
        require_borrower()
        fact = json.dumps(
            [*coords, source.branch, source.edition_id, source.right_id,
             source.kind, offer.delivery_offer_id, digest, secrets.token_hex(32)],
            ensure_ascii=False, separators=(",", ":"),
        )
        receipt = "core-delivery:" + hashlib.sha256(fact.encode("utf-8")).hexdigest()
        if (adept_attempt_id[0] or runtime_phase[0] == "purchase_acsm") and not adept_receipt_ref:
            # A runtime-guarded ACSM has the same Core ADEPT provenance.
            adept_receipt_ref = "core-adept:" + secrets.token_hex(32)
        return CompletedDelivery(
            source, offer.delivery_offer_id, final, digest, receipt, adept_receipt_ref,
            adept_attempt_id=adept_attempt_id[0],
            runtime_attempt_id=runtime_attempt_id[0],
        )
    except BaseException:
        # Best effort, without masking the original provider/cancellation
        # error or trusting a path that the provider may have replaced.
        try:
            now = os.stat(attempt, follow_symlinks=False)
            if (stat.S_ISDIR(now.st_mode)
                    and (now.st_dev, now.st_ino) == attempt_inode
                    and not base.is_symlink()):
                shutil.rmtree(attempt)
        except (OSError, ValueError):
            pass
        raise
    finally:
        # The callback must not authorize a future GET outside this one
        # attempted selected delivery, even if source ON/epochs are unchanged.
        admission_live[0] = False


__all__ = ["complete_selected_delivery"]
