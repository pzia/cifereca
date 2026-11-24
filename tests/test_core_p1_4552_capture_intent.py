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
    from types import SimpleNamespace
    monkeypatch.setattr(intent, "time", SimpleNamespace(time=lambda: 1000))
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
    from types import SimpleNamespace
    monkeypatch.setattr(intent, "time", SimpleNamespace(time=lambda: 1000))
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


@pytest.mark.parametrize("raw_present", (True, False))
def test_recover_archive_linked_before_sqlite_outcome(tmp_path, monkeypatch, raw_present):
    from cifereca.core import notice_regression_capture_intent as intent
    db = Storage(tmp_path / "interrupted.sqlite")
    try:
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            diagnostic = enqueue_capture_txn(
                sql, scope="a"*64, generation=1,
                review=ParseWitnessReview("parser_drift_suspected", ("critical_field_1",)),
                input_digest=sha256(b"body").hexdigest(),
                snapshots={name: "{}" for name in (
                    "baseline.json", "last_healthy.json", "current_parse.json", "environment.json"
                )},
            )
        def interrupted(*args):
            raise RuntimeError("process interrupted after archive link")
        with monkeypatch.context() as patch:
            patch.setattr(intent, "_record_outcome", interrupted)
            with pytest.raises(RuntimeError):
                intent.export_capture(db, diagnostic,
                    members={"response.raw": b"body"} if raw_present else {},
                    directory=tmp_path)
        assert intent.capture_status(db, diagnostic)["capture_state"] == "pending"
        archive, = tmp_path.glob("*.zip")
        before = archive.read_bytes()
        # The cache may have disappeared, or permission may have changed.
        assert intent.export_capture(db, diagnostic, members={}, directory=tmp_path) == (
            "complete" if raw_present else "partial")
        assert archive.read_bytes() == before
        assert len(list(tmp_path.glob("*.zip"))) == 1
        assert intent.pending_capture_ids(db) == ()
    finally:
        db.close(final=True)


@pytest.mark.parametrize("name", ("baseline.json", "last_healthy.json", "current_parse.json",
                                  "environment.json", "diff.json"))
def test_resolver_cannot_fabricate_omitted_snapshot(tmp_path, name):
    db = Storage(tmp_path / "snapshot.sqlite")
    try:
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            diagnostic = enqueue_capture_txn(sql, scope="a"*64, generation=1,
                review=ParseWitnessReview("parser_drift_suspected", ("critical_field_1",)),
                input_digest=sha256(b"body").hexdigest())
        assert export_capture(db, diagnostic, members={name: b"{}"},
                              directory=tmp_path) == "failed"
        assert not list(tmp_path.glob("*.zip"))
    finally:
        db.close(final=True)


def test_busy_replay_capture_retries_without_blocking_batch(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from cifereca.core import notice_regression_capture_intent as intent
    from cifereca.core.notice_c_work_bridge import RetryableNoticeWork

    clock = [1000]
    monkeypatch.setattr(intent, "time", SimpleNamespace(time=lambda: clock[0]))
    path = tmp_path / "busy.sqlite"
    db = Storage(path)
    try:
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            for scope in ("a"*64, "b"*64):
                enqueue_capture_txn(sql, scope=scope, generation=1,
                    review=ParseWitnessReview("parser_drift_suspected", ("critical_field_1",)),
                    input_digest=sha256(b"body").hexdigest())
        first, second = intent.pending_capture_ids(db)

        def resolve(diagnostic):
            if diagnostic == first:
                raise RetryableNoticeWork("notice_reparse_provider_busy")
            return {}

        assert intent.resume_captures(db, resolve, directory=tmp_path) == (
            (first, "failed"), (second, "partial"))
        assert intent.capture_status(db, first) == {
            "capture_state": "failed", "attempts": 1, "retry_after": 1002}
        assert len(list(tmp_path.glob("*.zip"))) == 1
        assert intent.pending_capture_ids(db) == ()
    finally:
        db.close(final=True)

    db = Storage(path)
    try:
        clock[0] = 1002
        assert intent.pending_capture_ids(db) == (first,)
        assert intent.resume_captures(db, lambda _: {"response.raw": b"body"},
                                      directory=tmp_path) == ((first, "partial"),)
        assert len(list(tmp_path.glob("*.zip"))) == 2
        assert intent.pending_capture_ids(db) == ()
    finally:
        db.close(final=True)
