"""#3171: Core-owned completion of a provider-selected *candidate* delivery.

Only the already held source Sync may call this. Provider plugins select one
exact signed offer and return candidate bytes, never CompletedDelivery,
SQLite authority, or an ADEPT receipt. This bridge independently validates
the final file within the current profile and witnesses a completed callback.
"""
from __future__ import annotations

import hashlib
from inspect import getattr_static
import json
import os
from pathlib import Path
import secrets
import stat

from ..adept_binding import capture_binding
from ..artifact_inspection import detect_artifact_format, detect_acsm_format, inspect_artifact
from ..catalog import LocalCatalog
from ..locking import InterProcessLock
from ..models import Account
from ..paths import acquisition_lock_file
from ..plugin_api.provider_rights_observation import ProviderRightObservation
from .canonical_publication import _physical_target, _same_file
from .convergence_execution import CompletedDelivery
from .source_convergence import SourceKey


def complete_selected_delivery(
    service, account: Account, session, source: SourceKey,
    offer: ProviderRightObservation, *,
    library_root: Path, shared_lock: InterProcessLock,
) -> CompletedDelivery:
    """Witness candidate GET then final validated EPUB/PDF or ADEPT fulfillment.

    The executor alone admits, checks consent before/after network, journals
    this completed result and publishes B atomically. A provider response's
    (path, URL) pair is NEVER itself treated as a receipt or final Artifact.
    """
    if (type(account) is not Account or type(source) is not SourceKey
            or type(offer) is not ProviderRightObservation
            or offer.state != "active" or not offer.provider_attested
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
    raw = getattr_static(session, "rights_selected_delivery_candidate", None)
    if raw is None or not callable(raw):
        raise ValueError("canonical_delivery_candidate_contract_unavailable")

    epoch = service._account_context_epoch
    accounts, sessions, storage = service.accounts, service._sessions, service.storage

    def bound() -> bool:
        try:
            return (
                service.accounts is accounts and service._sessions is sessions
                and service.storage is storage
                and service._account_context_epoch == epoch
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
                and storage.identity_for_connection(
                    scope.provider_id, scope.credential_id) == scope.identity_id
                and service.sync_source_enabled(account) is True
            )
        except (AttributeError, TypeError, KeyError, ValueError):
            return False

    if not bound():
        raise ValueError("canonical_delivery_source_rebound")
    identities = [x for x in storage.identities()
                  if x.get("identity_id") == scope.identity_id]
    if len(identities) != 1:
        raise ValueError("canonical_delivery_identity_unknown")
    directory = identities[0].get("directory_name")
    if (type(directory) is not str or not directory
            or directory in (".", "..") or "/" in directory or "\\" in directory):
        raise ValueError("canonical_delivery_profile_path_invalid")

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
    if not bound():
        raise ValueError("canonical_delivery_source_rebound")
    candidate = session.rights_selected_delivery_candidate(
        account, edition_id=source.edition_id, right_id=source.right_id,
        kind=source.kind, branch=source.branch,
        delivery_offer_id=offer.delivery_offer_id,
        target_dir=attempt, explicit_user_action=True,
    )
    if not bound():
        raise ValueError("canonical_delivery_source_rebound_after_get")
    if (type(candidate) is not tuple or len(candidate) != 2
            or type(candidate[0]) not in (str, Path)
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
        if not bound():
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
    if not _same_file(final, before) or not bound():
        raise ValueError("canonical_delivery_final_changed")
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


__all__ = ["complete_selected_delivery"]
