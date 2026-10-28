"""#3275/#3171: explicit local A→B transfer of *bytes*, never ownership.

A transfer is a one-off physical copy. It creates an independent Placement in
the recipient and two contextual OriginLinks (transfer + cifereca). It never
copies a provider SourceReference, entitlements, ADEPT grants or acquisition
permissions; it is NOT a source of ongoing Sync replication.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import time

from ..catalog import LocalCatalog
from .canonical_publication import _physical_target, _same_file, _stable


@dataclass(frozen=True, slots=True)
class TransferPublication:
    artifact_id: str
    placement_id: str
    internal_reference_id: str
    transfer_origin_id: str
    cifereca_origin_id: str
    changed: bool


def publish_completed_local_transfer(
    storage,
    *,
    library_root: Path,
    from_identity_id: str,
    to_identity_id: str,
    from_path: Path,
    to_path: Path,
    transfer_id: str,
    transfer_receipt_ref: str,
    explicitly_authorized: bool,
) -> TransferPublication:
    """Commit an explicitly completed, distinct A→B physical copy in v14.

    The caller must have finished the copy and its own user authorization under
    the shared acquisition lock. It must never call this from a passive Sync
    plan, an automatic purchase restoration or a provider plugin. There is NO
    file copy, network I/O or license transfer here.
    """
    def token(x):
        return (type(x) is str and bool(x) and len(x) <= 256
                and not any(c in x for c in "\x00\r\n"))

    if (type(explicitly_authorized) is not bool or not explicitly_authorized
            or not token(from_identity_id) or not token(to_identity_id)
            or from_identity_id == to_identity_id or not token(transfer_id)
            or not token(transfer_receipt_ref)
            or not isinstance(library_root, Path)
            or not isinstance(from_path, Path) or not isinstance(to_path, Path)):
        raise ValueError("canonical_transfer_authorization_required")

    try:
        profiles = storage.identities()
        source_dirs = [
            p.get("directory_name") for p in profiles
            if p.get("identity_id") == from_identity_id
        ]
        recipient_dirs = [
            p.get("directory_name") for p in profiles
            if p.get("identity_id") == to_identity_id
        ]
        if (len(source_dirs) != 1 or len(recipient_dirs) != 1
                or type(source_dirs[0]) is not str
                or type(recipient_dirs[0]) is not str
                or source_dirs[0] == recipient_dirs[0]):
            raise ValueError("canonical_transfer_profile_ambiguous")
        original = _physical_target(from_path, library_root, source_dirs[0])
        received = _physical_target(to_path, library_root, recipient_dirs[0])
        before_a = os.stat(original, follow_symlinks=False)
        before_b = os.stat(received, follow_symlinks=False)
        if (not stat.S_ISREG(before_a.st_mode)
                or not stat.S_ISREG(before_b.st_mode)
                or (before_a.st_dev, before_a.st_ino) ==
                    (before_b.st_dev, before_b.st_ino)
                or before_a.st_size != before_b.st_size):
            raise ValueError("canonical_transfer_independent_copy_required")
        format_a = LocalCatalog._media_format(original)
        format_b = LocalCatalog._media_format(received)
        if format_a not in ("epub", "kepub", "pdf") or (
            format_b not in ("epub", "kepub", "pdf")
        ):
            raise ValueError("canonical_transfer_format_unsupported")
        for path, fmt in ((original, format_a), (received, format_b)):
            valid, _reason = LocalCatalog._validate(path, fmt)
            if not valid:
                raise ValueError("canonical_transfer_invalid_bytes")
        sha = LocalCatalog.sha256(original)
        if (LocalCatalog.sha256(received) != sha
                or not _same_file(original, before_a)
                or not _same_file(received, before_b)):
            raise ValueError("canonical_transfer_bytes_mismatch")
    except OSError as exc:
        raise ValueError("canonical_transfer_missing_physical_copy") from exc

    artifact_id = "sha256:" + sha
    # The internal SourceReference identifies this *particular donor* and
    # transfer, not merely a recipient and arbitrarily reused transfer_id.
    # Two donors providing the same bytes to B remain separate histories.
    transfer_right_id = _stable(
        "transfer-right", from_identity_id, to_identity_id,
        artifact_id, transfer_id,
    )
    internal_id = _stable(
        "source", to_identity_id, "cifereca", "local", to_identity_id,
        "transfer", artifact_id, transfer_right_id, "internal",
    )
    placement_id = _stable("placement", str(received))
    transfer_origin = _stable(
        "origin", "transfer", from_identity_id, to_identity_id,
        artifact_id, internal_id, transfer_receipt_ref,
    )
    cifereca_origin = _stable(
        "origin", "cifereca", to_identity_id, artifact_id,
        internal_id, transfer_receipt_ref,
    )
    now = int(time.time())
    changed = False

    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # Filesystem path ownership and source Placement must not have
        # changed after our preliminary hash. The exact Artifact is reused,
        # never copied by assigning another profile an existing path.
        for identity, directory in (
            (from_identity_id, source_dirs[0]),
            (to_identity_id, recipient_dirs[0]),
        ):
            current = db.execute(
                "SELECT directory_name FROM identities WHERE identity_id=?",
                (identity,),
            ).fetchone()
            if current != (directory,):
                raise ValueError("canonical_transfer_profile_rebound")
        existing_artifact = db.execute(
            "SELECT sha256,size FROM artifacts WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        if existing_artifact != (sha, before_a.st_size):
            raise ValueError("canonical_transfer_source_artifact_missing")
        original_placement = db.execute(
            "SELECT artifact_id,identity_id,state FROM placements WHERE path=?",
            (str(original),),
        ).fetchone()
        if original_placement != (artifact_id, from_identity_id, "present"):
            raise ValueError("canonical_transfer_source_placement_unattested")
        if not _same_file(original, before_a) or not _same_file(received, before_b):
            raise ValueError("canonical_transfer_copy_changed")

        coordinates = (
            to_identity_id, "cifereca", "local", to_identity_id, "transfer",
            artifact_id, transfer_right_id, "internal",
        )
        ref = db.execute(
            "SELECT identity_id,provider_id,credential_id,account_id,"
            "branch_id,edition_id,right_id,reference_kind "
            "FROM source_references WHERE source_reference_id=?",
            (internal_id,),
        ).fetchone()
        if ref is None:
            db.execute(
                "INSERT INTO source_references(source_reference_id,identity_id,"
                "provider_id,credential_id,account_id,branch_id,edition_id,"
                "right_id,reference_kind,state,synchronizable,evidence_kind,"
                "evidence_ref,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (internal_id, *coordinates, "unknown", 0,
                 "completed_local_transfer", transfer_receipt_ref, now, now),
            )
            changed = True
        elif ref != coordinates:
            raise ValueError("canonical_transfer_reference_collision")

        existing_placement = db.execute(
            "SELECT placement_id,identity_id,artifact_id,state FROM placements "
            "WHERE path=?", (str(received),),
        ).fetchone()
        if existing_placement is None:
            db.execute(
                "INSERT INTO placements(placement_id,artifact_id,path,identity_id,"
                "state,last_seen_at,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (placement_id, artifact_id, str(received), to_identity_id,
                 "present", now, "{}", now, now),
            )
            changed = True
        else:
            placement_id = existing_placement[0]
            if (existing_placement[1] not in (None, to_identity_id)
                    or existing_placement[2] not in (None, artifact_id)):
                raise ValueError("canonical_transfer_placement_conflict")
            if (existing_placement[1] is None or existing_placement[2] is None
                    or existing_placement[3] != "present"):
                db.execute(
                    "UPDATE placements SET identity_id=?,artifact_id=?,"
                    "state='present',last_seen_at=?,updated_at=? "
                    "WHERE placement_id=?",
                    (to_identity_id, artifact_id, now, now, placement_id),
                )
                changed = True

        for link_id, kind, from_owner in (
            (transfer_origin, "transfer", from_identity_id),
            (cifereca_origin, "cifereca", None),
        ):
            expected = (
                kind, to_identity_id, artifact_id, internal_id,
                transfer_receipt_ref, from_owner,
            )
            link = db.execute(
                "SELECT origin_kind,identity_id,artifact_id,"
                "source_reference_id,evidence_ref,from_identity_id "
                "FROM origin_links WHERE origin_link_id=?", (link_id,),
            ).fetchone()
            if link is None:
                db.execute(
                    "INSERT INTO origin_links(origin_link_id,origin_kind,"
                    "identity_id,artifact_id,source_reference_id,evidence_ref,"
                    "from_identity_id,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (link_id, *expected, now, now),
                )
                changed = True
            elif link != expected:
                raise ValueError("canonical_transfer_origin_collision")
        if not _same_file(original, before_a) or not _same_file(received, before_b):
            raise ValueError("canonical_transfer_copy_changed")
    return TransferPublication(
        artifact_id, placement_id, internal_id,
        transfer_origin, cifereca_origin, changed,
    )


__all__ = ["TransferPublication", "publish_completed_local_transfer"]
