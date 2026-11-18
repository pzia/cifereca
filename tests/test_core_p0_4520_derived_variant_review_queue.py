"""#4520: review handles are exact, bounded and profile private."""
from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.core import derived_variant_journal as journal
from cifereca.core.derived_variant_review_queue import pending_derived_review_page
from tests.test_core_p0_4520_derived_journal import _fixture, _prepared_publication, _publish_reserved


def _reserve(db, intent, n):
    return journal.reserve_derived_publication(db, replace(
        intent, options_fingerprint=str(n),
        staged_path=str(Path(intent.staged_path).with_name(f"stage-{n}.epub")),
        candidate_path=str(Path(intent.candidate_path).with_name(f"out-{n}.epub")),
    ))


def test_pages_exact_handles_and_no_path_or_content_digest(tmp_path):
    service, intent = _fixture(tmp_path)
    db = service.storage
    try:
        handles = [_reserve(db, intent, n) for n in range(5)]
        first = pending_derived_review_page(db, identity_id="id1", limit=2)
        second = pending_derived_review_page(
            db, identity_id="id1", limit=2, after_key=first["next_key"],
        )
        third = pending_derived_review_page(
            db, identity_id="id1", limit=2, after_key=second["next_key"],
        )
        combined = first["reservations"] + second["reservations"] + third["reservations"]
        assert combined == tuple(sorted(handles, key=lambda h: h.key))
        assert third["complete"] and third["next_key"] is None
        assert not first["recovery_authorized"] and not first["destructive_cleanup_allowed"]
        assert intent.parent_path not in repr(first)
        assert intent.output_sha256 not in repr(first)
        assert journal.summarize_derived_journal(db)["pending_count"] == 5
    finally:
        db.close()


def test_foreign_cursor_and_ambiguous_owner_refuse(tmp_path):
    service, intent = _fixture(tmp_path)
    db = service.storage
    try:
        handle = _reserve(db, intent, 1)
        db.identity_put({"identity_id": "other", "label": "Other", "directory_name": "other"})
        assert pending_derived_review_page(db, identity_id="other")["reservations"] == ()
        with pytest.raises(journal.DerivedJournalConflict, match="cursor_changed"):
            pending_derived_review_page(db, identity_id="other", after_key=handle.key)
        with db._lock, db._connect() as sql:
            sql.execute("DROP INDEX IF EXISTS uq_identity_directory_name")
            sql.execute("UPDATE identities SET directory_name='library' WHERE identity_id='other'")
        with pytest.raises(journal.DerivedJournalConflict, match="scope"):
            pending_derived_review_page(db, identity_id="id1")
    finally:
        db.close()


def test_completed_cursor_requires_refresh_and_is_not_pending(tmp_path):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        assert pending_derived_review_page(db, identity_id="id1")["reservations"] == (reservation,)
        _publish_reserved(db, intent, reservation)
        assert pending_derived_review_page(db, identity_id="id1")["reservations"] == ()
        with pytest.raises(journal.DerivedJournalConflict, match="cursor_changed"):
            pending_derived_review_page(db, identity_id="id1", after_key=reservation.key)
        assert journal.summarize_derived_journal(db)["committed_count"] == 1
    finally:
        db.close()


@pytest.mark.parametrize("limit", [0, 33, True, "2"])
def test_invalid_page_bounds(tmp_path, limit):
    service, _intent = _fixture(tmp_path)
    try:
        with pytest.raises(journal.DerivedJournalConflict, match="invalid"):
            pending_derived_review_page(service.storage, identity_id="id1", limit=limit)
    finally:
        service.storage.close()
