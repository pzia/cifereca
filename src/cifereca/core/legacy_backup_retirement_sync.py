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
            or snapshot.branch != "personal_manifest"
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
            and row.state == "active" and row.right_id == row.edition_id
            and row.provider_revision_id is not None)
    }
    if not matched:
        return 0
    try:
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN")
            # The single old PK omits branch/borrower. Always read using
            # this current registered identity+credential+account, then
            # revalidate all ownership and physical facts in the writer.
            candidates = db.execute(
                "SELECT item_id,revision_id FROM backup_items "
                "WHERE provider_id='kobo' AND identity_id=? "
                "AND credential_id=? AND account_id=? AND status='complete' "
                "ORDER BY item_id LIMIT ?",
                (snapshot.scope.identity_id, snapshot.scope.credential_id,
                 snapshot.scope.account_id, _MAX_PER_SYNC + 1),
            ).fetchall()
    except (OSError, ValueError, TypeError, RuntimeError):
        return 0
    # Never silently treat a truncated set as an exhaustive retirement batch.
    # The remaining work can be revisited in a later source Sync.
    retired = 0
    for item, revision in candidates[:_MAX_PER_SYNC]:
        if context_current() is not True:
            break
        observed = matched.get(item)
        if (observed is None or type(revision) is not str
                or revision != observed.provider_revision_id):
            continue
        result = retire_attested_legacy_kobo_backup(
            storage, library_root=library_root,
            snapshot=snapshot, item_id=item, right_id=observed.right_id,
            context_current=context_current,
        )
        retired += int(result.status == "retired" and result.changed)
    return retired


__all__ = ["retire_canonical_kobo_legacy_in_sync"]
