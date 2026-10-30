"""#3719/#3185: transactional catalogue relink of a verified canonical duplicate.

This is deliberately NOT a physical deduplication executor. Both files and
both Placements remain present after commit. OriginLinks, source rights and
historical delivery receipts are never rewritten or inferred from SHA.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

from .canonical_publication import _same_file
from .canonical_recovery import _lock_ok
from .convergence_verified_presence import _verified_copy
from .duplicate_maintenance_guard import _mentions_exact_path


def _unique_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("canonical_relink_duplicate_catalogue_field")
        value[key] = item
    return value


def relink_canonical_duplicate_catalogue(
    storage, *, library_root: Path, candidate_placement_id: str,
    keeper_placement_id: str, shared_lock,
) -> int:
    """Rebind only catalogue projections; never retire a file or placement.

    Requires the live acquisition interprocess lock. A new executor must
    separately transfer other operational references and verify both physical
    copies again before any later unlink. Failures roll back all catalogue
    mutations; this operation never changes provider/ADEPT OriginLinks.
    """
    if (not isinstance(library_root, Path) or not _lock_ok(shared_lock)
            or type(candidate_placement_id) is not str
            or type(keeper_placement_id) is not str
            or not candidate_placement_id or not keeper_placement_id
            or candidate_placement_id == keeper_placement_id):
        raise ValueError("canonical_relink_invalid_request")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        columns = ("SELECT path,identity_id,artifact_id,state,"
                   "provider_id,source_item_id FROM placements WHERE placement_id=?")
        candidate = db.execute(columns, (candidate_placement_id,)).fetchone()
        keeper = db.execute(columns, (keeper_placement_id,)).fetchone()
        if (candidate is None or keeper is None
                or candidate[1] != keeper[1] or candidate[2] != keeper[2]
                or candidate[3] != "present" or keeper[3] != "present"
                # Canonical publication leaves provider/item ownership on
                # SourceReferences, never on the shared physical Placement.
                or candidate[4:] != (None, None)
                or keeper[4:] != (None, None)
                or type(candidate[1]) is not str or not candidate[1]
                or type(candidate[2]) is not str
                or not candidate[2].startswith("sha256:")):
            raise ValueError("canonical_relink_scope_changed")
        artifact_id, identity_id = candidate[2], candidate[1]
        artifact = db.execute(
            "SELECT sha256,size FROM artifacts WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        if (artifact is None or type(artifact[0]) is not str
                or len(artifact[0]) != 64
                or any(ch not in "0123456789abcdef" for ch in artifact[0])
                or artifact_id != "sha256:" + artifact[0]
                or type(artifact[1]) is not int or artifact[1] <= 0):
            raise ValueError("canonical_relink_artifact_changed")
        directories = db.execute(
            "SELECT identity_id,directory_name FROM identities"
        ).fetchall()
        owners = [d for identity, d in directories if identity == identity_id]
        if (len(owners) != 1 or type(owners[0]) is not str
                or not owners[0]
                or sum(d == owners[0] for _, d in directories) != 1):
            raise ValueError("canonical_relink_owner_ambiguous")
        registered = frozenset(
            d for _, d in directories if type(d) is str and d
        )
        snapshots = []
        for raw in (candidate[0], keeper[0]):
            path = Path(raw)
            try:
                before = os.stat(path, follow_symlinks=False)
            except OSError as exc:
                raise ValueError("canonical_relink_physical_evidence_changed") from exc
            if (not _verified_copy(
                raw, root=library_root, owner_directory=owners[0],
                registered_directories=registered,
                size=artifact[1], sha256=artifact[0],
            ) or not _same_file(path, before)):
                raise ValueError("canonical_relink_physical_evidence_changed")
            snapshots.append((path, before))

        rows = db.execute(
            "SELECT catalog_entry_id,artifact_id,identity_id,metadata_json "
            "FROM catalog_entries WHERE placement_id=? LIMIT 129",
            (candidate_placement_id,),
        ).fetchall()
        if len(rows) > 128:
            raise ValueError("canonical_relink_catalogue_budget_exceeded")
        staged = []
        for catalog_id, catalog_artifact, catalog_identity, raw in rows:
            if (catalog_artifact != artifact_id
                    or catalog_identity != identity_id
                    or type(raw) is not str or len(raw) > 1024 * 1024):
                raise ValueError("canonical_relink_catalogue_scope_changed")
            try:
                meta = json.loads(raw, object_pairs_hook=_unique_keys)
            except (ValueError, TypeError, RecursionError) as exc:
                raise ValueError("canonical_relink_catalogue_unreadable") from exc
            if type(meta) is not dict or meta.get("path") not in (
                None, "", candidate[0],
            ):
                raise ValueError("canonical_relink_catalogue_path_conflict")
            if any(
                _mentions_exact_path(value, candidate[0])
                for key, value in meta.items() if key != "path"
            ):
                raise ValueError("canonical_relink_nested_path_dependency")
            if "path" in meta:
                meta["path"] = keeper[0]
            staged.append((
                catalog_id,
                json.dumps(meta, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")),
            ))
        # Catch a rebound after hashing and before changing any catalogue row.
        if any(not _same_file(path, before) for path, before in snapshots):
            raise ValueError("canonical_relink_physical_evidence_changed")
        now = int(time.time())
        for catalog_id, metadata in staged:
            changed = db.execute(
                "UPDATE catalog_entries SET placement_id=?,metadata_json=?,"
                "updated_at=? WHERE catalog_entry_id=? AND placement_id=?",
                (keeper_placement_id, metadata, now,
                 catalog_id, candidate_placement_id),
            )
            if changed.rowcount != 1:
                raise ValueError("canonical_relink_catalogue_changed")
        if any(not _same_file(path, before) for path, before in snapshots):
            raise ValueError("canonical_relink_physical_evidence_changed")
    return len(staged)


__all__ = ["relink_canonical_duplicate_catalogue"]
