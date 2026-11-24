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


def test_capture_diagnostics_rejects_unbounded_or_foreign_payloads():
    from cifereca.diagnostics import Diagnostics
    from cifereca.core.notice_parse_witness_alert import report_capture_diagnostic

    diagnostics = Diagnostics()
    for diagnostic, state in (("private-coordinate", "partial"), ("a"*64, "unknown"),
                              (None, "partial"), ("a"*64, "capacity_refused")):
        report_capture_diagnostic(diagnostics, diagnostic, state, rejected=True)
    assert diagnostics.snapshot() == []
    report_capture_diagnostic(diagnostics, None, "capacity_refused", rejected=True)
    report_capture_diagnostic(diagnostics, "a"*64, "failed")
    first, second = diagnostics.snapshot()
    assert first["status"] == "warning"
    assert first["detail"] == {"diagnostic_id": None, "capture_state": "capacity_refused"}
    assert second["operation"] == "regression_capture_resumed"
    assert second["status"] == "error"
    assert second["detail"] == {"diagnostic_id": "a"*64, "capture_state": "failed"}

    class BrokenJournal:
        def add(self, *args, **kwargs):
            raise OSError("journal unavailable")

    report_capture_diagnostic(BrokenJournal(), "a"*64, "partial", rejected=True)


def test_capture_budget_preserves_parser_context_when_observations_are_large(tmp_path):
    import json
    from hashlib import sha256
    from cifereca.core.notice_parse_witness import admitted_source_scope_sha256
    from cifereca.core.notice_parse_witness_store import _PREFIX as pin_prefix
    from cifereca.core.notice_parse_witness_alert import review_publication_txn
    from cifereca.core.notice_regression_capture_intent import (
        _PREFIX as capture_prefix, export_capture,
    )
    from tests.test_core_p1_4552_persistent_parse_witness import RESPONSE, observation
    import zipfile

    db = Storage(tmp_path / "budget.sqlite")
    try:
        put(db)
        current = observation(version="1.0.0")
        scope = admitted_source_scope_sha256(KEY, parser_id=current.parser_id)
        pin = db.metadata_get(pin_prefix + scope)
        # Fit the old three snapshots in 16 KiB, leaving only 32 bytes
        # for environment.json, which previously disappeared entirely.
        fields = {"Title": "Un titre", "ExtraA": "a" * 7900, "ExtraB": "b"}
        sample = observation(fields=fields)
        size = 16384 - 32 - sum(len(v.encode("utf-8")) for v in (
            pin, current.raw_json, sample.raw_json,
        ))
        assert 0 < size < 8190
        fields["ExtraB"] += "b" * size
        candidate = observation(fields=fields)
        assert len(candidate.raw_json.encode("utf-8")) + len(pin.encode("utf-8")) + len(current.raw_json.encode("utf-8")) == 16384 - 32
        digest = sha256(RESPONSE).hexdigest()
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            reject, diagnostic = review_publication_txn(
                sql, current=current, candidate=candidate, input_digest=digest,
                implementation_digest="a" * 64,
            )
        assert reject and diagnostic
        snapshots = json.loads(db.metadata_get(capture_prefix + diagnostic))["snapshots"]
        assert sum(len(v.encode("utf-8")) for v in snapshots.values()) <= 16384
        assert json.loads(snapshots["environment.json"])["implementation_sha256"] == "a" * 64
        assert json.loads(snapshots["environment.json"])["input_sha256"] == digest
        assert "current_parse.json" not in snapshots
        assert export_capture(db, diagnostic, members={}, directory=tmp_path) == "partial"
        archive, = tmp_path.glob("*.zip")
        with zipfile.ZipFile(archive) as result:
            manifest = json.loads(result.read("manifest.json"))
            assert "environment.json" not in manifest["omitted"]
            assert "current_parse.json" in manifest["omitted"]
    finally:
        db.close(final=True)
