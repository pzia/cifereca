"""Q12 exact public-age preparation for the existing admitted work bridge.

The hold_selected port belongs to the trusted Core lifecycle owner and
must qualify the selected archive against independent trust inputs. It
is never a plugin callback. Package authority is rechecked outside SQL
at publication; the writer reattests the current source and pin in SQL.
"""
from contextlib import contextmanager

from .notice_c_public_age_request import (
    capture_exact_c_age_txn, reattest_public_age_job_txn,
)
from .notice_consolidator_manifest import ManifestTextConsolidator
from .notice_descriptive_selection_store import require_descriptive_selection
from .notice_descriptive_runner_adapter import propose_registered_descriptive_materialization
from .notice_descriptive_materialization import DescriptiveMaterialization
from .notice_descriptive_materialization_store import _row_key, _decode
from .notice_c_work_bridge import PreparedDescriptive, UnavailableNoticeWork


def prepare_exact_public_age(storage, spec, control, *, hold_selected):
    """Prepare one journal snapshot; no claim, submission, or storage write."""
    control.check_cancelled()
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        pin, admitted = reattest_public_age_job_txn(db, spec)
        target, scope = spec.public_age_locator
        facts, links = capture_exact_c_age_txn(db, target_edition_id=target)
        coordinate = DescriptiveMaterialization(
            1, "", "age", target, scope, "", "", "", "", False, (),
        )
        key = _row_key(coordinate)
        found = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        previous = None if found is None else _decode(found[0])
        if previous is not None and _row_key(previous) != key:
            raise ValueError("corrupt_descriptive_materialization")
    with hold_selected(pin) as candidate:
        if type(candidate) is not ManifestTextConsolidator:
            raise UnavailableNoticeWork("notice_descriptive_consolidator_unavailable")
        require_descriptive_selection(pin, pin.selection, candidate.installed.source_sha256,
                                      schema=candidate.schema)
    control.check_cancelled()
    proposal = propose_registered_descriptive_materialization(
        declarations=(candidate.declaration,), text_schemas=(candidate.schema,),
        installed=candidate.installed, canonical_type="age", scope=scope,
        selected_plugin_id=pin.selection.plugin_id, selected_version=pin.selection.version,
        target_edition_id=target, source_facts=facts, attested_strong_links=links,
    )
    control.check_cancelled()
    if proposal is None:
        raise UnavailableNoticeWork("notice_descriptive_no_publishable_result")

    @contextmanager
    def hold_current():
        with hold_selected(pin) as live:
            if live != candidate:
                raise UnavailableNoticeWork("notice_descriptive_consolidator_changed")
            yield

    def recapture(db):
        latest, current = reattest_public_age_job_txn(db, spec)
        if latest != pin or current != admitted:
            raise ValueError("notice_descriptive_public_age_input_changed")
        return pin.selection, current, candidate.installed.source_sha256

    return PreparedDescriptive(
        proposal.materialization, None if previous is None else previous.execution_key,
        recapture, candidate.schema, hold_current,
    )
