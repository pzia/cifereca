"""#6366: actual cache replay reuses one C snapshot, not one scan per DTO."""
from dataclasses import replace

from cifereca.core import search_orchestration
from cifereca.core.notice_c_cached_revision import matching_cached_public_canonical_ref
from tests.test_core_p0_6366_cached_public_notice_readthrough import _Public
from tests.test_core043_progress_cache_stats import _service


def test_actual_cached_rows_share_one_c_scan_and_keep_original_receipts(tmp_path, monkeypatch):
    service = _service(tmp_path)
    session = _Public()
    service._sessions = {session.provider_id: session}
    try:
        live = service.search("Progressive Book", provider_id=session.provider_id,
                              force=True)[0]
        ref = live._core_notice_canonical_cover_document_ref
        with service.storage._lock, service.storage._connect() as db:
            db.execute("DELETE FROM metadata WHERE key LIKE 'notice.c.observation.v1:%'")
        first, duplicate = replace(live), replace(live)
        absent = replace(live, id="absent", record_id="absent")
        wrong = replace(live, title="Different edition")
        private = replace(live, source_credential="private-session")
        connects = []
        connect = service.storage._connect

        def counted():
            connects.append(1)
            return connect()

        monkeypatch.setattr(service.storage, "_connect", counted)
        search_orchestration._stamp_cached_public_notice_refs(
            [first, absent, duplicate, wrong, private],
            identity_id="alice", storage=service.storage,
        )
        assert connects == [1]
        for row in (first, duplicate):
            assert matching_cached_public_canonical_ref(row, identity_id="alice") == ref
            assert matching_cached_public_canonical_ref(row, identity_id="bob") is None
        for row in (absent, wrong, private):
            assert matching_cached_public_canonical_ref(row, identity_id="alice") is None
            assert not hasattr(row, "_core_notice_canonical_cover_document_ref")
        assert matching_cached_public_canonical_ref(
            replace(first), identity_id="alice",
        ) is None
        assert session.calls == 1
    finally:
        service.close()
