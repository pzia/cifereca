from cifereca.storage import Storage
from cifereca.core.notice_parse_witness import ParseWitnessReview
from cifereca.core.notice_parse_witness_alert import record_parse_witness_review
from tests.test_core_p1_4552_persistent_parse_witness import put, KEY


def test_alert_coalesces_across_restart_and_rearms_after_healthy(tmp_path):
    path = tmp_path / "alerts.sqlite"
    db = Storage(path)
    approved = put(db)
    def record(db, review, generation=approved.generation):
        return record_parse_witness_review(
            db, key=KEY, parser_id="paris/notice", generation=generation,
            review=review,
        )
    bad = ParseWitnessReview("parser_regression_confirmed", ("critical_field_2",))
    assert record(db, bad)
    assert not record(db, bad)
    db.close(final=True)
    db = Storage(path)
    try:
        assert not record(db, bad)
        assert not record(db, ParseWitnessReview("unverified", ()))
        assert not record(db, bad)
        assert not record(db, ParseWitnessReview("unchanged", ()))
        assert record(db, bad)
        replacement = put(db, body=b"new", expected_generation=approved.generation)
        assert not record(db, bad)
        assert record(db, bad, replacement.generation)
        assert db.entitlements() == []
        assert db.acquisitions() == []
    finally:
        db.close(final=True)


def test_diagnostic_projection_rejects_obsolete_pin(tmp_path):
    from dataclasses import replace
    from cifereca.core.notice_parse_witness_alert import read_parse_witness_diagnostic
    db = Storage(tmp_path / "projection.sqlite")
    try:
        first = put(db)
        key = dict(key=KEY, parser_id="paris/notice")
        assert read_parse_witness_diagnostic(db, **key) is None
        assert record_parse_witness_review(
            db, **key, generation=first.generation,
            review=ParseWitnessReview("parser_regression_confirmed", ("critical_field_2",)),
        )
        assert read_parse_witness_diagnostic(db, **key) == {
            "generation": first.generation, "status": "parser_regression_confirmed",
            "fields": ["critical_field_2"], "capture_state": "not_recorded",
        }
        assert read_parse_witness_diagnostic(
            db, key=replace(KEY, identity_id="bob"), parser_id="paris/notice",
        ) is None
        put(db, body=b"new", expected_generation=first.generation)
        assert read_parse_witness_diagnostic(db, **key) is None
    finally:
        db.close(final=True)
