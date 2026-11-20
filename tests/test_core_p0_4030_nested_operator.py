"""#4030: admission and queued/batch reads never settle caller writes."""
import sqlite3
from pathlib import Path

import pytest

from cifereca.core import local_origin_suggestion_operator as operator
from cifereca.core import local_origin_suggestion_batch as batch
from tests.test_core_p0_3171_atomic_publication_v14 import store, BYTES
from tests.test_core_p0_4030_suggestion_operator import _service, _admit


@pytest.mark.parametrize("operation", ["admit", "current", "individual", "batch_bound", "batch"])
@pytest.mark.parametrize("finish", ["commit", "rollback"])
def test_nested_operator_keeps_caller_transaction(store, tmp_path, operation, finish):
    service, _root, pid = _service(store, tmp_path)
    request = _admit(service, pid)
    scope = batch.admit_origin_batch(service, confirmed=True)
    assert request is not None and scope is not None
    source = Path(store.placement_get(pid)["path"])
    db = store._connect()
    other = sqlite3.connect(store.path)
    key = "synthetic.4030.operator.caller"
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO metadata(key,value) VALUES(?,?)", (key, "pending"))
        if operation == "admit":
            assert _admit(service, pid) is None
        elif operation == "current":
            assert request.current() is False
        elif operation == "individual":
            result = operator.run_local_origin_suggestion(service, request)
            assert result["status"] == "excluded" and result["suggestions"] is None
        elif operation == "batch_bound":
            assert scope.bound() is False
        else:
            assert batch.run_origin_batch(service, scope) is None
        assert db.in_transaction
        assert db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone() == ("pending",)
        assert other.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone() is None
        if finish == "commit":
            db.commit()
        else:
            db.rollback()
        assert other.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone() == (
            ("pending",) if finish == "commit" else None)
        assert operator.run_local_origin_suggestion(service, request)["status"] == "partial"
        assert batch.run_origin_batch(service, scope)["status"] == "partial"
        assert source.read_bytes() == BYTES
    finally:
        if db.in_transaction:
            db.rollback()
        other.close()
