"""#3196: retire redundant v0.7 Kobo archives inside the existing Core Sync.

This is a bounded *maintenance tail* of a fully admitted current provider A
observation, never another rights read, GET, auto-download or file operation.
Only the caller's already-held global acquisition lease permits attempting
an exact Core B / provider-revision-attested retirement. The per-row writer
is the sole source of SQL authority and must fail closed.
"""
from __future__ import annotations

from pathlib import Path
from threading import get_ident
import os
import sqlite3
from typing import Callable

from ..locking import InterProcessLock
from ..paths import acquisition_lock_file
from ..plugin_api.provider_rights_observation import ProviderRightsSnapshot
from .legacy_backup_retirement import retire_attested_legacy_kobo_backup

_MAX_PER_SYNC = 4


def retire_canonical_kobo_legacy_in_sync(
    storage, snapshot: ProviderRightsSnapshot, *,
    library_root: Path, lease: InterProcessLock | None,
    context_current: Callable[[], bool],
) -> int:
    """Archive up to four exact already-published legacy Kobo rows.

    No authorization is inferred from manifest membership, from a SHA or
    from a still present legacy path. Only positive, complete and current A,
    together with the independent fully verified B receipt, can remove a
    redundant compatibility row. Zero on any incomplete or ambiguous scope.
    """
    if (type(snapshot) is not ProviderRightsSnapshot
            or snapshot.scope.provider_id != "kobo"
            or snapshot.status != "success" or snapshot.coverage != "complete"
            or snapshot.pagination_complete is not True
            or type(lease) is not InterProcessLock
            or not callable(context_current)
            or not isinstance(library_root, Path) or not library_root.is_absolute()):
        return 0
    held = getattr(lease, "_lease", None)
    handle = getattr(lease, "_handle", None)
    if (held is None or handle is None or getattr(handle, "closed", True)
            or held.pid != os.getpid() or held.thread_id != get_ident()
            or lease.path.resolve() != acquisition_lock_file().resolve()):
        return 0
    if context_current() is not True:
        return 0
    # Only the exact immutable producer's current positive revision is
    # eligible for a bounded lookup. Never scan or rehydrate every v0.7 row.
    matched = {
        row.edition_id: row
        for row in snapshot.rights
        if (row.provider_attested and row.kind == "purchase"
            and row.state == "active"
            and row.provider_revision_id is not None)
    }
    if not matched:
        return 0
    # Iterate only the POSITIVELY observed editions, not the earliest
    # historical backup IDs. A legacy-only row alphabetically before an
    # eligible canonical B must not starve the migration on every Sync.
    # Chunk by 200 IDs (below old SQLite bind limits), cap actual writes
    # to four and leave all unverifiable archives untouched for review.
    eligible: list[tuple[str, str]] = []
    rights_ids = sorted(matched)
    try:
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN")
            for offset in range(0, len(rights_ids), 200):
                ids = rights_ids[offset:offset + 200]
                placeholders = ",".join("?" for _ in ids)
                records = db.execute(
                    "SELECT b.item_id,b.revision_id FROM backup_items AS b "
                    "WHERE b.provider_id='kobo' AND b.identity_id=? "
                    "AND b.credential_id=? AND b.account_id=? "
                    "AND b.status='complete' "
                    f"AND b.item_id IN ({placeholders}) "
                    # Keep the bounded 4-row physical verifier from being
                    # starved by hundreds of provider-positive old downloads
                    # that still have ONLY a v0.7 placeholder, not published
                    # canonical B. This SQL join is merely a candidate
                    # prefilter: the full completed receipt, SHA/inode,
                    # uniqueness and source epoch are ALL checked again
                    # inside the per-row BEGIN IMMEDIATE writer.
                    "AND EXISTS (SELECT 1 FROM source_references AS s "
                    "JOIN origin_links AS o "
                    "ON o.source_reference_id=s.source_reference_id "
                    "AND o.origin_kind='provider' "
                    "AND o.identity_id=s.identity_id "
                    "JOIN placements AS p ON p.artifact_id=o.artifact_id "
                    "AND p.identity_id=s.identity_id AND p.state='present' "
                    "AND p.provider_id IS NULL AND p.source_item_id IS NULL "
                    "WHERE s.provider_id='kobo' "
                    "AND s.credential_id=b.credential_id "
                    "AND s.account_id=b.account_id "
                    "AND s.identity_id=b.identity_id "
                    "AND s.branch_id=? "
                    "AND s.edition_id=b.item_id AND s.reference_kind='purchase' "
                    "AND s.state='active' AND s.evidence_kind='provider_response')",
                    (snapshot.scope.identity_id, snapshot.scope.credential_id,
                     snapshot.scope.account_id, *ids, snapshot.branch),
                ).fetchall()
                for item, revision in records:
                    observed = matched.get(item)
                    if (observed is not None
                            and type(revision) is str
                            and revision == observed.provider_revision_id):
                        eligible.append((item, observed.right_id))
                if len(eligible) >= _MAX_PER_SYNC:
                    break
    except (sqlite3.Error, OSError, ValueError, TypeError, RuntimeError):
        return 0
    retired = 0
    for item, right_id in eligible[:_MAX_PER_SYNC]:
        if context_current() is not True:
            break
        result = retire_attested_legacy_kobo_backup(
            storage, library_root=library_root,
            snapshot=snapshot, item_id=item, right_id=right_id,
            context_current=context_current,
        )
        retired += int(result.status == "retired" and result.changed)
    return retired


__all__ = ["retire_canonical_kobo_legacy_in_sync"]
