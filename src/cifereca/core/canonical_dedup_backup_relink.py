"""#3719/#3185: migrate exact, current legacy backup path references only.

Both canonical Placements and BOTH physical copies remain intact. No new
supplier permission, SourceReference or OriginLink follows from matching SHA.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from .canonical_publication import _same_file
from .canonical_recovery import _lock_ok
from .convergence_verified_presence import _verified_copy


def relink_canonical_duplicate_backups(
    storage, *, library_root: Path, candidate_placement_id: str,
    keeper_placement_id: str, shared_lock,
) -> int:
    """Relink bounded attested backup rows under one SQL write transaction.

    This is strictly preparatory; the candidate Placement remains 'present'
    and its file MUST NOT be deleted by this method.
    """
    if (not isinstance(library_root, Path) or not _lock_ok(shared_lock)
            or type(candidate_placement_id) is not str or not candidate_placement_id
            or type(keeper_placement_id) is not str or not keeper_placement_id
            or candidate_placement_id == keeper_placement_id
            or storage.schema_version() < 14):
        raise ValueError("canonical_backup_relink_invalid_request")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        columns = ("SELECT path,identity_id,artifact_id,state,provider_id,"
                   "source_item_id FROM placements WHERE placement_id=?")
        candidate = db.execute(columns, (candidate_placement_id,)).fetchone()
        keeper = db.execute(columns, (keeper_placement_id,)).fetchone()
        if (candidate is None or keeper is None or candidate[0] == keeper[0]
                or candidate[1] != keeper[1] or candidate[2] != keeper[2]
                or candidate[3] != "present" or keeper[3] != "present"
                or candidate[4:] != (None, None)
                or keeper[4:] != (None, None)
                or type(candidate[1]) is not str or not candidate[1]
                or type(candidate[2]) is not str
                or not candidate[2].startswith("sha256:")):
            raise ValueError("canonical_backup_relink_scope_changed")
        artifact = db.execute(
            "SELECT sha256,size FROM artifacts WHERE artifact_id=?",
            (candidate[2],),
        ).fetchone()
        if (artifact is None or type(artifact[0]) is not str
                or len(artifact[0]) != 64
                or candidate[2] != "sha256:" + artifact[0]
                or type(artifact[1]) is not int or artifact[1] <= 0):
            raise ValueError("canonical_backup_relink_artifact_changed")
        directories = db.execute(
            "SELECT identity_id,directory_name FROM identities"
        ).fetchall()
        owners = [directory for identity, directory in directories
                  if identity == candidate[1]]
        if (len(owners) != 1 or type(owners[0]) is not str
                or not owners[0]
                or sum(directory == owners[0] for _, directory in directories) != 1):
            raise ValueError("canonical_backup_relink_owner_ambiguous")
        reserved = frozenset(directory for _, directory in directories
                             if type(directory) is str and directory)
        snapshots = []
        for raw in (candidate[0], keeper[0]):
            path = Path(raw)
            try:
                before = os.stat(path, follow_symlinks=False)
            except OSError as exc:
                raise ValueError("canonical_backup_relink_physical_changed") from exc
            if (not _verified_copy(
                    raw, root=library_root, owner_directory=owners[0],
                    registered_directories=reserved, size=artifact[1],
                    sha256=artifact[0])
                    or not _same_file(path, before)):
                raise ValueError("canonical_backup_relink_physical_changed")
            snapshots.append((path, before))

        # A local_path might belong to an old, unbound backup whose 'complete'
        # label/sha was reconstructed from historical inventory. Such a record
        # must not be redirected on its hash alone.
        rows = db.execute(
            "SELECT provider_id,credential_id,item_id,account_id,"
            "identity_id,sha256,size,status FROM backup_items "
            "WHERE local_path=? LIMIT 129", (candidate[0],),
        ).fetchall()
        if len(rows) > 128:
            raise ValueError("canonical_backup_relink_budget_exceeded")
        for provider, credential, item, account, identity, digest, size, status in rows:
            if (not all(type(x) is str and x for x in
                        (provider, credential, item, account))
                    or identity != candidate[1]
                    or digest != artifact[0] or size != artifact[1]
                    or status != "complete"):
                raise ValueError("canonical_backup_relink_receipt_untrusted")
            owner = db.execute(
                "SELECT identity_id,enabled FROM provider_connections "
                "WHERE provider_id=? AND credential_id=?",
                (provider, credential),
            ).fetchone()
            accounts = db.execute(
                "SELECT account_id FROM provider_accounts WHERE identity_id=? "
                "AND provider_id=? AND credential_id=?",
                (identity, provider, credential),
            ).fetchall()
            if (owner != (identity, 1)
                    or sum(found[0] == account for found in accounts) != 1):
                raise ValueError("canonical_backup_relink_account_rebound")
        if any(not _same_file(path, st) for path, st in snapshots):
            raise ValueError("canonical_backup_relink_physical_changed")
        now = int(time.time())
        for provider, credential, item, _account, _identity, *_ in rows:
            changed = db.execute(
                "UPDATE backup_items SET local_path=?,updated_at=? "
                "WHERE provider_id=? AND credential_id=? AND item_id=? "
                "AND local_path=?",
                (keeper[0], now, provider, credential, item, candidate[0]),
            )
            if changed.rowcount != 1:
                raise ValueError("canonical_backup_relink_receipt_changed")
        if any(not _same_file(path, st) for path, st in snapshots):
            raise ValueError("canonical_backup_relink_physical_changed")
    return len(rows)


__all__ = ["relink_canonical_duplicate_backups"]
