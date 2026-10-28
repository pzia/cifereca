"""#3171: post-acquisition canonical publication; NO provider/network I/O.

A transaction writes the final content-addressed Artifact, per-profile
Placement, exact SourceReference and independent provider/ADEPT OriginLinks.
Only a Core executor with an already-admitted download may call this.
The receipt is an input from *completed* acquisition, never inferred from
right inventory, filename, ISBN, matching SHA or a different profile.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import time

from .source_convergence import SourceKey
from ..catalog import LocalCatalog
from ..plugin_api.provider_rights_observation import (
    ProviderRightObservation, ProviderRightsSnapshot,
)


def _stable(prefix: str, *values: str) -> str:
    return prefix + ":" + hashlib.sha256(
        json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _same_file(path: Path, before: os.stat_result) -> bool:
    try:
        after = os.stat(path, follow_symlinks=False)
        return stat.S_ISREG(after.st_mode) and all(
            getattr(before, key) == getattr(after, key)
            for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        )
    except OSError:
        return False


def _physical_target(path: Path, root: Path, directory: str) -> Path:
    # Core passes the configured library root. Symlinks within a profile
    # cannot borrow a physical copy from another profile or outside the root.
    if not directory or directory in {".", ".."} or "/" in directory or "\\" in directory:
        raise ValueError("invalid_canonical_profile_directory")
    root_alias = Path(os.path.abspath(root.expanduser()))
    physical = Path(os.path.abspath(path.expanduser()))
    try:
        relative = physical.relative_to(root_alias)
    except ValueError as exc:
        raise ValueError("canonical_path_outside_library") from exc
    if len(relative.parts) < 2 or relative.parts[0] != directory:
        raise ValueError("canonical_profile_path_mismatch")
    current = root_alias
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError("canonical_profile_symlink")
    resolved_root = root_alias.resolve()
    resolved_path = physical.resolve(strict=True)
    if not resolved_path.is_relative_to(resolved_root / directory):
        raise ValueError("canonical_profile_path_mismatch")
    return resolved_path


@dataclass(frozen=True, slots=True)
class PublicationResult:
    artifact_id: str
    placement_id: str
    source_reference_id: str
    origin_link_ids: tuple[str, ...]
    changed: bool


def publish_completed_acquisition(
    storage,
    *,
    library_root: Path,
    path: Path,
    source: SourceKey,
    snapshot: ProviderRightsSnapshot,
    final_sha256: str,
    delivery_receipt_ref: str,
    adept_receipt_ref: str = "",
) -> PublicationResult:
    """Publish an *already completed* authorized acquisition into schema v14.

    The caller must retain the exclusive Sync/acquisition lock and independently
    revalidate account/session/epoch, offer, consent, final ADEPT result and
    proof that delivery_receipt_ref belongs to this exact source. This writer
    never dispatches acquisition or invents those authorizations. Its own
    guards check exact positive provider scope, connected owner, physical
    profile bytes, and SQLite atomicity; no legacy backup_items writes.
    """
    if (type(source) is not SourceKey or type(snapshot) is not ProviderRightsSnapshot
            or snapshot.scope != source.scope or snapshot.branch != source.branch):
        raise ValueError("canonical_source_scope_mismatch")
    row: ProviderRightObservation | None = snapshot.exact(
        source.edition_id, source.right_id, source.kind,
    )
    if (row is None or row.state != "active" or not row.provider_attested
            or snapshot.status not in ("success", "partial")):
        raise ValueError("canonical_active_provider_right_required")
    if (type(delivery_receipt_ref) is not str
            or not delivery_receipt_ref or delivery_receipt_ref == row.evidence_ref
            or len(delivery_receipt_ref) > 1024
            or any(c in delivery_receipt_ref for c in "\x00\r\n")
            or type(adept_receipt_ref) is not str
            or len(adept_receipt_ref) > 1024
            or any(c in adept_receipt_ref for c in "\x00\r\n")):
        raise ValueError("canonical_completed_delivery_receipt_required")
    if (type(final_sha256) is not str or len(final_sha256) != 64
            or any(c not in "0123456789abcdef" for c in final_sha256)):
        raise ValueError("canonical_final_sha_invalid")
    if not isinstance(library_root, Path) or not isinstance(path, Path):
        raise ValueError("canonical_path_invalid")

    # This is non-authoritative preliminary I/O. The authoritative owner
    # binding and file identity are rechecked *inside* the SQL transaction.
    try:
        identities = storage.identities()
        known = [i for i in identities if i.get("identity_id") == source.scope.identity_id]
        if len(known) != 1:
            raise ValueError("canonical_profile_unknown")
        directory = known[0].get("directory_name")
        if type(directory) is not str:
            raise ValueError("canonical_profile_unknown")
        target = _physical_target(path, library_root, directory)
        fmt = LocalCatalog._media_format(target)
        if fmt not in ("epub", "kepub", "pdf"):
            raise ValueError("canonical_final_format_unsupported")
        before = os.stat(target, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("canonical_file_not_regular")
        valid, reason = LocalCatalog._validate(target, fmt)
        if not valid:
            raise ValueError("canonical_final_file_invalid:" + str(reason))
        if LocalCatalog.sha256(target) != final_sha256 or not _same_file(target, before):
            raise ValueError("canonical_final_sha_mismatch")
    except OSError as exc:
        raise ValueError("canonical_final_file_unavailable") from exc

    scope = source.scope
    artifact_id = "sha256:" + final_sha256
    source_id = _stable(
        "source", scope.identity_id, scope.provider_id, scope.credential_id,
        scope.account_id, source.branch, source.edition_id, source.right_id,
        source.kind,
    )
    placement_id = _stable("placement", str(target))
    origin_specs = [
        ("provider", delivery_receipt_ref),
        *([("adept", adept_receipt_ref)] if adept_receipt_ref else []),
    ]
    origin_ids = tuple(
        _stable("origin", kind, scope.identity_id, artifact_id, source_id, proof)
        for kind, proof in origin_specs
    )
    now = int(time.time())
    changed = False
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # Do not rely on a caller's old account-to-credential binding.
        owner = db.execute(
            "SELECT identity_id,enabled FROM provider_connections "
            "WHERE provider_id=? AND credential_id=?",
            (scope.provider_id, scope.credential_id),
        ).fetchone()
        registered_profile = db.execute(
            "SELECT directory_name FROM identities WHERE identity_id=?",
            (scope.identity_id,),
        ).fetchone()
        if (owner != (scope.identity_id, 1)
                or registered_profile != (directory,)):
            raise ValueError("canonical_connection_or_profile_rebound")
        # An authenticated credential may serve several borrower IDs.
        # Require one exact registered account, not a credential-only match
        # or an opportunistic first item. Upstream primary '' is legal.
        account_rows = db.execute(
            "SELECT account_id FROM provider_accounts "
            "WHERE identity_id=? AND provider_id=? AND credential_id=?",
            (scope.identity_id, scope.provider_id, scope.credential_id),
        ).fetchall()
        if sum(a[0] == scope.account_id for a in account_rows) != 1:
            raise ValueError("canonical_account_not_registered")
        if not _same_file(target, before):
            raise ValueError("canonical_final_file_changed")

        artifact = db.execute(
            "SELECT sha256,size,media_format FROM artifacts WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        if artifact is None:
            db.execute(
                "INSERT INTO artifacts(artifact_id,sha256,size,media_format,"
                "mime_type,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (artifact_id, final_sha256, before.st_size, fmt,
                 "application/pdf" if fmt == "pdf" else "application/epub+zip",
                 "{}", now, now),
            )
            changed = True
        elif not (
            artifact[0] == final_sha256 and artifact[1] == before.st_size
            and (artifact[2] == fmt or {artifact[2], fmt} <= {"epub", "kepub"})
        ):
            # KEPUB and EPUB are both ZIP-based EPUB bytes. When an already
            # indexed identical file has another extension, preserve its
            # immutable SHA record instead of creating an artificial clash.
            raise ValueError("canonical_artifact_collision")

        coordinates = (
            scope.identity_id, scope.provider_id, scope.credential_id,
            scope.account_id, source.branch, source.edition_id,
            source.right_id, source.kind,
        )
        existing_source = db.execute(
            "SELECT identity_id,provider_id,credential_id,account_id,"
            "branch_id,edition_id,right_id,reference_kind "
            "FROM source_references WHERE source_reference_id=?", (source_id,),
        ).fetchone()
        if existing_source is None:
            db.execute(
                "INSERT INTO source_references(source_reference_id,identity_id,"
                "provider_id,credential_id,account_id,branch_id,edition_id,"
                "right_id,reference_kind,state,synchronizable,evidence_kind,"
                "evidence_ref,observed_at,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (source_id, *coordinates, "active", 1, row.evidence_kind,
                 row.evidence_ref, row.observed_at, now, now),
            )
            changed = True
        elif existing_source != coordinates:
            raise ValueError("canonical_source_reference_collision")

        existing_placement = db.execute(
            "SELECT placement_id,identity_id,artifact_id,state FROM placements "
            "WHERE path=?", (str(target),),
        ).fetchone()
        if existing_placement is None:
            db.execute(
                "INSERT INTO placements(placement_id,artifact_id,path,identity_id,"
                "provider_id,source_item_id,state,last_seen_at,metadata_json,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (placement_id, artifact_id, str(target), scope.identity_id,
                 None, None, "present", now, "{}", now, now),
            )
            changed = True
        else:
            placement_id = existing_placement[0]
            if existing_placement[1] not in (None, scope.identity_id) or (
                existing_placement[2] not in (None, artifact_id)
            ):
                raise ValueError("canonical_placement_scope_or_bytes_conflict")
            if existing_placement[1] is None or existing_placement[2] is None or existing_placement[3] != "present":
                db.execute(
                    "UPDATE placements SET artifact_id=?,identity_id=?,"
                    "state='present',last_seen_at=?,updated_at=? WHERE placement_id=?",
                    (artifact_id, scope.identity_id, now, now, placement_id),
                )
                changed = True

        for (kind, proof), link_id in zip(origin_specs, origin_ids):
            original = db.execute(
                "SELECT origin_kind,identity_id,artifact_id,source_reference_id,"
                "evidence_ref FROM origin_links WHERE origin_link_id=?",
                (link_id,),
            ).fetchone()
            expected = (kind, scope.identity_id, artifact_id, source_id, proof)
            if original is None:
                db.execute(
                    "INSERT INTO origin_links(origin_link_id,origin_kind,"
                    "identity_id,artifact_id,source_reference_id,evidence_ref,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (link_id, *expected, now, now),
                )
                changed = True
            elif original != expected:
                raise ValueError("canonical_origin_collision")
        if not _same_file(target, before):
            raise ValueError("canonical_final_file_changed")
    return PublicationResult(artifact_id, placement_id, source_id, origin_ids, changed)
