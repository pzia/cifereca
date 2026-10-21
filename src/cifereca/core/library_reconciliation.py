"""Read-only evidence graph for #2565; no action token, mutation or acquisition.

Private input/output stays local. redacted() exports only closed counters.
Source SQLite handles are never opened: mode=ro may create/write SHM.
Stable main files and isolated Linux WAL captures are queried in memory,
WITHOUT starting Storage/migrations or writing temp files.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import os
import json
from pathlib import Path
import sqlite3
import time

from .library_inventory import Inventory, _fingerprint, _open_root

# Do not load licenses, tokens, arbitrary metadata blobs or private error prose.
_COLUMNS = {
    'entitlements': 'entitlement_id provider_id credential_id account_id item_id identity_id state entitlement_type loan_id expires_at metadata_json',
    'acquisition_ledger': 'acquisition_id entitlement_id provider_id credential_id account_id item_id identity_id status artifact_id',
    'artifacts': 'artifact_id sha256 size media_format',
    'placements': 'placement_id artifact_id path identity_id provider_id source_item_id state',
    'backup_items': 'provider_id credential_id item_id identity_id local_path sha256 size status',
    'catalog_entries': 'catalog_entry_id artifact_id placement_id identity_id provider_id item_id metadata_json',
    'adept_profile_bindings': 'scope_type scope_id profile_id',
    'paris_borrower_adept_bindings': 'connection_identity_id borrower_identity_id profile_id',
}


@dataclass(repr=False)
class EvidenceSnapshot:
    tables: dict[str, list[dict]] = field(default_factory=dict)
    reasons: set[str] = field(default_factory=set)


def read_quiescent_snapshot(path: Path, *, max_rows: int = 100_000,
                            max_bytes: int = 128*1024*1024, seconds: float = 10,
                            cancelled=lambda: False) -> EvidenceSnapshot:
    """Bounded copy of a stable, non-symlink, quiescent DB, queried in memory.

    A live WAL/journal yields snapshot_live_journal, not an empty rights list.
    Does not checkpoint, lock, recover, migrate, or open an SQLite disk handle.
    Callers needing live WAL must supply a separately attested coherent snapshot
    through a future Storage API; never fall back to stale immutable WAL reads.
    """
    if (type(max_rows) is not int or not 1 <= max_rows <= 100_000
            or type(max_bytes) is not int or not 1 <= max_bytes <= 128*1024*1024
            or type(seconds) not in (int, float) or not 0 < seconds <= 30):
        raise ValueError('invalid_snapshot_limits')
    result = EvidenceSnapshot()
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('invalid_snapshot_path')
    parent = fd = -1
    deadline = time.monotonic() + seconds
    try:
        parent = _open_root(path.parent)

        def journal_present():
            for suffix in ('-wal', '-journal'):
                try:
                    os.stat(path.name+suffix, dir_fd=parent, follow_symlinks=False)
                    return True
                except FileNotFoundError:
                    pass
            return False

        if journal_present():
            result.reasons.add('snapshot_live_journal')
            return result
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        import stat
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 100 <= before.st_size <= max_bytes:
            result.reasons.add('snapshot_byte_budget_or_invalid')
            return result
        parts = []
        remaining = before.st_size
        while remaining:
            if cancelled():
                result.reasons.add('snapshot_cancelled')
                return result
            if time.monotonic() >= deadline:
                result.reasons.add('snapshot_time_budget')
                return result
            data = os.read(fd, min(1024*1024, remaining))
            if not data:
                break
            parts.append(data)
            remaining -= len(data)
        if (remaining or journal_present()
                or _fingerprint(before) != _fingerprint(os.fstat(fd))
                or _fingerprint(before) != _fingerprint(os.stat(path.name, dir_fd=parent, follow_symlinks=False))):
            result.reasons.add('snapshot_changed')
            return result
        image = b''.join(parts)
        if image[:16] != b'SQLite format 3\0':
            result.reasons.add('snapshot_unavailable')
            return result
        return _query_image(image, max_rows=max_rows, deadline=deadline, cancelled=cancelled)
    except (OSError, sqlite3.Error, ValueError):
        result.tables.clear()
        result.reasons.add('snapshot_cancelled' if cancelled() else 'snapshot_time_budget' if time.monotonic() >= deadline else 'snapshot_unavailable')
    finally:
        if fd >= 0:
            os.close(fd)
        if parent >= 0:
            os.close(parent)
    return result


def _query_image(image: bytes, *, max_rows: int, deadline: float,
                 cancelled=lambda: False) -> EvidenceSnapshot:
    """Project a bounded coherent image in memory, never source SQLite handles."""
    result = EvidenceSnapshot()
    try:
        if not 100 <= len(image) <= 128*1024*1024 or image[:16] != b'SQLite format 3\0':
            result.reasons.add('snapshot_unavailable')
            return result
        # A closed WAL-mode DB without a WAL is complete. Normalize ONLY the
        # in-memory copy's journal header so deserialize never seeks a sidecar.
        image = image[:18] + b'\x01\x01' + image[20:]
        with sqlite3.connect(':memory:') as db:
            if not hasattr(db, 'deserialize'):
                result.reasons.add('snapshot_reader_unavailable')
                return result
            db.deserialize(image)
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            db.set_progress_handler(lambda: int(cancelled() or time.monotonic() >= deadline), 1000)
            db.execute('BEGIN')
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'metadata' in names and db.execute(
                    "SELECT 1 FROM metadata WHERE key=? AND value IS NOT NULL AND value<>'' LIMIT 1",
                    ('identity-directory-migration-v1',)).fetchone():
                result.reasons.add('snapshot_identity_migration')
                return result
            remaining_rows = max_rows
            for name, columns in _COLUMNS.items():
                if cancelled() or time.monotonic() >= deadline:
                    raise sqlite3.OperationalError('interrupted')
                if name not in names:
                    result.reasons.add('snapshot_schema_incomplete')
                    continue
                available = {r[1] for r in db.execute(f'PRAGMA table_info({name})')}
                wanted = columns.split()
                if not set(wanted) <= available:
                    result.reasons.add('snapshot_schema_incomplete')
                    continue
                rows = db.execute(f"SELECT {','.join(wanted)} FROM {name} LIMIT ?", (remaining_rows+1,)).fetchall()
                if len(rows) > remaining_rows:
                    result.reasons.add('snapshot_row_budget')
                rows = rows[:remaining_rows]
                remaining_rows -= len(rows)
                result.tables[name] = [dict(zip(wanted, row)) for row in rows]
                if name == 'catalog_entries':
                    # Import here avoids the module-level snapshot/type cycle.
                    from .library_metadata_candidates import identifiers
                    for row in result.tables[name]:
                        if cancelled() or time.monotonic() >= deadline:
                            raise sqlite3.OperationalError('interrupted')
                        raw = row.pop('metadata_json', None)
                        try:
                            metadata = json.loads(raw) if type(raw) is str and len(raw) <= 65536 else {}
                        except (ValueError, RecursionError):
                            metadata = {}
                        row['bibliographic_identifiers'] = identifiers(metadata)
                if name == 'entitlements':
                    for row in result.tables[name]:
                        if cancelled() or time.monotonic() >= deadline:
                            raise sqlite3.OperationalError('interrupted')
                        raw = row.pop('metadata_json', None)
                        try:
                            metadata = json.loads(raw) if type(raw) is str and len(raw) <= 65536 else {}
                        except (ValueError, RecursionError):
                            metadata = {}
                        source = metadata.get('source') if isinstance(metadata, dict) else None
                        row['attested_source'] = source if source in ('kobo.personal_manifest', 'numilog.authenticated_library') else None
    except (sqlite3.Error, ValueError):
        result.tables.clear()
        result.reasons.add('snapshot_cancelled' if cancelled() else 'snapshot_time_budget' if time.monotonic() >= deadline else 'snapshot_unavailable')
    return result


def read_library_snapshot(path: Path, *, max_rows: int = 100_000,
                          max_bytes: int = 128*1024*1024, seconds: float = 10,
                          cancelled=lambda: False) -> EvidenceSnapshot:
    """Stable main image, or isolated Linux WAL capture, under one deadline."""
    started = time.monotonic()
    result = read_quiescent_snapshot(path, max_rows=max_rows, max_bytes=max_bytes,
                                     seconds=seconds, cancelled=cancelled)
    deadline = started+seconds
    if result.reasons != {'snapshot_live_journal'}:
        return result
    if cancelled() or time.monotonic() >= deadline:
        return EvidenceSnapshot(reasons={'snapshot_cancelled' if cancelled() else 'snapshot_time_budget'})
    from .library_wal_image import read_wal_image
    try:
        image, reason = read_wal_image(path, max_bytes=max_bytes,
                                      seconds=deadline-time.monotonic(), cancelled=cancelled)
    except OSError:
        return EvidenceSnapshot(reasons={'snapshot_wal_reader_unavailable'})
    if reason:
        return EvidenceSnapshot(reasons={reason})
    return _query_image(image, max_rows=max_rows, deadline=deadline, cancelled=cancelled)


@dataclass(frozen=True, repr=False)
class EvidenceEdge:
    file_index: int
    table: str
    row_index: int
    reason: str
    certainty: str


@dataclass(repr=False)
class ReconciliationReport:
    # Indices are ephemeral local references, never stable public identifiers.
    files: dict[int, set[str]] = field(default_factory=dict)
    edges: list[EvidenceEdge] = field(default_factory=list)
    unverified_rights: list[int] = field(default_factory=list)
    missing_placements: list[int] = field(default_factory=list)
    reasons: set[str] = field(default_factory=set)

    def redacted(self):
        counts = Counter(category for categories in self.files.values() for category in categories)
        counts['rights_without_verified_file'] = len(self.unverified_rights)
        return {'schema': 1, 'read_only': True, 'acquisition_authorized': False,
                'status': 'incomplete_scan' if self.reasons else 'complete',
                'adept_status': 'not_requested',
                'not_evaluated': ['adept_remote_inventory'],
                'reasons': sorted(self.reasons), 'counts': dict(sorted(counts.items())),
                'edge_counts': dict(sorted(Counter(e.reason for e in self.edges).items()))}


def reconcile(inventory: Inventory, snapshot: EvidenceSnapshot, *, cancelled=lambda: False) -> ReconciliationReport:
    """Compare file/placement/artifact/right evidence without inferring ownership.

    Confirm only full entitlement -> successful ledger -> artifact -> explicit
    identity/provider/item placement, with exact SHA AND size. SHA alone is a
    present_unlinked byte candidate, never attested right. Missing paths are
    asserted only for completely scanned owning identities; otherwise unknown.
    OPF is retained in Inventory for private inspection, not title matching.
    """
    result = ReconciliationReport(reasons=set(inventory.reasons) | set(snapshot.reasons))
    # No ADEPT runtime is contacted by this offline tranche.
    tables = snapshot.tables
    artifacts = defaultdict(list)
    hashes = defaultdict(list)
    for ai, row in enumerate(tables.get('artifacts', [])):
        artifacts[row['artifact_id']].append(row)
        hashes[(row['sha256'], row['size'])].append((ai, row))
    rights = tables.get('entitlements', [])
    acquisitions = defaultdict(list)
    for row in tables.get('acquisition_ledger', []):
        acquisitions[row['entitlement_id']].append(row)
    rights_by_placement = defaultdict(list)
    for ri, right in enumerate(rights):
        rights_by_placement[(right['identity_id'], right['provider_id'], right['item_id'])].append((ri, right))
    placements = tables.get('placements', [])
    by_path = defaultdict(list)
    for index, row in enumerate(placements):
        by_path[(row['identity_id'], row['path'])].append((index, row))
    backup_hashes = defaultdict(list)
    for bi, row in enumerate(tables.get('backup_items', [])):
        if row.get('sha256') and row.get('size'):
            backup_hashes[(row['identity_id'], row['sha256'], row['size'])].append((bi, row))
    verified = set()
    deadline = time.monotonic() + 10
    comparisons = 0
    for fi, observation in enumerate(inventory.files):
        if cancelled():
            result.reasons.add('cancelled')
            break
        if time.monotonic() >= deadline or comparisons >= 100_000 or len(result.edges) >= 20_000:
            result.reasons.add('comparison_budget')
            break
        categories = result.files[fi] = set()
        if observation.sha256 is None:
            categories.add('unverified_file')
            continue
        byte_candidates = hashes.get((observation.sha256, observation.size), [])
        backup_candidates = backup_hashes.get((observation.identity_id, observation.sha256, observation.size), [])
        if byte_candidates or backup_candidates:
            categories.add('present_unlinked')
        for ai, _ in byte_candidates[:24]:
            result.edges.append(EvidenceEdge(fi, 'artifacts', ai, 'artifact_sha_size_candidate', 'bytes'))
        if len(byte_candidates) > 24:
            result.reasons.add('comparison_budget')
        for bi, _ in backup_candidates[:24]:
            result.edges.append(EvidenceEdge(fi, 'backup_items', bi, 'backup_sha_size_candidate', 'bytes'))
        if len(backup_candidates) > 24:
            result.reasons.add('comparison_budget')
        links = by_path.get((observation.identity_id, observation.path), [])
        confirmed = []
        for pi, placement in links:
            comparisons += 1
            if comparisons >= 100_000 or len(result.edges) >= 20_000:
                result.reasons.add('comparison_budget')
                break
            candidates = artifacts.get(placement['artifact_id'], [])
            matches = [a for a in candidates if a['sha256'] == observation.sha256 and a['size'] == observation.size]
            if not matches:
                if candidates:
                    categories.add('hash_mismatch')
                continue
            result.edges.append(EvidenceEdge(fi, 'placements', pi, 'explicit_placement_sha_size', 'bytes'))
            for ri, right in rights_by_placement.get((observation.identity_id, placement['provider_id'], placement['source_item_id']), []):
                comparisons += 1
                if comparisons >= 100_000 or len(result.edges) >= 20_000:
                    result.reasons.add('comparison_budget')
                    break
                if (right['identity_id'] != observation.identity_id
                        or right['provider_id'] != placement['provider_id']
                        or right['item_id'] != placement['source_item_id']
                        or right['state'] != 'active'
                        or right.get('entitlement_type') != 'owned'
                        or right.get('loan_id') or right.get('expires_at')
                        or right.get('attested_source') != {
                            'kobo': 'kobo.personal_manifest',
                            'numilog': 'numilog.authenticated_library',
                        }.get(right['provider_id'])
                        or right['provider_id'] not in {'kobo', 'numilog'}
                        or not all(right.get(k) for k in ('entitlement_id', 'credential_id', 'account_id', 'item_id', 'identity_id', 'provider_id'))):
                    continue
                ledger = acquisitions.get(right['entitlement_id'], [])
                matching = [a for a in ledger[:1] if a['status'] == 'complete'
                            and a['artifact_id'] == placement['artifact_id']
                            and all(a.get(k) == right.get(k) for k in ('identity_id', 'provider_id', 'credential_id', 'account_id', 'item_id'))]
                # Conflicting owner evidence in the linked ledger is not ignored.
                if (matching and len(ledger) == 1 and len(candidates) == 1
                        and placement['state'] == 'present'
                        and observation.validation == 'ok'
                        and observation.structure_validation == 'epub_structure_crc_ok'
                        and observation.media_format == 'epub'
                        and candidates[0]['media_format'] in {'epub', 'kepub'}
                        and candidates[0]['artifact_id'] == 'sha256:' + observation.sha256):
                    confirmed.append(ri)
                    result.edges.append(EvidenceEdge(fi, 'entitlements', ri, 'explicit_right_ledger_placement_sha_size', 'attested'))
        # A single borrower/account scope is not necessarily a single right:
        # two distinct entitlement rows for the same purchase can both have
        # valid-looking ledger chains pointing at the *same* file. Do not
        # coalesce these into one confirmed entitlement by account identity.
        # Likewise, never confirm when the per-file comparison stopped early:
        # later uninspected rows could invalidate uniqueness.
        if (len(confirmed) == 1 and len(links) == 1
                and not snapshot.reasons
                and 'comparison_budget' not in result.reasons):
            categories.discard('present_unlinked')
            categories.add('confirmed_link')
            verified.update(confirmed)
        else:
            if confirmed or len(links) > 1:
                categories.add('ambiguous')
            categories.add('local_file_without_attested_right')
    result.unverified_rights = [i for i, r in enumerate(rights) if r['state'] == 'active' and i not in verified]
    # Inventory may not include an owning root: never call foreign paths missing.
    # Root completeness is deliberately conservative until captured in Inventory.
    return result


def main(argv=None):
    """Standalone offline CLI. Roots must be explicitly authorized per identity.

    Default output is redacted. A private report is written ONLY when its output
    filename is explicitly supplied, exclusively created, mode 0600. Existing
    files/symlinks are never overwritten. This export is separate from audit.
    """
    import argparse
    import signal
    import threading
    from dataclasses import asdict
    from .library_inventory import scan_identity_roots
    from .library_stored_paths import inspect_stored_paths
    from .library_metadata_candidates import metadata_candidates
    from .library_evidence_scope import scope_evidence, group_evidence

    parser = argparse.ArgumentParser(description='Offline library reconciliation (no repair or acquisition).')
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--root', required=True, action='append', metavar='IDENTITY=ABSOLUTE_DIRECTORY',
                        help='Explicitly authorized identity root; repeat for separate roots.')
    parser.add_argument('--private-report', type=Path,
                        help='Explicitly create a NEW private local JSON file (contains paths, titles, IDs and hashes).')
    args = parser.parse_args(argv)
    roots = defaultdict(list)
    try:
        for value in args.root:
            identity, separator, directory = value.partition('=')
            if not separator:
                raise ValueError('invalid_inventory_roots')
            roots[identity].append(Path(directory))
        cancelled = threading.Event()
        previous = signal.signal(signal.SIGINT, lambda *_: cancelled.set())
        try:
            inventory = scan_identity_roots({k: tuple(v) for k, v in roots.items()}, cancelled=cancelled.is_set)
            snapshot = EvidenceSnapshot(reasons={'snapshot_cancelled'}) if cancelled.is_set() else read_library_snapshot(args.database, cancelled=cancelled.is_set)
            snapshot = scope_evidence(snapshot, tuple(roots))
            report = reconcile(inventory, snapshot, cancelled=cancelled.is_set)
            groups = group_evidence(inventory, snapshot, report, tuple(roots))
            candidates = metadata_candidates(inventory, snapshot, cancelled=cancelled.is_set)
            stored_paths = inspect_stored_paths(snapshot, {k: tuple(v) for k, v in roots.items()}, cancelled=cancelled.is_set)
        finally:
            signal.signal(signal.SIGINT, previous)
        if args.private_report is not None:
            private = {'privacy': 'local_private_do_not_share',
                       'inventory': asdict(inventory), 'snapshot': asdict(snapshot),
                       'reconciliation': asdict(report), 'stored_paths': asdict(stored_paths), 'metadata_candidates': asdict(candidates),
                       'groups': {'identities': groups.identities, **groups.redacted()}}
            fd = os.open(args.private_report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(private, stream, ensure_ascii=False, default=lambda value: sorted(value) if isinstance(value, set) else None)
        print(json.dumps({'inventory': inventory.redacted(), 'reconciliation': report.redacted(), 'stored_paths': stored_paths.redacted(), 'metadata_candidates': candidates.redacted(), 'groups': groups.redacted()}, sort_keys=True))
        return 2 if inventory.reasons or report.reasons or stored_paths.reasons or candidates.reasons else 0
    except (OSError, ValueError):
        # Never echo an input path, account identifier or raw parser exception.
        print(json.dumps({'status': 'reconciliation_unavailable', 'read_only': True,
                          'acquisition_authorized': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
