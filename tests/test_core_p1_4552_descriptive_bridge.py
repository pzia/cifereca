"""#4552: one shared broker, one lease and one atomic descriptive writer."""
from contextlib import closing
import pytest

from cifereca.storage import Storage
from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_work_bridge import AggregateWorkBridge, PreparedDescriptive
from cifereca.core.notice_descriptive_selection_store import (
    set_descriptive_selection, descriptive_selection_input_digest,
)
from cifereca.core.notice_descriptive_materialization_store import (
    read_descriptive_materialization, publish_descriptive_materialization_txn,
)
from cifereca.core.notice_canonical_text_schema import (
    CanonicalTextSchema, canonical_text_schema_digest,
)
from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_queue import TaskPriority
from cifereca.task_runtime import TaskBrokerImpl
from tests.test_core_p1_4552_descriptive_materialization_store import (
    SELECTION, SOURCE, facts, materialization,
)


@pytest.mark.parametrize("changed", (False, True))
def test_admitted_descriptive_bridge_uses_existing_writer_and_lease(tmp_path, changed):
    with closing(Storage(tmp_path / "bridge.sqlite")) as db:
        budget = SharedTaskAdmission(max_inflight=2, reserve_user_slots=1)
        broker = TaskBrokerImpl(object(), max_workers=1, shared_admission=budget)
        schema = CanonicalTextSchema("age", "edition:fr", "plugin.age", "1.0",
                                     "age.v1", 128, True, ("adult",))
        pin = set_descriptive_selection(
            db, canonical_type="age", scope="edition:fr", selection=SELECTION,
            installed_source_sha256=SOURCE, expected_generation=0,
            schema_sha256=canonical_text_schema_digest(schema),
        )
        row = materialization(facts())
        spec = journal.descriptive_job_spec(
            target_edition_id=row.target_edition_id, canonical_type="age",
            scope=row.scope, implementation_digest=SOURCE,
            input_digest=descriptive_selection_input_digest(row.execution_key, pin),
        )
        ref = journal.request_job(db, spec, now=100, expected_generation=0)
        capture = lambda sql: (SELECTION, facts(), SOURCE)
        calls = []

        def prepare(current, control):
            assert current == spec
            control.check_cancelled()
            calls.append(current)
            return PreparedDescriptive(
                row, None,
                (lambda sql: (SELECTION, facts(revision="rev:2"), SOURCE)) if changed else capture,
                schema,
            )

        bridge = AggregateWorkBridge(
            db, broker, lambda *_: pytest.fail("cover writer used"),
            clock=lambda: 100, prepare_descriptive=prepare,
            on_committed=lambda *_: pytest.fail("cover notification used"),
        )
        hold = budget.acquire(priority=int(TaskPriority.METADATA))
        try:
            assert hold is not None
            assert bridge.offer(ref, circuit="local").outcome == "blocked"
            assert calls == []
            assert journal.read_job(db, ref).attempts == 0
            hold.release()
            handle = bridge.offer(ref, circuit="local").handle
            assert handle is not None
            assert handle.result(timeout=10) == ("transient_failure" if changed else "current")
            assert len(calls) == 1
            assert journal.read_job(db, ref).state == ("pending" if changed else "current")
            assert read_descriptive_materialization(
                db, target_edition_id=row.target_edition_id, canonical_type="age",
                scope=row.scope, capture_current_txn=capture, schema=schema,
            ) == (None if changed else row)
            assert db.entitlements() == db.acquisitions() == []
        finally:
            hold.release()
            broker.close()


def test_transaction_port_rolls_back_row_and_completion_together(tmp_path):
    from tests.test_core_p1_4552_descriptive_journal_atomic import setup
    db, row, capture, ref, lease = setup(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="rollback"):
            with db._lock, db._connect() as sql:
                sql.execute("BEGIN IMMEDIATE")
                assert publish_descriptive_materialization_txn(
                    sql, row, expected_execution_key=None, capture_current_txn=capture,
                    journal_lease=lease, journal_now=102,
                ) == row
                raise RuntimeError("rollback")
        assert journal.read_job(db, ref).state == "pending"
        assert read_descriptive_materialization(
            db, target_edition_id=row.target_edition_id, canonical_type=row.canonical_type,
            scope=row.scope, capture_current_txn=capture,
        ) is None
    finally:
        db.close(final=True)
