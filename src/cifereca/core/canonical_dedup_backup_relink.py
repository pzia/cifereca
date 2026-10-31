"""#3185/#3719: conservatively rebind attested legacy backup file paths.

backup_items is only a compatibility projection. Identical SHA is NOT
evidence of an owned right; canonical SourceReference + published OriginLink
must independently establish the exact source before changing the pointer.
This prepares, but never authorizes, physical deletion.
"""
from __future__ import annotations

import os
import stat
import time
from pathlib import Path

from .canonical_publication import _same_file
from .canonical_recovery import _lock_ok
from .convergence_verified_presence import _verified_copy


def relink_canonical_duplicate_backup_paths(
    storage, *, library_root: Path, candidate_placement_id: str,
    keeper_placement_id: str, shared_lock,
) -> int:
    """Rebind complete, source-attested backup_items paths; SQL only.

    All candidate-path rows must have exact, separately published provenance.
    Unknown ownership, NULL account, uncertain status, inconsistent digest or
    an ambiguous credential vetoes the ENTIRE transaction. The legacy row's
    status, source, borrower and entitlement are never changed.
    """
    if (not isinstance(library_root, Path) or not _lock_ok(shared_lock)
            or type(candidate_placement_id) is not str
            or type(keeper_placement_id) is not str
            or not 0 < len(candidate_placement_id) <= 256
            or not 0 < len(keeper_placement_id) <= 256
            or candidate_placement_id == keeper_placement_id):
        raise ValueError("canonical_backup_relink_invalid_request")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        query = (
            "SELECT path,identity_id,artifact_id,state,provider_id,source_item_id "
            "FROM placements WHERE placement_id=?"
        )
        candidate = db.execute(query, (candidate_placement_id,)).fetchone()
        keeper = db.execute(query, (keeper_placement_id,)).fetchone()
        if (candidate is None or keeper is None
                or candidate[0] == keeper[0]
                or candidate[1:4] != keeper[1:4]
                or candidate[3] != "present"
                or candidate[4:] != (None, None)
                or keeper[4:] != (None, None)
                or type(candidate[1]) is not str or not candidate[1]
                or type(candidate[2]) is not str):
            raise ValueError("canonical_backup_relink_scope_changed")
        identity, artifact_id = candidate[1:3]
        artifact = db.execute(
            "SELECT sha256,size FROM artifacts WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        if (artifact is None or type(artifact[0]) is not str
                or len(artifact[0]) != 64
                or any(c not in "0123456789abcdef" for c in artifact[0])
                or artifact_id != "sha256:" + artifact[0]
                or type(artifact[1]) is not int or artifact[1] <= 0):
            raise ValueError("canonical_backup_relink_artifact_invalid")
        identities = db.execute(
            "SELECT identity_id,directory_name FROM identities"
        ).fetchall()
        folders = [folder for profile, folder in identities if profile == identity]
        if (len(folders) != 1 or type(folders[0]) is not str
                or not folders[0]
                or sum(folder == folders[0] for _, folder in identities) != 1):
            raise ValueError("canonical_backup_relink_identity_ambiguous")
        reserved = frozenset(
            folder for _, folder in identities if type(folder) is str and folder
        )
        snapshots = []
        for raw in (candidate[0], keeper[0]):
            if type(raw) is not str or not raw:
                raise ValueError("canonical_backup_relink_bytes_changed")
            path = Path(raw)
            try:
                before = os.stat(path, follow_symlinks=False)
            except OSError as exc:
                raise ValueError("canonical_backup_relink_bytes_changed") from exc
            if (not stat.S_ISREG(before.st_mode)
                    or not _verified_copy(
                        raw, root=library_root, owner_directory=folders[0],
                        registered_directories=reserved, size=artifact[1],
                        sha256=artifact[0],
                    ) or not _same_file(path, before)):
                raise ValueError("canonical_backup_relink_bytes_changed")
            snapshots.append((path, before))

        # Bounded, path-exact scan. Never infer a legacy account from title,
        # digest, credential alone, or a matching different profile.
        rows = db.execute(
            "SELECT provider_id,credential_id,item_id,identity_id,account_id,"
            "status,sha256,size FROM backup_items WHERE local_path=? "
            "ORDER BY provider_id,credential_id,item_id LIMIT 129",
            (candidate[0],),
        ).fetchall()
        if len(rows) > 128:
            raise ValueError("canonical_backup_relink_budget_exceeded")
        for provider, credential, item, owner, account, status, digest, size in rows:
            if (owner != identity or type(account) is not str
                    or type(provider) is not str or not provider
                    or type(credential) is not str or not credential
                    or type(item) is not str or not item
                    or status != "complete" or digest != artifact[0]
                    or size != artifact[1]):
                raise ValueError("canonical_backup_relink_unattested_projection")
            connection = db.execute(
                "SELECT identity_id,enabled FROM provider_connections "
                "WHERE provider_id=? AND credential_id=?",
                (provider, credential),
            ).fetchone()
            accounts = db.execute(
                "SELECT account_id FROM provider_accounts WHERE "
                "identity_id=? AND provider_id=? AND credential_id=?",
                (identity, provider, credential),
            ).fetchall()
            if (connection != (identity, 1)
                    or sum(a[0] == account for a in accounts) != 1):
                raise ValueError("canonical_backup_relink_account_rebound")
            # A source reference with exact identity/account/edition must
            # already own an independent provider-origin publication of THIS
            # artifact. SHA by itself cannot create or transfer permission.
            published = db.execute(
                "SELECT COUNT(*) FROM source_references AS s "
                "JOIN origin_links AS o ON "
                "o.source_reference_id=s.source_reference_id "
                "WHERE s.identity_id=? AND s.provider_id=? "
                "AND s.credential_id=? AND s.account_id=? "
                "AND s.edition_id=? AND o.origin_kind='provider' "
                "AND o.identity_id=? AND o.artifact_id=? "
                "AND o.evidence_ref IS NOT NULL AND o.evidence_ref!=''",
                (identity, provider, credential, account, item,
                 identity, artifact_id),
            ).fetchone()
            if not published or published[0] < 1:
                raise ValueError("canonical_backup_relink_origin_missing")
        if any(not _same_file(path, before) for path, before in snapshots):
            raise ValueError("canonical_backup_relink_bytes_changed")
        now = int(time.time())
        for provider, credential, item, *_ in rows:
            result = db.execute(
                "UPDATE backup_items SET local_path=?,updated_at=? "
                "WHERE provider_id=? AND credential_id=? AND item_id=? "
                "AND local_path=?",
                (keeper[0], now, provider, credential, item, candidate[0]),
            )
            if result.rowcount != 1:
                raise ValueError("canonical_backup_relink_projection_changed")
        if any(not _same_file(path, before) for path, before in snapshots):
            raise ValueError("canonical_backup_relink_bytes_changed")
    return len(rows)


__all__ = ["relink_canonical_duplicate_backup_paths"]
