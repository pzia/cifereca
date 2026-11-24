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
