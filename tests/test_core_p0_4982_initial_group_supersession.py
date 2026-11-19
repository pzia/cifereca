"""A different durable initial-group target fences only the superseded job."""
import pytest

from cifereca.core import notice_c_cover_index as index
from cifereca.core import notice_c_initial_group_publication as initial
from cifereca.core import notice_c_job_journal as journal
from cifereca.core import notice_c_origin_link_producer as origin
from tests.test_core_p0_4982_initial_group_publication import _budget
from tests.test_core_p0_4982_notice_work_runtime import open_core
from tests.test_core_p0_4982_relink_inputs import add_third
from tests.test_core_p0_6366_origin_attested_public_c import _origins, _source_rows
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist


def _two(core, tmp_path):
    _origins(core.storage, tmp_path)
    pair = tuple(persist(core.storage, row, at=100) for row in _source_rows())
    for source in pair:
        index.persist_exact_public_search_cover(core.storage, source)
    ref = initial.request_initial_group_publication(core.storage, pair[0], now=100)
    assert ref is not None
    return pair, ref


@pytest.mark.parametrize("published", [False, True])
def test_new_group_target_supersedes_old_job_without_retry(tmp_path, published):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        pair, old_ref = _two(core, tmp_path)
        old = journal.read_job(core.storage, old_ref)
        third = add_third(core.storage, tmp_path)
        index.persist_exact_public_search_cover(core.storage, third)
        if published:
            new_c = origin.reconcile_origin_linked_public_c(core.storage, third)
            assert new_c is not None
            assert new_c.document.source_key != old.spec.target
        else:
            new_ref = initial.request_initial_group_publication(core.storage, third, now=101)
            assert new_ref is not None and new_ref.key != old_ref.key
        events = []
        core.notice_work.bind_committed_observer(lambda *args: events.append(args))
        bridge = core.notice_work._pump._bridge
        assert bridge.submit(old_ref).result(timeout=10) == "stale"
        stale = journal.read_job(core.storage, old_ref)
        assert (stale.state, stale.reason, stale.attempts, stale.lease_until) == (
            "stale", "input_superseded", 1, 0,
        )
        assert events == []
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        assert journal.read_job(core.storage, old_ref) == stale
        bridge = core.notice_work._pump._bridge
        assert bridge.submit(old_ref).result(timeout=10) == "not_ready"
        if not published:
            assert bridge.submit(new_ref).result(timeout=10) == "current"
        group = origin.reconcile_origin_linked_public_c(core.storage, third)
        assert group is not None and group.document.source_key != old.spec.target
        assert {m.source_ref for m in group.sources} == {s.document.source_key for s in (*pair, third)}
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []


def test_changed_target_without_durable_successor_preserves_bounded_retry(tmp_path):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        _, old_ref = _two(core, tmp_path)
        add_third(core.storage, tmp_path)
        assert core.notice_work._pump._bridge.submit(old_ref).result(timeout=10) == "transient_failure"
        job = journal.read_job(core.storage, old_ref)
        assert job.state == "pending" and job.reason == "transient" and job.attempts == 1


def test_explicit_reattestation_can_renew_same_vector_after_supersession(tmp_path):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        pair, old_ref = _two(core, tmp_path)
        third = add_third(core.storage, tmp_path)
        new_ref = initial.request_initial_group_publication(core.storage, third, now=101)
        assert new_ref is not None
        bridge = core.notice_work._pump._bridge
        assert bridge.submit(old_ref).result(timeout=10) == "stale"
        old = journal.read_job(core.storage, old_ref)
        with core.storage._lock, core.storage._connect() as db:
            db.execute(
                "DELETE FROM origin_links WHERE source_reference_id IN ("
                "SELECT source_reference_id FROM source_references WHERE provider_id='seller-c')"
            )
        renewed = initial.request_initial_group_publication(core.storage, pair[0], now=102)
        assert renewed.key == old_ref.key and renewed.generation == old_ref.generation + 1
        job = journal.read_job(core.storage, renewed)
        assert job.state == "pending" and job.spec == old.spec and job.attempts == 0
        assert initial.request_initial_group_publication(core.storage, pair[1], now=103) == renewed
        assert bridge.submit(old_ref).result(timeout=10) == "obsolete"
        assert bridge.submit(renewed).result(timeout=10) == "current"
