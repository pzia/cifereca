"""An authorized source worker must retain a valid first self-C job after revision."""
import pytest

from cifereca.core import notice_c_cover_index as index
from cifereca.core import notice_c_fetch_publication as fetch
from cifereca.core import notice_c_initial_publication as initial
from cifereca.core import notice_c_job_journal as journal
from cifereca.core import notice_c_reparse_execution as reparse
from cifereca.core.notice_c_fetch_authority import PublicFetchAuthority
from cifereca.core.notice_c_fetch_execution import PublicFetchExecution, PublicFetchPort
from cifereca.core.notice_c_work_pump import PublicNoticeWorkPump
from cifereca.core.notice_cover_repository import read_notice_cover
from cifereca.storage import Storage
from tests.test_core_p0_4982_fetch_authority import (
    Authority, TRANSPORT, IMPLEMENTATION, request as request_fetch,
)
from tests.test_core_p0_4982_fetch_execution import Transport, broker
from tests.test_core_p0_4982_raw_input_receipt import public, record
from tests.test_core_p0_4982_reparse_requests import TOKEN, request as request_reparse
from tests.test_core_p0_4982_reparse_execution import FakePort


@pytest.mark.parametrize("mode", ["fetch", "reparse"])
@pytest.mark.parametrize("refused", [False, True])
def test_source_worker_atomically_renews_first_self_c_intention(tmp_path, monkeypatch, mode, refused):
    path = tmp_path / "source.sqlite"
    db = Storage(path)
    tasks = broker()
    authority = None
    try:
        source = public(db)
        document = index.canonical_cover_document_ref(source.observation.key.edition_id)
        old_ref = initial.request_initial_publication(db, source, now=100)
        old = journal.read_job(db, old_ref)
        assert index.read_consolidated_notice_cover(db, document) is None
        if mode == "fetch":
            owner = Authority()
            authority = PublicFetchAuthority(db, clock=lambda: 100)
            ref = request_fetch(authority, source, owner)
            adapter = Transport(source, owner, "changed")
            adapter.authority = authority
            options = {"fetch_execution": PublicFetchExecution(authority, (PublicFetchPort(
                source.observation.key.provider_id, TRANSPORT, IMPLEMENTATION, adapter.reserve,
            ),))}
            module, title = fetch, "Titre corrigé"
        else:
            receipt = record(db, source, producer_token=TOKEN)
            ref = request_reparse(db, source, receipt)
            adapter = FakePort(source, receipt)
            options = {"replay_registry": adapter.registry()}
            module, title = reparse, "Corrigé par le parseur"
        if refused:
            def refuse_after_write(sql, persisted, *, now):
                assert initial.request_initial_publication_txn(sql, persisted, now=now) is not None
                raise ValueError("simulated repair journal refusal")
            monkeypatch.setattr(module, "request_initial_publication_txn", refuse_after_write)
        pump = PublicNoticeWorkPump(db, tasks, clock=lambda: 100, **options)
        # Exercise the adversarial order, before the queued initial self-C.
        outcome = pump._bridge.submit(ref).result(timeout=10)
        assert adapter.calls == (["reserve", "execute", "release"] if mode == "fetch"
                                 else ["read", "parse", "hold"])
        assert index.read_consolidated_notice_cover(db, document) is None
        if refused:
            assert outcome == ("error" if mode == "fetch" else "transient_failure")
            assert read_notice_cover(db, source.document) == source
            assert journal.read_job(db, old_ref) == old
            return
        assert outcome == "current"
        assert journal.read_job(db, ref).state == "current"
        latest_source = read_notice_cover(db, source.document)
        assert latest_source.epoch == source.epoch + 1
        with db._lock, db._connect() as sql:
            renewed = journal._load(sql, old_ref.key)
        assert renewed.ref.generation == old_ref.generation + 1
        assert renewed.spec.input_digest != old.spec.input_digest
        assert renewed.state == "pending"
        with pytest.raises(ValueError):
            journal.read_job(db, old_ref)
    finally:
        tasks.close()
        if authority is not None:
            authority.close()
        db.close()

    # Fresh worker without a parser registry or transport grant.
    db = Storage(path)
    tasks = broker()
    try:
        pump = PublicNoticeWorkPump(db, tasks, clock=lambda: 100)
        assert pump._bridge.submit(renewed.ref).result(timeout=10) == "current"
        canonical = index.read_consolidated_notice_cover(db, document)
        assert canonical.sources[0].public_bibliography.title == title
        assert canonical.sources[0].source_epoch == latest_source.epoch
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        tasks.close()
        db.close()
