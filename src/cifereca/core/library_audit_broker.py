"""Public redacted-only facade; private audit cases never cross this boundary."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from .library_audit import audit_library
from .library_inventory import InventoryLimits
from .library_audit_roots import configured_identity_roots
from .library_inspection_cache import begin_inspection_cache, remember_inspection_cache
from .library_reconciliation import read_library_snapshot

_ROOT_ERRORS = frozenset({
    'invalid_reconciliation_identity_scope', 'invalid_reconciliation_root_configuration',
    'reconciliation_identity_root_collision', 'unknown_reconciliation_identity',
})
# Full explicit identity scan: the 120-second InventoryLimits default used by
# the generic API is too short for hundreds of EPUB/CRC checks. This scope
# remains read-only, cancellable and hard-capped by both Core validators.
_SCOPED_AUDIT_SECONDS = 600

_MESSAGES = {
    'inventory': 'Inventaire local', 'snapshot': 'Lecture des preuves locales',
    'reconciliation': 'Comparaison des preuves locales',
    'metadata_candidates': 'Comparaison des identifiants bibliographiques',
    'stored_paths': 'Vérification des chemins enregistrés', 'finished': 'Audit local terminé',
}


def _unavailable(reasons):
    return {'schema': 1, 'read_only': True, 'acquisition_authorized': False,
            'repair_authorized': False, 'status': 'incomplete_scan',
            'reasons': sorted(reasons)}


@dataclass(slots=True, repr=False)
class LibraryReconciliationReadBrokerImpl:
    service: object

    def audit(self, *, identity_id: str, control=None):
        """Audit only the explicitly requested ACTIVE identity, without actions.

        No path/options/remote toggle or private case payload is accepted.
        Snapshot precedes filesystem reads to reject incomplete migrations.
        Revalidate the selected identity, epoch and complete configured roots
        throughout the operation and before releasing even redacted counts.
        """
        generation, stamp = begin_inspection_cache(self.service)

        def finish(result, scope_valid):
            # The plugin sees redacted counters only. A private, bounded
            # inspection can be picked up once by CoreUI without a second scan.
            public = result.redacted()
            remember_inspection_cache(
                self.service, identity_id, result, scope_valid, generation, stamp,
            )
            return public

        return _run_scoped_audit(self.service, identity_id, control, finish)


def _run_scoped_audit(service, identity_id, control, finish):
    """One terminal, aggregate-only event per broker attempt.

    The event collector is optional; it can neither block the offline audit
    nor receive the private Inventory/Snapshot or untrusted exception text.
    """
    from ..diagnostics import Diagnostics

    started = monotonic()
    result = _execute_scoped_audit(service, identity_id, control, finish)
    try:
        diagnostics = getattr(service, 'diagnostics', None)
        # The same scoped helper serves CoreUI's *private* LocalInspection.
        # Only broker summaries are report-shaped; never classify a private
        # inspection object as a failed audit or pass it to Diagnostics.
        if type(diagnostics) is Diagnostics and type(result) is dict:
            diagnostics.add_core_reconciliation_result(
                result, duration_ms=max(0, int((monotonic() - started) * 1000)),
            )
    except Exception:
        # Diagnostics are optional and cannot change the audit result.
        pass
    return result


def _execute_scoped_audit(service, identity_id, control, finish):
    """Core-only completion hook; the public broker always returns redacted data."""
    try:
        config = service.config
        config_data = config.data
        identities = service.identities
        current = service.current_identity
        epoch = service._account_context_epoch
        if current is None or current.identity_id != identity_id:
            return _unavailable({'reconciliation_identity_not_active'})
        roots = configured_identity_roots(config, identity_id)
        projected = tuple((row.identity_id, row.directory_name or row.identity_id) for row in identities)
        configured = tuple((row['id'], row['directory_name']) for row in config.identities())
        if projected != configured:
            return _unavailable({'reconciliation_scope_changed'})
        if not any(row.identity_id == identity_id for row in identities):
            return _unavailable({'unknown_reconciliation_identity'})
        storage = service.storage
        database = Path(storage.path)
        if not database.is_absolute() or '..' in database.parts:
            return _unavailable({'snapshot_unavailable'})
        changed = False

        def same_scope():
            nonlocal changed
            try:
                same = (service.config is config and config.data is config_data
                        and service.identities is identities
                        and service.current_identity is current
                        and service._account_context_epoch == epoch
                        and current.identity_id == identity_id
                        and service.storage is storage and Path(storage.path) == database
                        and tuple((row.identity_id, row.directory_name or row.identity_id) for row in identities) == projected
                        and tuple((row["id"], row["directory_name"]) for row in config.identities()) == configured
                        and configured_identity_roots(config, identity_id) == roots)
            except Exception:
                same = False
            changed = changed or not same
            return not changed

        def cancelled():
            return not same_scope() or (control is not None and control.cancelled())

        def progress(event):
            if control is not None:
                counters = event.get('inventory', {})
                control.report(current=counters.get('files'), total=None,
                               message=_MESSAGES.get(event.get('phase'), 'Audit local'))

        progress({'phase': 'snapshot'})
        if cancelled():
            return _unavailable({'reconciliation_scope_changed' if changed else 'audit_cancelled'})
        snapshot_started = monotonic()
        snapshot = read_library_snapshot(database, cancelled=cancelled, identity_id=identity_id)
        snapshot_ms = max(0, min(30000, int((monotonic() - snapshot_started) * 1000)))

        def aggregate_snapshot_timing(value):
            # Only the public already-redacted dictionary can carry an
            # elapsed counter. Private LocalInspection remains opaque.
            return {**value, 'snapshot_ms': snapshot_ms} if type(value) is dict else value

        if snapshot.reasons:
            if changed:
                return aggregate_snapshot_timing(_unavailable({'reconciliation_scope_changed'}))
            return aggregate_snapshot_timing(_unavailable(snapshot.reasons))
        result = audit_library(
            database, roots, limits=InventoryLimits(seconds=_SCOPED_AUDIT_SECONDS),
            seconds=_SCOPED_AUDIT_SECONDS,
            # The 600-second whole-audit ceiling remains authoritative.
            # Distinct budgets: 30 s proof comparison, 30 s catalogue index,
            # then a reserved 30 s of metadata-only file matching.
            comparison_seconds=30, candidate_seconds=60,
            candidate_index_seconds=30,
            cancelled=cancelled, progress=progress, _snapshot=snapshot,
        )
        stopped = cancelled()
        if changed:
            return aggregate_snapshot_timing(_unavailable({'reconciliation_scope_changed'}))
        if stopped:
            return aggregate_snapshot_timing(_unavailable({'audit_cancelled'}))
        return aggregate_snapshot_timing(finish(result, same_scope))
    except ValueError as exc:
        reason = str(exc)
        return _unavailable({reason if reason in _ROOT_ERRORS else 'reconciliation_unavailable'})
    except Exception:
        # A TaskControl, parser or source exception must never expose paths,
        # identifiers, provider prose or traceback through the plugin API.
        return _unavailable({'reconciliation_unavailable'})
