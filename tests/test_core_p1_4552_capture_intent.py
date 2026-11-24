from hashlib import sha256
import pytest
from cifereca.storage import Storage
from cifereca.core.notice_parse_witness import ParseWitnessReview
from cifereca.core.notice_regression_capture_intent import enqueue_capture_txn, export_capture


def test_capture_intent_rollback_restart_retry(tmp_path):
    path = tmp_path / "db.sqlite"
    db = Storage(path)
    args = dict(scope="a"*64, generation=1,
                review=ParseWitnessReview("parser_drift_suspected", ("critical_field_1",)),
                input_digest=sha256(b"body").hexdigest())
    with pytest.raises(RuntimeError):
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            diagnostic = enqueue_capture_txn(sql, **args)
            raise RuntimeError("rollback")
    assert export_capture(db, diagnostic, members={}, directory=tmp_path) is None
    with db._lock, db._connect() as sql:
        sql.execute("BEGIN IMMEDIATE")
        assert enqueue_capture_txn(sql, **args) == diagnostic
        assert enqueue_capture_txn(sql, **args) == diagnostic
    db.close(final=True)
    db = Storage(path)
    try:
        assert export_capture(db, diagnostic, members={"response.raw": b"wrong"},
                              directory=tmp_path) == "failed"
        assert not list(tmp_path.glob("*.zip"))
        assert export_capture(db, diagnostic, members={"response.raw": b"body"},
                              directory=tmp_path) == "partial"
        assert export_capture(db, diagnostic, members={}, directory=tmp_path) == "partial"
        assert len(list(tmp_path.glob("*.zip"))) == 1
    finally:
        db.close(final=True)


def test_restart_drain_is_bounded_and_skips_completed(tmp_path):
    from cifereca.core.notice_regression_capture_intent import pending_capture_ids, resume_captures
    db = Storage(tmp_path / "drain.sqlite")
    try:
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            for scope in ("a"*64, "b"*64):
                enqueue_capture_txn(sql, scope=scope, generation=1,
                    review=ParseWitnessReview("parser_drift_suspected", ("critical_field_1",)),
                    input_digest=sha256(b"body").hexdigest())
        calls = []
        def resolve(diagnostic):
            calls.append(diagnostic)
            return {}
        assert len(pending_capture_ids(db)) == 2
        first = resume_captures(db, resolve, directory=tmp_path, limit=1)
        assert len(first) == 1 and first[0][1] == "partial"
        assert len(pending_capture_ids(db)) == 1
        second = resume_captures(db, resolve, directory=tmp_path, limit=1)
        assert len(second) == 1 and second[0][1] == "partial"
        assert resume_captures(db, resolve, directory=tmp_path) == ()
        assert len(set(calls)) == 2
    finally:
        db.close(final=True)


def test_failed_capture_backoff_survives_restart(tmp_path, monkeypatch):
    from cifereca.core import notice_regression_capture_intent as intent
    monkeypatch.setattr(intent.time, "time", lambda: 1000)
    path = tmp_path / "fair.sqlite"
    db = Storage(path)
    try:
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            for scope in ("a"*64, "b"*64):
                enqueue_capture_txn(sql, scope=scope, generation=1,
                    review=ParseWitnessReview("parser_drift_suspected", ("critical_field_1",)),
                    input_digest=sha256(b"body").hexdigest())
        first, second = intent.pending_capture_ids(db)
        assert export_capture(db, first, members={"response.raw": b"wrong"},
                              directory=tmp_path) == "failed"
        assert intent.pending_capture_ids(db, limit=1) == (second,)
    finally:
        db.close(final=True)
    db = Storage(path)
    try:
        assert intent.pending_capture_ids(db, now=1001) == (second,)
        assert intent.pending_capture_ids(db, now=1002) == (first, second)
        assert intent.resume_captures(db, lambda _: {}, directory=tmp_path,
                                      limit=1) == ((second, "partial"),)
    finally:
        db.close(final=True)


def test_resolver_failure_backoff_and_status(tmp_path, monkeypatch):
    from cifereca.core import notice_regression_capture_intent as intent
    monkeypatch.setattr(intent.time, "time", lambda: 1000)
    db = Storage(tmp_path / "resolver.sqlite")
    try:
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            for scope in ("a"*64, "b"*64):
                enqueue_capture_txn(sql, scope=scope, generation=1,
                    review=ParseWitnessReview("parser_drift_suspected", ("critical_field_1",)),
                    input_digest=sha256(b"body").hexdigest())
        first, second = intent.pending_capture_ids(db)
        def unavailable(_):
            raise OSError("unavailable")
        assert intent.resume_captures(db, unavailable, limit=1) == ((first, "failed"),)
        assert intent.capture_status(db, first) == {
            "capture_state": "failed", "attempts": 1, "retry_after": 1002}
        assert intent.pending_capture_ids(db, limit=1) == (second,)
        assert intent.capture_status(db, "0"*64) is None
        assert intent.resume_captures(db, lambda _: {}, directory=tmp_path,
                                      limit=1) == ((second, "partial"),)
        assert intent.capture_status(db, second) == {"capture_state": "partial"}
    finally:
        db.close(final=True)


def test_capacity_preserves_unfinished_capture(tmp_path, monkeypatch):
    from cifereca.core import notice_regression_capture_intent as intent
    monkeypatch.setattr(intent, "_MAX_INTENTS", 2)
    db = Storage(tmp_path / "capacity.sqlite")
    def enqueue(scope):
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            return enqueue_capture_txn(sql, scope=scope*64, generation=1,
                review=ParseWitnessReview("parser_drift_suspected", ("critical_field_1",)),
                input_digest=sha256(b"body").hexdigest())
    try:
        first, second = enqueue("a"), enqueue("b")
        assert enqueue("c") is None
        assert intent.export_capture(db, first, members={}, directory=tmp_path) == "partial"
        third = enqueue("c")
        assert third is not None
        assert intent.capture_status(db, first) is None
        assert set(intent.pending_capture_ids(db)) == {second, third}
        assert len(list(tmp_path.glob("*.zip"))) == 1
    finally:
        db.close(final=True)


def test_concurrent_capture_outcomes(tmp_path):
    import json
    from cifereca.core import notice_regression_capture_intent as intent
    db = Storage(tmp_path / "concurrent.sqlite")
    try:
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            diagnostic = enqueue_capture_txn(sql, scope="a"*64, generation=1,
                review=ParseWitnessReview("parser_drift_suspected", ("critical_field_1",)),
                input_digest=sha256(b"body").hexdigest())
            key = intent._PREFIX + diagnostic
            previous = sql.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()[0]
        record = json.loads(previous)
        assert intent._record_outcome(db, key, previous, record, "failed") == "failed"
        assert intent.export_capture(db, diagnostic, members={}, directory=tmp_path) == "partial"
        assert intent._record_outcome(db, key, previous, record, "failed") == "partial"
        assert intent.capture_status(db, diagnostic)["capture_state"] == "partial"
    finally:
        db.close(final=True)
