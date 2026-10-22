"""Private two-run evidence stability comparison for reconciliation #2796.

Only per-process, keyed digests of scoped observations and SQLite projections
are retained. No digest, path, book ID, title, account, SHA or raw row crosses
the redacted broker boundary. This is a diagnostic, NEVER a link authority.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from threading import Lock
from time import monotonic

_KEY = os.urandom(32)
_LOCK = Lock()
_GENERATION = 0
_SLOT = None
_TTL = 600
_MAX_FILES = 4096
_TABLES = (
    'entitlements', 'acquisition_ledger', 'placements',
    'artifacts', 'backup_items',
)


@dataclass(repr=False, slots=True)
class _Projection:
    # Keys and values are keyed, process-local 128-bit binary digests only.
    files: dict[bytes, tuple[bytes, bool]]
    rows: dict[str, Counter]
    inventory_complete: bool
    proof_complete: bool
    link_complete: bool


@dataclass(repr=False, slots=True)
class _Slot:
    owner: object
    identity_id: str
    scope: tuple
    generation: int
    expires: float
    value: _Projection


def _digest(category: str, data: object) -> bytes:
    serialized = json.dumps(
        data, ensure_ascii=True, sort_keys=True, separators=(',', ':'),
        allow_nan=False,
    ).encode('utf-8')
    # Avoid unbounded formatting, retaining or hashing oversized private
    # values. A non-serializable projection is not comparable.
    if len(serialized) > 65536:
        raise ValueError('oversized_drift_observation')
    return hashlib.blake2b(
        category.encode('ascii') + b'\0' + serialized,
        key=_KEY, digest_size=16,
    ).digest()


def _project(audit) -> _Projection | None:
    try:
        deadline = monotonic() + 2.0  # diagnostic must never dominate an audit
        inventory, snapshot, reconciliation = (
            audit.inventory, audit.snapshot, audit.reconciliation,
        )
        observations = inventory.files
        if len(observations) > _MAX_FILES:
            return None
        files = {}
        for fi, row in enumerate(observations):
            if fi % 128 == 0 and monotonic() >= deadline:
                return None
            path_key = _digest('file-key', (row.identity_id, row.path))
            if path_key in files:
                return None
            evidence = _digest('file-evidence', (
                row.fingerprint, row.sha256, row.size, row.media_format,
                row.validation, row.structure_validation,
            ))
            files[path_key] = (
                evidence, 'confirmed_link' in reconciliation.files.get(fi, ()),
            )
        tables = snapshot.tables
        # Missing or incomplete tables cannot prove a stable SQLite snapshot.
        if not all(type(tables.get(name)) is list for name in _TABLES):
            return None
        rows = {}
        total_rows = 0
        for table in _TABLES:
            total_rows += len(tables[table])
            if total_rows > 100000:
                return None
            rows[table] = Counter()
            for index, row in enumerate(tables[table]):
                if index % 128 == 0 and monotonic() >= deadline:
                    return None
                rows[table][_digest('sqlite-' + table, row)] += 1
        return _Projection(
            files, rows,
            not inventory.reasons,
            not snapshot.reasons,
            not bool(set(reconciliation.reasons) & {
                'comparison_budget', 'cancelled', 'time_budget',
                'audit_time_budget', 'snapshot_row_budget',
                'snapshot_time_budget', 'snapshot_unavailable',
                'snapshot_changed', 'snapshot_cancelled',
            }),
        )
    except (AttributeError, KeyError, TypeError, ValueError,
            OverflowError, UnicodeError, RecursionError):
        return None


def begin_audit_stability(service, identity_id: str, scope: tuple):
    """Consume the previous comparable run; revoke older concurrent writers."""
    global _GENERATION, _SLOT
    with _LOCK:
        _GENERATION += 1
        generation = _GENERATION
        prior, _SLOT = _SLOT, None
    if (prior is not None and prior.owner is service
            and prior.identity_id == identity_id and prior.scope == scope
            and monotonic() < prior.expires):
        return generation, prior.value
    return generation, None


def finish_audit_stability(service, identity_id: str, scope: tuple,
                           generation: int, previous: _Projection | None,
                           audit, scope_valid):
    """Return closed, aggregate-only diagnostics; never persist source facts.

    Counts describe the *observed* cohort only unless both runs had complete
    source/proof comparisons. Changing an input is not proof of a particular
    cause for link loss. A cancelled/revoked run stores no projection.
    """
    global _SLOT
    if not scope_valid():
        return None
    current = _project(audit)
    if current is None:
        return None
    with _LOCK:
        if generation != _GENERATION or not scope_valid():
            return None
        _SLOT = _Slot(service, identity_id, scope, generation,
                      monotonic() + _TTL, current)
    if previous is None:
        return None

    former, present = previous.files, current.files
    before_keys, now_keys = set(former), set(present)
    common = before_keys & now_keys
    added = len(now_keys - before_keys)
    removed = len(before_keys - now_keys)
    changed = sum(former[key][0] != present[key][0] for key in common)
    old_confirmed = {key for key, (_, confirmed) in former.items() if confirmed}
    new_confirmed = {key for key, (_, confirmed) in present.items() if confirmed}
    changed_tables = [
        table for table in _TABLES if previous.rows[table] != current.rows[table]
    ]
    same_cohort = not (added or removed)
    same_files = same_cohort and changed == 0
    same_sqlite = not changed_tables
    complete = (previous.inventory_complete and current.inventory_complete
                and previous.proof_complete and current.proof_complete
                and previous.link_complete and current.link_complete)
    if not previous.link_complete or not current.link_complete:
        interpretation = 'comparison_incomplete'
    elif not same_files and not same_sqlite:
        interpretation = 'both_inputs_changed'
    elif not same_cohort:
        interpretation = 'different_observed_cohort'
    elif changed:
        interpretation = 'file_evidence_changed'
    elif not same_sqlite:
        interpretation = 'sqlite_proofs_changed'
    elif old_confirmed != new_confirmed:
        # Identical observed file evidence AND identical SQLite proof rows
        # must not yield different confirmed links in a completed comparison.
        # Report this as unexplained, never silently classify it as stable.
        interpretation = 'unexplained_link_drift'
    else:
        interpretation = 'stable_inputs'
    return {
        'schema': 1, 'basis': 'complete' if complete else 'observed_only',
        'previous_observed': len(former), 'current_observed': len(present),
        'files_added': added, 'files_removed': removed,
        'files_changed': changed,
        'same_observed_cohort': same_cohort,
        'same_file_evidence': same_files,
        'same_sqlite_proofs': same_sqlite,
        'changed_sqlite_tables': changed_tables,
        'lost_confirmed': len(old_confirmed - new_confirmed),
        'gained_confirmed': len(new_confirmed - old_confirmed),
        'interpretation': interpretation,
    }
