"""Private offline audit orchestration for #2565.

Identity-owning callers supply explicit roots. This is an internal Core
primitive, not a plugin capability or repair token. Only redacted() and the
progress payloads may enter diagnostics. No service startup, network, export,
mkdir, Storage migration or mutation is performed.

The snapshot reader supports isolated Linux WAL capture. Unsupported or
unavailable journals remain visible in the aggregate result.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from time import monotonic
from typing import Callable

from .library_evidence_scope import GroupedEvidence, group_evidence, scope_evidence
from .library_inventory import Inventory, InventoryLimits, scan_identity_roots
from .library_metadata_candidates import MetadataCandidateReport, metadata_candidates
from .library_reconciliation import (
    EvidenceSnapshot, ReconciliationReport, read_library_snapshot, reconcile,
)
from .library_stored_paths import StoredPathReport, inspect_stored_paths


@dataclass(repr=False)
class LibraryAudit:
    """Local private evidence; never log or serialize the dataclass itself."""

    inventory: Inventory
    snapshot: EvidenceSnapshot
    reconciliation: ReconciliationReport
    metadata_candidates: MetadataCandidateReport
    stored_paths: StoredPathReport
    groups: GroupedEvidence
    reasons: set[str] = field(default_factory=set)

    def redacted(self) -> dict:
        # Derive incompleteness from EVERY phase, including stored paths and
        # metadata. A complete graph alone must not hide a later phase failure.
        reasons = (set(self.reasons) | self.inventory.reasons | self.snapshot.reasons
                   | self.reconciliation.reasons | self.metadata_candidates.reasons
                   | self.stored_paths.reasons)
        return {
            'schema': 1, 'read_only': True, 'acquisition_authorized': False,
            'repair_authorized': False,
            'status': 'incomplete_scan' if reasons else 'complete',
            'reasons': sorted(reasons),
            'inventory': self.inventory.redacted(),
            'reconciliation': self.reconciliation.redacted(),
            'metadata_candidates': self.metadata_candidates.redacted(),
            'stored_paths': self.stored_paths.redacted(),
            'groups': self.groups.redacted(),
        }


def audit_library(
    database: Path, roots: dict[str, tuple[Path, ...]], *,
    limits: InventoryLimits | None = None, seconds: float = 180,
    comparison_seconds: float = 10, candidate_seconds: float = 10,
    candidate_index_seconds: float | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[dict], None] | None = None,
    _snapshot: EvidenceSnapshot | None = None,
    _reuse: dict | None = None,
) -> LibraryAudit:
    """Run all existing local phases with a shared deadline/cancel signal.

    The caller must authorize the identity roots before invocation. Copy the
    capability mapping before callbacks so their mutation cannot broaden the
    identity scope of later phases. Root traversal validation remains owned by
    scan_identity_roots. Progress contains closed phases and redacted counters,
    never file paths, metadata, SHA, SQLite rows or account identifiers.

    Individual parsers retain their existing bounded critical sections; the
    shared deadline is checked at their cooperative interruption boundaries.
    Callback exceptions propagate without being logged or reinterpreted.
    """
    if (type(seconds) not in (int, float) or not 0 < seconds <= 600
            or type(comparison_seconds) not in (int, float)
            or not 0 < comparison_seconds <= 60
            or type(candidate_seconds) not in (int, float)
            or not 0 < candidate_seconds <= 60
            or (candidate_index_seconds is not None and (
                type(candidate_index_seconds) not in (int, float)
                or not 0 < candidate_index_seconds < candidate_seconds))
            or not isinstance(roots, dict)):
        raise ValueError('invalid_library_audit_options')
    database = Path(database)
    if not database.is_absolute() or '..' in database.parts:
        raise ValueError('invalid_snapshot_path')
    # Do not iterate arbitrary callback-controlled containers repeatedly.
    selected = {}
    for identity, paths in roots.items():
        if type(paths) is not tuple:
            raise ValueError('invalid_inventory_roots')
        selected[identity] = tuple(Path(path) for path in paths)
    limits = limits or InventoryLimits()
    deadline = monotonic() + seconds
    reasons: set[str] = set()

    def stopped():
        if cancelled():
            reasons.add('audit_cancelled')
            return True
        if monotonic() >= deadline:
            reasons.add('audit_time_budget')
            return True
        return False

    def emit(phase, counters=None):
        if progress is not None:
            payload = {'schema': 1, 'phase': phase, 'read_only': True,
                       'acquisition_authorized': False, 'repair_authorized': False}
            if counters is not None:
                payload['inventory'] = counters
            progress(payload)

    emit('inventory')
    inventory = scan_identity_roots(
        selected, limits=replace(limits, seconds=min(limits.seconds, seconds)),
        cancelled=stopped, progress=lambda counters: emit('inventory', counters),
        _reuse=_reuse,
    )
    emit('snapshot')
    if stopped():
        snapshot = EvidenceSnapshot(reasons=set(reasons))
    elif _snapshot is not None:
        # Internal callers may capture before inventory to reject pending
        # identity migrations BEFORE opening library files. It is evidence of
        # that instant only and still undergoes identity scoping below.
        snapshot = _snapshot
    else:
        snapshot = read_library_snapshot(
            database, seconds=min(10, max(0.001, deadline-monotonic())),
            cancelled=stopped,
        )
    snapshot = scope_evidence(snapshot, tuple(selected))
    emit('reconciliation')
    report = reconcile(inventory, snapshot, cancelled=stopped,
                       seconds=comparison_seconds)
    emit('metadata_candidates')
    candidates = metadata_candidates(
        inventory, snapshot, cancelled=stopped,
        seconds=candidate_seconds, index_seconds=candidate_index_seconds,
    )
    emit('stored_paths')
    stored_started = monotonic()
    paths = inspect_stored_paths(snapshot, selected, cancelled=stopped)
    # Only the elapsed aggregate crosses the Core redaction boundary.
    # Standalone StoredPathReport uses no invented measurement.
    paths.duration_ms = max(0, min(30_000, int((monotonic() - stored_started) * 1000)))
    stopped()
    groups = group_evidence(inventory, snapshot, report, tuple(selected))
    result = LibraryAudit(inventory, snapshot, report, candidates, paths, groups, reasons)
    emit('finished')
    return result
