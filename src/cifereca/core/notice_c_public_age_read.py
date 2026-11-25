"""Q12 exact public-age read projection for trusted Core consumers.

Never invoke a consolidator on read. A materialization is current only
under the independently qualified archive, current explicit selection and
authoritative exact C/source lineage. No age interpretation, permission,
fallback or new cache. The caller owns scope applicability and the
serialized lifecycle/managed package guard supplied by hold_selected.
"""
from dataclasses import dataclass
from sqlite3 import Error as SQLiteError

from .notice_c_cover_index import canonical_cover_document_ref
from .notice_c_public_age_request import _snapshot
from .notice_consolidator_selection import _identifier
from .notice_consolidator_manifest import ManifestTextConsolidator
from .notice_descriptive_materialization import DescriptiveMaterialization
from .notice_descriptive_materialization_store import read_descriptive_materialization
from .notice_descriptive_selection_store import (
    read_descriptive_selection_txn, require_descriptive_selection,
)


@dataclass(frozen=True, slots=True)
class PublicAgeRead:
    status: str
    materialization: DescriptiveMaterialization | None = None


def read_public_age_result(storage, *, target_edition_id, scope, hold_selected):
    """Return a closed status; only current carries a materialization.

    unconfigured: no schema-pinned explicit selection.
    unavailable: no qualified active implementation, or read unavailable.
    unknown: no materialization yet.
    stale: persisted result failed source/selection/schema validation.
    current: exact validated result, not an age or rights decision by Core.
    """
    canonical_cover_document_ref(target_edition_id)
    if not _identifier(scope, max_len=160) or not callable(hold_selected):
        raise ValueError("notice_descriptive_invalid_read_scope")
    try:
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN")
            pin = read_descriptive_selection_txn(db, canonical_type="age", scope=scope)
        if pin is None or pin.selection is None or pin.schema_sha256 is None:
            return PublicAgeRead("unconfigured")
        with hold_selected(pin) as candidate:
            if type(candidate) is not ManifestTextConsolidator:
                return PublicAgeRead("unavailable")
            require_descriptive_selection(pin, pin.selection, candidate.installed.source_sha256,
                                          schema=candidate.schema)

            def capture(db):
                snapshot = _snapshot(db, target_edition_id=target_edition_id, scope=scope)
                if snapshot is None or snapshot[1] != pin:
                    raise ValueError("notice_descriptive_public_age_input_changed")
                _, latest, admitted = snapshot
                return latest.selection, admitted, candidate.installed.source_sha256

            try:
                result = read_descriptive_materialization(
                    storage, target_edition_id=target_edition_id,
                    canonical_type="age", scope=scope,
                    capture_current_txn=capture, schema=candidate.schema,
                )
            except ValueError:
                return PublicAgeRead("stale")
            if result is None:
                return PublicAgeRead("unknown")
            return PublicAgeRead("current", result)
    except (ValueError, OSError, RuntimeError, SQLiteError):
        # Closed projection: paths, package metadata and diagnostic strings
        # must not become display text or documentary evidence.
        return PublicAgeRead("unavailable")
