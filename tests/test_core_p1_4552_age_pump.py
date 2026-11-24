"""Q12 bounded opt-in local lane using the real existing runtime and broker."""
from contextlib import closing

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_work_bridge import UnavailableNoticeWork
from cifereca.core.notice_c_work_runtime import NoticeWorkRuntime
from cifereca.core.notice_c_reparse_execution import PublicReplayRegistry
from cifereca.storage import Storage
from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_runtime import TaskBrokerImpl


def test_public_age_lane_is_explicit_and_filters_legacy_and_unknown_digest(tmp_path):
    with closing(Storage(tmp_path / "age.sqlite")) as db:
        broker = TaskBrokerImpl(object(), max_workers=3, shared_admission=
                                SharedTaskAdmission(max_inflight=4, reserve_user_slots=1))
        runtime = NoticeWorkRuntime(db, broker, clock=lambda: 100)
        seen = []

        def enqueue(edition, digest, locator):
            spec = journal.descriptive_job_spec(
                target_edition_id=edition, canonical_type="age", scope="fr:editions",
                input_digest="a"*64, implementation_digest=digest, public_age=locator,
            )
            return journal.request_job(db, spec, now=100, expected_generation=0)

        exact = enqueue("edition:exact", "b"*64, True)
        legacy = enqueue("edition:legacy", "b"*64, False)
        unknown = enqueue("edition:unknown", "c"*64, True)
        try:
            assert not runtime._pump.tick().handles
            assert journal.read_job(db, exact).attempts == 0
            def prepare(spec, control):
                control.check_cancelled()
                seen.append(spec)
                raise UnavailableNoticeWork("test_unavailable")

            assert runtime.install_public_age_execution(prepare, implementation_digests=("b"*64,))
            # An existing idle replay-registry rebuild must preserve the lane.
            assert runtime.install_replay_registry(PublicReplayRegistry())
            scan = runtime.request_tick()
            assert scan is not None
            tick = scan.result(timeout=10)
            assert tick.examined <= 32 and len(tick.handles) == 1
            assert tick.handles[0].result(timeout=10) == "error"
            assert seen == [journal.read_job(db, exact).spec]
            assert journal.read_job(db, exact).attempts == 1
            for ref in (legacy, unknown):
                assert journal.read_job(db, ref).attempts == 0
            assert not runtime.install_public_age_execution(prepare, implementation_digests=("b"*64,))
            assert db.entitlements() == db.acquisitions() == []
        finally:
            runtime.close()
            broker.close()
