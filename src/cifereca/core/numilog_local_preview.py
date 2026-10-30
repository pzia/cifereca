"""Offline, read-only preview of Numilog purchases before any local backup (#1905).

This is NOT an acquisition plan, confirmation token or proof that an ACSM is
downloadable. Only durable, private-manifest attested rights are counted.
Everything returned to the broker consists of bounded counters and fixed codes.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import re
from typing import Any

from ..library_files import safe_library_component
from ..plugin_api import Cancelled, SyncResult
from .kobo_local_audit import _read_hash_within
from .numilog_startup_diagnostics import local_proof

_MAX_ROWS = 100_000
_ID = re.compile(r"numilog:([0-9a-f]{20}):([A-Za-z0-9_.-]{1,160}):([0-9a-f]{16})\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_COUNT_NAMES = (
    "owned_purchases", "already_local_linked", "already_local_verified",
    "needs_file_verification", "potentially_missing", "candidate_unverified",
    "needs_reconciliation", "attention_required", "not_downloadable",
    "ignored_unattested_rights",
)


@dataclass(frozen=True)
class _Binding:
    """Capture the caller's scope before DB/file reads can change accounts."""

    provider_id: str
    identity_id: str
    credential_id: str
    unique_id: str


def _refused(code: str) -> SyncResult:
    return SyncResult(ok=False, changed=0, warnings=(code,), facts={
        "phase": "local_materialization", "status": code, "read_only": True,
        "acquisition_authorized": False,
    })


def _valid_binding(broker: Any, account: Any, source_id: str) -> bool:
    """One enabled durable account; no reliance on current GUI identity."""
    try:
        identity = getattr(account, "identity_id", None)
        credential = getattr(account, "credential_id", None)
        account_id = getattr(account, "unique_id", None)
        if (getattr(account, "provider_id", None) != "numilog"
                or not all(type(value) is str and 0 < len(value) <= 1024
                           and not any(c in value for c in "\0\r\n")
                           for value in (identity, credential, account_id))
                or not broker._enabled(source_id)
                or broker.service.identity_by_id(identity) is None):
            return False
        matches = [
            row for row in broker.service.accounts
            if getattr(row, "provider_id", None) == "numilog"
            and getattr(row, "credential_id", None) == credential
        ]
        if (len(matches) != 1
                or matches[0].identity_id != identity
                or matches[0].unique_id != account_id
                or broker._source_id(matches[0]) != source_id
                or broker._source_id(account) != source_id):
            return False
        storage = broker.service.storage
        if storage.identity_for_connection("numilog", credential) != identity:
            return False
        persisted = [
            row for row in storage.provider_connections(identity_id=identity)
            if row.get("provider_id") == "numilog"
            and row.get("credential_id") == credential
            and row.get("enabled") is True
        ]
        return len(persisted) == 1
    except Exception:
        return False


def _item_id(item_id: Any, credential: str) -> bool:
    if type(item_id) is not str or len(item_id) > 512:
        return False
    match = _ID.fullmatch(item_id)
    return match is not None and match.group(1) == hashlib.sha256(
        credential.encode("utf-8"),
    ).hexdigest()[:20]


def _identity_root(service: Any, identity_id: str) -> tuple[Path, ...] | None:
    """Compute an identity-owned root WITHOUT mkdir(), resolve(), or scans."""
    try:
        current = service.identity_by_id(identity_id)
        if current is None:
            return None
        dirname = safe_library_component(current.directory_name or identity_id, "default")
        if any(
            other.identity_id != identity_id
            and safe_library_component(other.directory_name or other.identity_id, "default") == dirname
            for other in service.identities
        ):
            return None
        configured = service.config.get("library.directory", "~/EBooks")
        if type(configured) is not str or not configured.strip():
            return None
        configured_path = Path(os.path.expandvars(os.path.expanduser(configured)))
        if not configured_path.is_absolute():
            # Match LibraryService.library_dir() without creating directories.
            configured_path = Path.home() / configured_path
        root = Path(os.path.abspath(configured_path))
        return (root / dirname,)
    except Exception:
        return None


def _path_in_identity(value: Any, roots: tuple[Path, ...]) -> bool:
    """Reject an obviously foreign path even without requesting SHA scans."""
    if type(value) is not str or not value or "\0" in value:
        return False
    candidate = Path(value)
    if not candidate.is_absolute() or ".." in candidate.parts:
        return False
    absolute = Path(os.path.abspath(candidate))
    return any(absolute != root and absolute.is_relative_to(root) for root in roots)


def preview(
    broker: Any, account: Any, source_id: str, *, verify_local_files: bool,
    _only_item_id: str | None = None,
) -> SyncResult:
    """Examine SQLite and optionally hash known files; private per-item scope."""
    if type(verify_local_files) is not bool:
        return _refused("invalid_options")
    if _only_item_id is not None and (type(_only_item_id) is not str or not _only_item_id):
        return _refused("invalid_options")
    account = _Binding(
        getattr(account, "provider_id", None),
        getattr(account, "identity_id", None),
        getattr(account, "credential_id", None),
        getattr(account, "unique_id", None),
    )
    if not _valid_binding(broker, account, source_id):
        return _refused("invalid_source")

    identity = account.identity_id
    credential = account.credential_id
    account_id = account.unique_id
    roots = _identity_root(broker.service, identity)
    if not roots:
        return _refused("invalid_directory")

    def scope_current() -> bool:
        # Directory ownership/configuration can change independently of the
        # connection. A verified file under an old root is not current proof.
        return (
            _valid_binding(broker, account, source_id)
            and _identity_root(broker.service, identity) == roots
        )

    try:
        storage = broker.service.storage
        evidence = storage.source_materialization_evidence(
            provider_id="numilog", credential_id=credential,
            account_id=account_id, identity_id=identity, max_rows=_MAX_ROWS,
        )
        rights = evidence["entitlements"]
        ledger = evidence["acquisitions"]
        placements = evidence["placements"]
        artifacts = evidence["artifacts"]
        if not scope_current():
            return _refused("source_changed")

        counts = dict.fromkeys(_COUNT_NAMES, 0)

        def note(item, gate, **facts):
            # Only the worker's already selected purchase is traced here.
            # Passive whole-library previews keep their existing behavior.
            if _only_item_id is not None:
                local_proof(account, source_id, item, gate, **facts)

        scoped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rights:
            if _only_item_id is not None and row.get("item_id") != _only_item_id:
                continue
            if (row.get("identity_id") != identity
                    or row.get("credential_id") != credential
                    or row.get("account_id") != account_id
                    or row.get("entitlement_type") != "owned"
                    or row.get("state") != "active"):
                continue
            metadata = row.get("metadata")
            if (not isinstance(metadata, dict)
                    or row.get("expires_at") or row.get("loan_id")
                    or metadata.get("source") != "numilog.authenticated_library"
                    or not _item_id(row.get("item_id"), credential)):
                counts["ignored_unattested_rights"] += 1
                continue
            scoped[row["item_id"]].append(row)
        counts["owned_purchases"] = len(scoped)

        acquired: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in ledger:
            if (row.get("credential_id") == credential
                    and _item_id(row.get("item_id"), credential)):
                acquired[str(row["item_id"])].append(row)

        # A content-addressed artifact may legitimately have placements
        # for several identities, accounts or purchases. Only placements
        # attested for this exact purchase can resolve its local copy.
        linked: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in placements:
            values = (
                row.get("artifact_id"), row.get("identity_id"),
                row.get("provider_id"), row.get("source_item_id"),
            )
            if all(type(value) is str and value for value in values):
                linked[values].append(row)
        artifact_by_id = {
            row.get("artifact_id"): row for row in artifacts
            if type(row.get("artifact_id")) is str
        }

        for item_id, item_rights in scoped.items():
            if len(item_rights) != 1:
                note(item_id, "multiple_rights", right_count=len(item_rights))
                counts["needs_reconciliation"] += 1
                continue
            right = item_rights[0]
            metadata = right["metadata"]
            acquisitions = acquired.get(item_id, [])
            if not acquisitions:
                note(item_id, "receipt_missing")
                if metadata.get("downloadable") is False:
                    counts["not_downloadable"] += 1
                else:
                    counts["potentially_missing"] += 1
                    # A missing ledger is NOT proof that a signed fulfillment
                    # link, ADEPT or format is currently available.
                    counts["candidate_unverified"] += 1
                continue

            if len(acquisitions) != 1:
                note(item_id, "multiple_receipts", receipt_count=len(acquisitions))
                counts["needs_reconciliation"] += 1
                continue
            acquisition = acquisitions[0]
            if (acquisition.get("identity_id") != identity
                    or acquisition.get("account_id") != account_id
                    or acquisition.get("entitlement_id") != right.get("entitlement_id")):
                note(item_id, "receipt_scope_mismatch", acquisition_id=acquisition.get("acquisition_id"))
                counts["needs_reconciliation"] += 1
                continue
            if acquisition.get("status") in {"attention_required", "blocked"}:
                note(item_id, "receipt_attention", acquisition_id=acquisition.get("acquisition_id"),
                     receipt_status=acquisition.get("status"))
                counts["attention_required"] += 1
                continue
            if acquisition.get("status") != "complete":
                note(item_id, "receipt_incomplete", acquisition_id=acquisition.get("acquisition_id"),
                     receipt_status=acquisition.get("status"))
                counts["needs_reconciliation"] += 1
                continue

            artifact_id = acquisition.get("artifact_id")
            matched = linked.get((artifact_id, identity, "numilog", item_id), [])
            artifact = artifact_by_id.get(artifact_id)
            if (type(artifact_id) is not str or len(matched) != 1
                    or not isinstance(artifact, dict)):
                note(item_id, "placement_or_artifact_missing", artifact_id=artifact_id,
                     matching_placements=len(matched), acquisition_id=acquisition.get("acquisition_id"))
                counts["needs_reconciliation"] += 1
                continue
            placement = matched[0]
            sha = artifact.get("sha256")
            size = artifact.get("size")
            if (placement.get("identity_id") != identity
                    or placement.get("provider_id") != "numilog"
                    or placement.get("source_item_id") != item_id
                    or placement.get("state") != "present"
                    or not _path_in_identity(placement.get("path"), roots)
                    or type(sha) is not str or _SHA.fullmatch(sha) is None
                    or artifact_id != "sha256:" + sha
                    # ACSM, HTML, unknown blobs and bundles are not readable
                    # local books, even with an exact SHA and a completed ledger.
                    or artifact.get("kind") != "file"
                    # Storage can normalize malformed persisted kind metadata
                    # into "file" for legacy readers. Never treat an explicit
                    # invalid artifact_kind as verified local ebook evidence.
                    or ("artifact_kind" in artifact.get("metadata", {})
                        and artifact["metadata"]["artifact_kind"] != "file")
                    or type(artifact.get("media_format")) is not str
                    or artifact["media_format"].casefold() not in {"epub", "kepub", "pdf"}
                    or type(size) is not int or size <= 0):
                note(item_id, "placement_or_artifact_invalid", artifact_id=artifact_id,
                     placement_id=placement.get("placement_id"), path=placement.get("path"),
                     placement_identity_id=placement.get("identity_id"),
                     placement_provider_id=placement.get("provider_id"),
                     placement_item_id=placement.get("source_item_id"),
                     placement_state=placement.get("state"), sha256=sha, size=size,
                     artifact_kind=artifact.get("kind"), media_format=artifact.get("media_format"),
                     identity_roots=[str(root) for root in roots])
                counts["needs_reconciliation"] += 1
                continue

            counts["already_local_linked"] += 1
            if not verify_local_files:
                counts["needs_file_verification"] += 1
                continue
            # Stop at the next file boundary after a reassignment/revocation;
            # do not continue reading a former owner's entire library only
            # to discard the result at the end. An in-flight hash is not a
            # transaction lease: its result is rejected at the next check.
            if not scope_current():
                return _refused("source_changed")
            observed = _read_hash_within(placement.get("path"), roots)
            if observed is None or observed[:2] != (sha, size):
                note(item_id, "file_unavailable" if observed is None else "file_fingerprint_mismatch",
                     artifact_id=artifact_id, placement_id=placement.get("placement_id"),
                     path=placement.get("path"), expected_sha256=sha, expected_size=size,
                     observed_sha256=observed[0] if observed is not None else None,
                     observed_size=observed[1] if observed is not None else None)
                counts["already_local_linked"] -= 1
                counts["needs_reconciliation"] += 1
            else:
                note(item_id, "file_fingerprint_verified", artifact_id=artifact_id,
                     placement_id=placement.get("placement_id"), path=placement.get("path"),
                     observed_sha256=observed[0], observed_size=observed[1])
                counts["already_local_verified"] += 1

        # If a source was disabled/reassigned during DB reads, no preview can
        # be considered scoped, even though nothing was downloaded or stored.
        if not scope_current():
            return _refused("source_changed")
        return SyncResult(ok=True, changed=0, facts={
            "phase": "local_materialization", "status": "preview_only",
            "read_only": True, "offline": True, "acquisition_authorized": False,
            "schedule_available": False, "confirmation_available": False,
            "manifest_refreshed": False, "requires_manifest_validation": True,
            "files_verified": verify_local_files,
            "known_item_count": counts["owned_purchases"],
            "counts": counts,
        })
    except Cancelled:
        raise
    except ValueError as exc:
        if _only_item_id is not None:
            local_proof(account, source_id, _only_item_id, "preview_failed", exc_info=True)
        if str(exc) == "evidence_budget_exhausted":
            return _refused("evidence_budget_exhausted")
        return _refused("preview_unavailable")
    except Exception:
        if _only_item_id is not None:
            local_proof(account, source_id, _only_item_id, "preview_failed", exc_info=True)
        return _refused("preview_unavailable")
