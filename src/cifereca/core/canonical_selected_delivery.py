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
from .canonical_publication import _physical_target, _same_file
from .convergence_execution import CompletedDelivery
from .source_convergence import SourceKey


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
    guarded = (callable(consent_still_current)
               and callable(getattr_static(session, guarded_name, None)))
    reader_name = (
        guarded_name if guarded else
        "downloadable_selected_delivery_candidate"
        if getattr_static(session, "downloadable_selected_delivery_candidate", None) is not None
        else "rights_selected_delivery_candidate"
    )
    raw = getattr_static(session, reader_name, None)
    if raw is None or not callable(raw):
        raise ValueError("canonical_delivery_candidate_contract_unavailable")

    epoch = source_context_epoch(service)
    accounts, sessions, storage = service.accounts, service._sessions, service.storage

    def bound() -> bool:
        try:
            session_accounts = session.accounts
            if (not isinstance(session_accounts, (list, tuple))
                    or sum(
                        type(x) is Account
                        and (x.identity_id, x.provider_id,
                             x.credential_id, x.unique_id) == coords
                        for x in session_accounts
                    ) != 1):
                return False
            return (
                service.accounts is accounts and service._sessions is sessions
                and service.storage is storage
                and source_context_epoch(service) == epoch
                and (account.identity_id, account.provider_id,
                     account.credential_id, account.unique_id) == coords
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
    # The staging namespace belongs to this failed attempt until the Core
    # issues a completed delivery receipt. Failed GET, invalid candidates and
    # rejected ADEPT results must not leave signed ACSM/partial bytes behind.
    # Once this method returns, the executor owns the verified bytes for its
    # durable crash-recovery checkpoint: never delete that successful run.
    try:
        if not current():
            raise ValueError("canonical_delivery_source_rebound")
        candidate = getattr(session, reader_name)(
            account, edition_id=source.edition_id,
            **({"resource_id": source.right_id,
                "selected_offer_id": offer.delivery_offer_id}
               if reader_name in {"downloadable_selected_delivery_candidate", guarded_name}
               else {"right_id": source.right_id,
                     "delivery_offer_id": offer.delivery_offer_id}),
            kind=source.kind, branch=source.branch,
            target_dir=attempt, explicit_user_action=True,
            **({"admission_current": admission_current} if guarded else {}),
        )
        if not current():
            raise ValueError("canonical_delivery_source_rebound_after_get")
        if not physical_owner_unique():
            raise ValueError("canonical_delivery_profile_directory_ambiguous")
        if reader_name in {"downloadable_selected_delivery_candidate", guarded_name}:
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
        if received.stat().st_size <= 0:
            raise ValueError("canonical_delivery_candidate_empty")
        kind = detect_artifact_format(received)
        adept_receipt_ref = ""

        if kind == "acsm":
            # The ACSM token is not an Artifact. The configured ADEPT runtime,
            # selected borrower's profile and final bytes must ALL be confirmed.
            fmt = detect_acsm_format(received)
            if fmt not in ("epub", "pdf"):
                raise ValueError("canonical_delivery_adept_format_unconfirmed")
            profile, _profile_source = service.resolve_adept_profile_for_identity(
                scope.identity_id)
            if type(profile) is not str or not profile:
                raise ValueError("canonical_delivery_adept_profile_missing")
            destination = attempt / ("fulfilled." + fmt)
            if destination.exists() or destination.is_symlink():
                raise ValueError("canonical_delivery_adept_target_occupied")
            profile_id = service._originating_adept_profile_id(scope.identity_id, profile)
            request = {
                "schema_version": 2,
                "source": {"type": "acsm", "provider": scope.provider_id,
                           "path": str(received), "headers": {}},
                "destination": str(destination), "format": fmt,
                "profile": profile,
                "metadata": {"identity_id": scope.identity_id},
                "options": {"force": False},
            }
            result = service.execute_acquisition_request(request)
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
        valid, _ = LocalCatalog._validate(final, kind)
        if (not inspection.valid or inspection.media_format != kind
                or inspection.protected is True or not valid):
            raise ValueError("canonical_delivery_final_invalid")
        digest = LocalCatalog.sha256(final)
        if not _same_file(final, before) or not current():
            raise ValueError("canonical_delivery_final_changed")
        if not physical_owner_unique():
            raise ValueError("canonical_delivery_profile_directory_ambiguous")
        # This receipt is a Core witness issued AFTER the verified, in-scope
        # candidate callback. It is deliberately distinct from provider A's
        # inventory evidence and signed-offer ID, and contains no private URL.
        fact = json.dumps(
            [*coords, source.branch, source.edition_id, source.right_id,
             source.kind, offer.delivery_offer_id, digest, secrets.token_hex(32)],
            ensure_ascii=False, separators=(",", ":"),
        )
        receipt = "core-delivery:" + hashlib.sha256(fact.encode("utf-8")).hexdigest()
        return CompletedDelivery(
            source, offer.delivery_offer_id, final, digest, receipt, adept_receipt_ref,
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
