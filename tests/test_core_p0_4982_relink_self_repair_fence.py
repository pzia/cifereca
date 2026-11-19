"""A queued strict relink survives an independently repaired overlapping self-C."""
import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core import notice_c_cover_index as index
from cifereca.core import notice_c_initial_publication as initial
from cifereca.core import notice_c_exact_recompute as exact
from cifereca.core import notice_c_origin_relink as relink
from cifereca.core import notice_c_initial_group_publication as recovery
from tests.test_core_p0_4982_initial_group_publication import _budget
from tests.test_core_p0_4982_notice_work_runtime import open_core
from tests.test_core_p0_4982_relink_inputs import group, add_third
from tests.test_core_p0_6366_origin_attested_public_c import witnessed, public_row
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist


def _setup(core, tmp_path, existing):
    document, pair = group(core.storage, tmp_path)
    third = add_third(core.storage, tmp_path)
    own = index.canonical_cover_document_ref(third.observation.key.edition_id)
    if existing:
        index.persist_exact_public_search_cover(core.storage, third)
        row = witnessed(public_row(
            source_provider="seller-c", record_id="edition-c",
            title="L'ouvrage commun", thumbnail_url="https://covers.example.org/third.jpg",
            description="Nouveau résumé",
        ))
        changed = persist(core.storage, row, at=101)
        assert changed.document == third.document and changed.epoch > third.epoch
        third = changed
        own_ref = exact.request_exact_public_recompute(
            core.storage, own, now=101, expected_generation=0,
        )
    else:
        own_ref = initial.request_initial_publication(core.storage, third, now=101)
    assert own_ref is not None
    return document, (*pair, third), own_ref


@pytest.mark.parametrize("existing", [False, True])
def test_exact_self_repair_refreshes_pending_relink_with_full_cas(tmp_path, existing):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        document, sources, own_ref = _setup(core, tmp_path, existing)
        before = index.read_consolidated_notice_cover(core.storage, document)
        previous_ref = recovery.request_search_group_repair(core.storage, sources[-1], now=101)
        previous = journal.read_job(core.storage, previous_ref)
        assert previous.spec.implementation_digest == relink.IMPLEMENTATION_DIGEST
        # Force the valid but adversarial execution order. Only real worker
        # jobs can publish; no test writes directly to the canonical index.
        bridge = core.notice_work._pump._bridge
        assert bridge.submit(own_ref).result(timeout=10) == "current"
        with core.storage._lock, core.storage._connect() as db:
            renewed = journal._load(db, previous_ref.key)
        assert renewed.state == "pending" and renewed.attempts == 0
        assert renewed.ref.generation == previous_ref.generation + 1
        assert renewed.spec.target == previous.spec.target
        assert renewed.spec.input_digest != previous.spec.input_digest
        assert renewed.due == previous.due
        with pytest.raises(ValueError):
            journal.read_job(core.storage, previous_ref)
        assert index.read_consolidated_notice_cover(core.storage, document) == before
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        bridge = core.notice_work._pump._bridge
        assert bridge.submit(renewed.ref).result(timeout=10) == "current"
        latest = index.read_consolidated_notice_cover(core.storage, document)
        assert {m.source_ref: m.source_epoch for m in latest.sources} == {
            s.document.source_key: s.epoch for s in sources
        }
        assert journal.read_job(core.storage, renewed.ref).state == "current"
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []


def test_self_repair_does_not_create_missing_relink_intent(tmp_path):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        document, sources, own_ref = _setup(core, tmp_path, False)
        assert core.notice_work._pump._bridge.submit(own_ref).result(timeout=10) == "current"
        with core.storage._lock, core.storage._connect() as db:
            keys = db.execute("SELECT key FROM metadata WHERE key>=? AND key<?",
                              (journal._PREFIX, journal._END)).fetchall()
        assert keys == [(journal._PREFIX + own_ref.key,)]
        assert len(index.read_consolidated_notice_cover(core.storage, document).sources) == 2


def test_self_repair_does_not_renew_revoked_group(tmp_path):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        document, sources, own_ref = _setup(core, tmp_path, False)
        ref = recovery.request_search_group_repair(core.storage, sources[-1], now=101)
        previous = journal.read_job(core.storage, ref)
        with core.storage._lock, core.storage._connect() as db:
            db.execute(
                "DELETE FROM origin_links WHERE source_reference_id IN ("
                "SELECT source_reference_id FROM source_references WHERE provider_id='seller-c')"
            )
        assert core.notice_work._pump._bridge.submit(own_ref).result(timeout=10) == "current"
        assert journal.read_job(core.storage, ref) == previous
        assert len(index.read_consolidated_notice_cover(core.storage, document).sources) == 2
