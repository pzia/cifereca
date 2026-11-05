"""P0 #3185: explicitly confirmed, non-destructive legacy Placement adoption.

A historical provider/item label is not an account. Only an exact completed
path receipt, published source proof, uniquely bound account, current physical
bytes and a bounded inventory permit dropping that *obsolete label*.

This does not relink a source, move a path, delete a file or retire a Placement.
Subsequent duplicate cleanup still uses the canonical supervisor separately.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Literal

from .canonical_legacy_scope_review import _completed_path_receipt_evidence
from .canonical_publication import _same_file
from .canonical_recovery import _lock_ok
from .convergence_verified_presence import _verified_copy
from .duplicate_maintenance_guard import (
    _mentions_exact_path, canonical_metadata_mentions_path,
)
from .library_declared_links import _records
from .missing_placement_retirement import _directory_signature

_LIMIT = 100_000
_MAX_JSON = 65536
_QUERY = (
    "SELECT path,identity_id,artifact_id,state,provider_id,source_item_id "
    "FROM placements WHERE placement_id=?"
)


class _Refusal(ValueError):
    pass


def _pairs(items):
    out = {}
    for key, value in items:
        if key in out:
            raise _Refusal("ambiguous_metadata")
        out[key] = value
    return out


def _json(raw, *, limit=_MAX_JSON):
    if type(raw) is not str or len(raw) > limit:
        raise _Refusal("unreadable_dependency")
    try:
        obj = json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, TypeError, RecursionError) as error:
        raise _Refusal("unreadable_dependency") from error
    if type(obj) is not dict:
        raise _Refusal("unreadable_dependency")
    return obj


def _bounded(db, sql, params=(), *, budget=_LIMIT):
    # No truncated scan may be interpreted as absence of references.
    for index, item in enumerate(db.execute(sql, params)):
        if index >= budget:
            raise _Refusal("dependency_budget")
        yield item


def _references(db, *, candidate_id, candidate, artifact_size, owner_scope):
    """Recognize operational references, never edit or move them.

    Candidate bytes and path remain in place after adoption. Typed catalogue
    and complete backup references may continue pointing at that same copy.
    Unknown paths/metadata and dependencies bound to different owners block.
    """
    path, owner, artifact, _, provider, edition = candidate
    credential, account = owner_scope
    catalogue = backup = 0
    for placement, identity, linked_artifact, linked_provider, linked_item, raw in _bounded(
        db, "SELECT placement_id,identity_id,artifact_id,provider_id,"
        "item_id,metadata_json FROM catalog_entries"
    ):
        meta = _json(raw, limit=1024 * 1024)
        names_path = _mentions_exact_path(meta, path)
        if placement != candidate_id and not names_path:
            continue
        if (placement != candidate_id or identity != owner
                or linked_artifact != artifact
                or linked_provider != provider or linked_item != edition
                or meta.get("path") not in (None, "", path)
                or any(_mentions_exact_path(item, path)
                       for key, item in meta.items() if key != "path")):
            raise _Refusal("catalogue_scope_unverified")
        catalogue += 1
        if catalogue > 128:
            raise _Refusal("dependency_budget")

    for row in _bounded(
        db, "SELECT provider_id,credential_id,item_id,identity_id,"
        "account_id,status,sha256,size FROM backup_items WHERE local_path=?",
        (path,), budget=128,
    ):
        p, c, item, identity, a, status, digest, size = row
        if ((p, c, item, identity, a, status, digest, size)
                != (provider, credential, edition, owner, account, "complete",
                    artifact[7:], artifact_size)):
            raise _Refusal("backup_scope_unverified")
        backup += 1

    # The matching ledger receipt is validated separately with strict
    # SourceKey/stage ID/OriginLink proof and a global path-collision scan.
    # The remaining namespaces have no approved path-migration semantics.
    if canonical_metadata_mentions_path(db, path) is not False:
        raise _Refusal("opaque_reference")
    for (raw,) in _bounded(db, "SELECT metadata_json FROM artifact_variants"):
        if _mentions_exact_path(_json(raw, limit=1024 * 1024), path):
            raise _Refusal("opaque_reference")
    for (raw,) in _bounded(db, "SELECT value FROM metadata"):
        if type(raw) is not str or len(raw) > 1024 * 1024:
            raise _Refusal("unreadable_dependency")
        if path in raw:
            raise _Refusal("opaque_reference")
        if "\\u" in raw or "\\/" in raw:
            try:
                decoded = json.loads(raw, object_pairs_hook=_pairs)
            except (ValueError, TypeError, RecursionError) as error:
                raise _Refusal("unreadable_dependency") from error
            if (_mentions_exact_path(decoded, path)
                    or path in json.dumps(decoded, ensure_ascii=False)):
                raise _Refusal("opaque_reference")
    for declaration in _records(db):
        if (declaration["state"] == "active"
                and declaration["file"]["path"] == path):
            raise _Refusal("active_declaration")
    return catalogue, backup


def _inspect(db, *, library_root, candidate_id, keeper_id):
    candidate = db.execute(_QUERY, (candidate_id,)).fetchone()
    keeper = db.execute(_QUERY, (keeper_id,)).fetchone()
    if candidate is None or keeper is None:
        raise _Refusal("placement_missing")
    if candidate[4:] == (None, None):
        raise _Refusal("already_canonical")
    if (candidate[1:4] != keeper[1:4] or candidate[3] != "present"
            or candidate[0] == keeper[0] or keeper[4:] != (None, None)
            or any(type(value) is not str or not value
                   for value in (candidate[0], candidate[1], candidate[2],
                                 candidate[4], candidate[5], keeper[0]))):
        raise _Refusal("scope_mismatch")
    path, owner, artifact_id, _, provider, edition = candidate
    artifact = db.execute(
        "SELECT sha256,size FROM artifacts WHERE artifact_id=?", (artifact_id,)
    ).fetchone()
    if (artifact is None or type(artifact[0]) is not str
            or len(artifact[0]) != 64
            or any(ch not in "0123456789abcdef" for ch in artifact[0])
            or artifact_id != "sha256:" + artifact[0]
            or type(artifact[1]) is not int or artifact[1] <= 0):
        raise _Refusal("artifact_unverified")
    directories = list(_bounded(
        db, "SELECT identity_id,directory_name FROM identities"
    ))
    owners = [directory for identity, directory in directories
              if identity == owner]
    if (len(owners) != 1 or type(owners[0]) is not str or not owners[0]
            or sum(d == owners[0] for _, d in directories) != 1):
        raise _Refusal("profile_ambiguous")
    registered = frozenset(d for _, d in directories if type(d) is str and d)
    # Pin the real directory inode as well as the two file inodes. The
    # library/profile directory may be swapped while identical hardlinks
    # remain readable at the old paths; a byte hash alone misses that swap.
    if (owners[0] in ("", ".", "..") or "/" in owners[0]
            or "\\" in owners[0]):
        raise _Refusal("profile_ambiguous")
    try:
        physical_root = library_root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as error:
        raise _Refusal("profile_ambiguous") from error
    signatures = (
        _directory_signature(physical_root),
        _directory_signature(physical_root / owners[0]),
    )
    if any(value is None for value in signatures):
        raise _Refusal("profile_ambiguous")
    observations = []
    for raw in (candidate[0], keeper[0]):
        file = Path(raw)
        try:
            before = os.stat(file, follow_symlinks=False)
        except OSError as error:
            raise _Refusal("physical_unverified") from error
        if (not _verified_copy(
                raw, root=library_root, owner_directory=owners[0],
                registered_directories=registered, size=artifact[1],
                sha256=artifact[0],
        ) or not _same_file(file, before)):
            raise _Refusal("physical_unverified")
        observations.append((file, before))

    matches = list(_bounded(
        db, "SELECT s.credential_id,s.account_id "
        "FROM origin_links o JOIN source_references s "
        "ON s.source_reference_id=o.source_reference_id "
        "WHERE o.origin_kind='provider' AND o.identity_id=? "
        "AND o.artifact_id=? AND s.identity_id=? AND s.provider_id=? "
        "AND s.edition_id=? AND s.evidence_kind='provider_response' "
        "AND typeof(o.evidence_ref)='text' AND o.evidence_ref!=''",
        (owner, artifact_id, owner, provider, edition), budget=256,
    ))
    scopes = set(matches)
    if len(scopes) != 1:
        raise _Refusal("source_scope_unverified")
    credential, account = next(iter(scopes))
    if (type(credential) is not str or not credential
            or type(account) is not str or not account):
        raise _Refusal("source_scope_unverified")
    try:
        receipts = _completed_path_receipt_evidence(
            db, path=path, identity=owner, artifact=artifact_id,
            provider=provider, edition=edition, credential=credential,
            account=account,
        )
    except (ValueError, TypeError, sqlite3.Error) as error:
        raise _Refusal("receipt_unverified") from error
    if receipts != 1:
        raise _Refusal("receipt_unverified")
    catalogue, backup = _references(
        db, candidate_id=candidate_id, candidate=candidate,
        artifact_size=artifact[1], owner_scope=(credential, account),
    )
    if (any(not _same_file(file, before) for file, before in observations)
            or signatures != (
                _directory_signature(physical_root),
                _directory_signature(physical_root / owners[0]),
            )):
        raise _Refusal("physical_changed_during_review")
    # This opaque version stamp is not authority: the commit ALSO requires
    # confirmation, the genuine shared lock, and full fresh revalidation.
    # It refuses a swapped directory/file or a changed source between the
    # operator's plan and the final SQL transaction.
    fingerprint = [
        str(physical_root), signatures, candidate, keeper, artifact,
        owners[0], [tuple(int(getattr(state, field)) for field in (
            "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"))
            for _file, state in observations],
        credential, account, catalogue, backup,
    ]
    token = hashlib.sha256(json.dumps(
        fingerprint, ensure_ascii=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return catalogue, backup, observations, physical_root, owners[0], signatures, token


@dataclass(frozen=True, slots=True)
class LegacyAdoptionResult:
    status: Literal["review_required", "blocked", "migrated"]
    reason: str
    candidate_placement_id: str
    keeper_placement_id: str
    catalogue_references: int = 0
    backup_references: int = 0
    completed_path_receipts: int = 0
    review_token: str = ""

    @property
    def may_delete(self) -> bool:
        return False


def _validate(library_root, candidate_id, keeper_id):
    return (
        isinstance(library_root, Path)
        and type(candidate_id) is str and 0 < len(candidate_id) <= 256
        and type(keeper_id) is str and 0 < len(keeper_id) <= 256
        and candidate_id != keeper_id
    )


def adopt_verified_legacy_placement(
    storage, *, library_root: Path, candidate_placement_id: str,
    keeper_placement_id: str, action: Literal["preview", "commit"] = "preview",
    confirmed: bool = False, shared_lock=None,
    expected_review_token: str = "",
) -> LegacyAdoptionResult:
    """Review or atomically clear ONLY the obsolete provider/item label.

    No path is rewritten: the two existing copies, catalogue, backup, receipt,
    ADEPT and provenance rows remain unchanged. A subsequent deduplication
    requires a *separate* explicit confirmation under the Core supervisor.
    """
    if not _validate(library_root, candidate_placement_id, keeper_placement_id):
        raise ValueError("legacy_adoption_invalid_request")
    if action not in ("preview", "commit"):
        raise ValueError("legacy_adoption_invalid_action")
    if action == "commit" and (confirmed is not True or not _lock_ok(shared_lock)):
        raise ValueError("legacy_adoption_confirmation_and_lock_required")

    def report(status, reason, *, catalogue=0, backup=0, receipts=0,
               token=""):
        return LegacyAdoptionResult(
            status, reason, candidate_placement_id, keeper_placement_id,
            catalogue, backup, receipts, token,
        )

    try:
        if storage.schema_version() < 14:
            return report("blocked", "schema_unavailable")
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN IMMEDIATE" if action == "commit" else "BEGIN")
            (catalogue, backup, observations, physical_root,
             owner_folder, signatures, current_token) = _inspect(
                db, library_root=library_root,
                candidate_id=candidate_placement_id,
                keeper_id=keeper_placement_id,
            )
            if action == "preview":
                return report("review_required", "exact_path_receipt_attested",
                              catalogue=catalogue, backup=backup, receipts=1,
                              token=current_token)
            if (type(expected_review_token) is not str
                    or len(expected_review_token) != 64
                    or expected_review_token != current_token):
                raise _Refusal("stale_plan")
            changed = db.execute(
                "UPDATE placements SET provider_id=NULL,source_item_id=NULL "
                "WHERE placement_id=? AND identity_id=(SELECT identity_id "
                "FROM placements WHERE placement_id=?) "
                "AND provider_id IS NOT NULL AND source_item_id IS NOT NULL "
                "AND state='present'",
                (candidate_placement_id, keeper_placement_id),
            )
            if changed.rowcount != 1:
                raise _Refusal("concurrent_placement_change")
            if (any(not _same_file(file, before) for file, before in observations)
                    or signatures != (
                        _directory_signature(physical_root),
                        _directory_signature(physical_root / owner_folder),
                    )):
                raise _Refusal("physical_changed_during_commit")
            # SQLite rollback covers the entire adoption on exceptions.
        return report("migrated", "legacy_label_cleared_no_file_mutation",
                      catalogue=catalogue, backup=backup, receipts=1)
    except _Refusal as error:
        return report("blocked", str(error))
    except (sqlite3.Error, OSError, ValueError, TypeError, RuntimeError,
            RecursionError):
        return report("blocked", "inspection_or_commit_unavailable")


__all__ = ["LegacyAdoptionResult", "adopt_verified_legacy_placement"]
