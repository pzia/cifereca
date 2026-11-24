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


def test_diagnostic_projection_rejects_foreign_capture_reference(tmp_path):
    import json
    from hashlib import sha256
    from cifereca.core.notice_parse_witness import admitted_source_scope_sha256
    from cifereca.core.notice_parse_witness_alert import (
        _ALERT_PREFIX, read_parse_witness_diagnostic,
    )
    from cifereca.core.notice_regression_capture_intent import enqueue_capture_txn
    db = Storage(tmp_path / "foreign.sqlite")
    try:
        pin = put(db)
        key = dict(key=KEY, parser_id="paris/notice")
        scope = admitted_source_scope_sha256(**key)
        review = ParseWitnessReview("parser_regression_confirmed", ("critical_field_2",))
        assert record_parse_witness_review(db, **key, generation=pin.generation, review=review)
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            args = dict(generation=pin.generation, review=review,
                        input_digest=sha256(b"body").hexdigest())
            own = enqueue_capture_txn(sql, scope=scope, **args)
            foreign = enqueue_capture_txn(sql, scope="f"*64, **args)
            alert_key = _ALERT_PREFIX + scope
            raw, = sql.execute("SELECT value FROM metadata WHERE key=?", (alert_key,)).fetchone()
            alert = json.loads(raw)
            alert["capture_id"] = foreign
            sql.execute("UPDATE metadata SET value=? WHERE key=?", (json.dumps(alert), alert_key))
        assert read_parse_witness_diagnostic(db, **key)["capture_state"] == "metadata_unavailable"
        with db._lock, db._connect() as sql:
            alert["capture_id"] = own
            sql.execute("UPDATE metadata SET value=? WHERE key=?", (json.dumps(alert), alert_key))
        assert read_parse_witness_diagnostic(db, **key)["capture_state"] == "pending"
    finally:
        db.close(final=True)
