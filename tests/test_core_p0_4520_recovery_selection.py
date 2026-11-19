"""#4520: readable private selection, exact review, no implicit recovery."""
from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.core import derived_variant_recovery_selection as selection
from cifereca.core import derived_variant_journal as journal
from tests.test_core_p0_4520_recovery_operator import _setup


def _choices(service, **options):
    return selection.recovery_choices(service, identity_id="id1",
                                      **{"explicitly_requested": True, **options})


def test_choice_is_relative_and_only_reviewed_on_explicit_request(tmp_path):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        assert _choices(service, explicitly_requested=False) is None
        page = _choices(service)
        choice, = page.choices
        assert page.complete and page.next_cursor is None
        assert choice.parent_relative == "Book.epub"
        assert choice.target_relative == "variant.epub"
        assert choice.output_size == 9
        assert reservation.key not in repr(page) and str(tmp_path) not in repr(choice)
        assert selection.review_recovery_choice(service, page, choice) is None
        assert selection.review_recovery_choice(
            service, page, replace(choice), explicitly_requested=True,
        ) is None
        result = selection.review_recovery_choice(
            service, page, choice, explicitly_requested=True,
        )
        assert result is not None and result.choice is choice
        assert result.review.reservation == reservation
        assert len(db.placements()) == 1
        assert journal.summarize_derived_journal(db)["pending_count"] == 1
    finally:
        db.close()


@pytest.mark.parametrize("change", ["epoch", "unlinked", "changed_receipt", "claimed"])
def test_stale_or_unlinked_choice_never_prepares_confirmation(tmp_path, change):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        page = _choices(service)
        choice, = page.choices
        if change == "epoch":
            service._account_context_epoch += 2
        elif change == "unlinked":
            Path(intent.candidate_path).unlink()
        elif change == "changed_receipt":
            raw = db.metadata_get(reservation.key)
            import json
            changed = json.loads(raw)
            changed["nonce"] = "0" * 32 if changed["nonce"] != "0" * 32 else "1" * 32
            with db._lock, db._connect() as sql:
                sql.execute("UPDATE metadata SET value=? WHERE key=?",
                            (json.dumps(changed), reservation.key))
        else:
            with db._lock, db._connect() as sql:
                sql.execute("UPDATE placements SET path=? WHERE placement_id=?",
                            (intent.candidate_path, intent.parent_placement_id))
        assert selection.review_recovery_choice(
            service, page, choice, explicitly_requested=True,
        ) is None
        assert journal.summarize_derived_journal(db)["pending_count"] == 1
    finally:
        db.close()


def test_private_cursor_stays_with_original_scope(tmp_path):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        other = replace(intent, candidate_path=str(Path(intent.candidate_path).with_name("other.epub")),
                        staged_path=str(Path(intent.staged_path).with_name("other-stage.epub")),
                        options_fingerprint="other")
        journal.reserve_derived_publication(db, other)
        page = _choices(service, limit=1)
        assert not page.complete and page.next_cursor
        next_page = _choices(service, previous=page, limit=1)
        assert next_page.complete
        assert page.choices[0].target_relative != next_page.choices[0].target_relative
        service._source_context_epoch += 1
        assert _choices(service, previous=page, limit=1) is None
    finally:
        db.close()
