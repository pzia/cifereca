"""#6366: recorded supplier HTML -> durable C -> actual Search controller.

The HTML is an observed Numilog fixture; only HTTP transport is substituted.
The second source is a synthetic, owner-scoped session held by an explicit
event. Production worker/parallel orchestration and GLib delivery callbacks
execute; the GTK delivery queue is drained by the test's main thread.
No timing-based performance claim, live supplier or N4 pixels.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from threading import Event, get_ident

from cifereca.core.notice_c_public_search import PublicNoticeCPage
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.gui.query_model import QueryState, progressive_online_search
from tests.test_core043_progress_cache_stats import _FakeSession, _service
from tests.test_search_controller_state import _host


PLUGIN = Path(__file__).resolve().parents[1] / "src/plugins/provider-numilog"
HTML = PLUGIN / "tests/fixtures/observed_search-loup-0.html"
SOURCE = (
    "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
    "?mode=brSimple&query=loup"
)


def test_observed_provider_c_first_and_fast_batch_do_not_wait_for_slow(
    tmp_path, monkeypatch,
):
    monkeypatch.syspath_prepend(str(PLUGIN))
    import cifereca_numilog_public_provider as numilog
    from cifereca_numilog_public import parse_public_search

    provider_calls = []
    dispatcher = get_ident()

    def recorded_http(*args, **kwargs):
        provider_calls.append(get_ident())
        # Actual parse on the real supplier worker, not a prebuilt DTO.
        return parse_public_search(HTML.read_text(encoding="utf-8"), SOURCE)

    monkeypatch.setattr(numilog, "fetch_public_search", recorded_http)
    service = _service(tmp_path)
    fast = numilog.NumilogPublicSession(
        credential_id="__public__", label="Numilog public",
        credentials={}, options={},
    )
    fast.connect()
    entered_slow, release_slow = Event(), Event()

    class Slow(_FakeSession):
        def search(self, query, scope, *, field="all", content_types=()):
            entered_slow.set()
            if not release_slow.wait(30):
                raise AssertionError("slow fixture was not released")
            return super().search(
                query, scope, field=field, content_types=content_types,
            )

    slow = Slow("slow")
    try:
        # Seed from actual provider session/response and Core ingestion.
        service._sessions = {fast.provider_id: fast}
        seeded = service.search("loup", provider_id=fast.provider_id, force=True)
        assert len(seeded) == 1 and len(provider_calls) == 1
        doc = seeded[0]._core_notice_canonical_cover_document_ref
        assert read_consolidated_notice_cover(service.storage, doc) is not None

        # No legacy result cache or parsed-page reuse can manufacture C-first.
        service.storage.cache_delete_namespace("search")
        fast._public_cache.clear()
        before_catalog = service.storage.catalog_entries()
        from cifereca.core import notice_cover_repository
        original_writer = notice_cover_repository.persist_notice_cover_many
        writes = []

        def counted_writer(storage, observations, **kwargs):
            writes.append(len(observations))
            return original_writer(storage, observations, **kwargs)

        monkeypatch.setattr(notice_cover_repository, "persist_notice_cover_many", counted_writer)
        service._sessions = {fast.provider_id: fast, slow.provider_id: slow}
        host = _host()
        host.service = service
        host.search_controls.state = lambda: QueryState("loup")
        host.online.side_effect = progressive_online_search
        deliveries = Queue()
        env = host.run_search.__func__.__globals__

        def idle_add(callback, *args):
            assert get_ident() != dispatcher
            deliveries.put((callback, args))
            return 1

        monkeypatch.setattr(env["GLib"], "idle_add", idle_add)
        host.run_search(True)
        request = host.requests[-1]
        assert host._populate_search.call_count == 0

        # Explicit TaskRunner/GLib boundary: the C descriptor must already
        # be queued ahead of any provider result, even with fast completion.
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(request.work)
            try:
                callback, args = deliveries.get(timeout=15)
                assert isinstance(args[0], PublicNoticeCPage)
                assert callback(*args) is False
                known = list(host._search_local_objects.values())
                assert len(known) == 1
                assert known[0]["_cover_document_ref"] == doc
                assert known[0]["source"] == "notice_c_public"
                assert known[0]["thumbnail_url"] is None
                assert known[0]["local"] is False
                assert not known[0].get("download_url")
                assert not known[0].get("placement_id")
                assert host._remote_search_results == []

                assert entered_slow.wait(15)
                callback, args = deliveries.get(timeout=15)
                fast_batch = args[0]
                assert fast_batch.provider_id == fast.provider_id
                assert fast_batch.results
                assert callback(*args) is False
                assert not release_slow.is_set() and not future.done()
                assert len(provider_calls) == 2
                assert provider_calls[-1] != dispatcher
                assert len(host._remote_search_results) == 1
                assert host._remote_search_results[0]._core_notice_canonical_cover_document_ref == doc
                assert host._populate_search.call_count >= 2
                assert writes == [1]
                # Early C is not an early per-profile catalogue/right mirror.
                assert service.storage.catalog_entries() == before_catalog
            finally:
                release_slow.set()

            result = future.result(timeout=15)
            summary, _rows = result
            assert not summary.cancelled and summary.providers_completed == 2
            assert summary.providers_failed == 0
            callback, args = deliveries.get(timeout=15)
            assert args[0].provider_id == slow.provider_id
            assert callback(*args) is False
            request.done(result)
            assert {r.source_provider for r in host._remote_search_results} == {
                fast.provider_id, slow.provider_id,
            }

        assert len(provider_calls) == 2 and writes == [1]
        assert read_consolidated_notice_cover(service.storage, doc).epoch == 1
        assert service.storage.entitlements() == []
        assert service.storage.acquisitions() == []
    finally:
        release_slow.set()
        service.close()
        fast.close()


def test_progressive_two_public_sources_late_join_without_second_source_write(tmp_path, monkeypatch):
    from cifereca.core import notice_cover_repository, search_orchestration
    from cifereca.core.notice_c_known_projection import matching_live_public_canonical_ref
    from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
    from tests.test_core_p0_6366_origin_attested_public_c import _origins
    from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row

    service = _service(tmp_path)
    release = Event()

    class Public(_FakeSession):
        def __init__(self, provider, record, color):
            super().__init__(provider)
            self.credential_id = "__public__"
            self.credentials = {}
            self._accounts = []
            self.record, self.color = record, color

        def search(self, query, scope, *, field="all", content_types=()):
            if self.provider_id == "seller-b" and not release.wait(20):
                raise AssertionError("second fixture not released")
            row = public_row(
                id=self.record, record_id=self.record, source_provider=self.provider_id,
                title="Livre commun", thumbnail_url=f"https://covers.example.org/{self.color}.png",
            )
            return search_orchestration._public_rows_from_response(
                ProviderSearchResponse([row], public_notices=(PublicSearchNotice(row),)),
                self,
            )

    writes = []
    original = notice_cover_repository.persist_notice_cover_many
    def counted(storage, observations, **kwargs):
        writes.append(len(observations))
        return original(storage, observations, **kwargs)

    monkeypatch.setattr(notice_cover_repository, "persist_notice_cover_many", counted)
    try:
        _origins(service.storage, tmp_path)
        service._sessions = {
            "seller-a": Public("seller-a", "edition-a", "red"),
            "seller-b": Public("seller-b", "edition-b", "blue"),
        }
        first_refs = []
        batches = []
        def received(batch):
            batches.append(batch.provider_id)
            if batch.provider_id == "seller-a":
                row = batch.results[0]
                first_refs.append(getattr(row, "_core_notice_canonical_cover_document_ref", None))
                release.set()

        summary = service.search_progressive("Livre commun", force=True, on_batch=received)
        assert not summary.cancelled and summary.providers_failed == 0
        assert batches == ["seller-a", "seller-b"]
        assert first_refs and first_refs[0] is not None
        assert len(service.last_search) == 2
        assert writes == [1, 1]  # One write per provider, none at final reconciliation.
        refs = [matching_live_public_canonical_ref(row, identity_id="alice")
                for row in service.last_search]
        assert refs[0] is not None and refs[0] == refs[1] != first_refs[0]
        stored = read_consolidated_notice_cover(service.storage, refs[0])
        assert len(stored.sources) == 2
        assert service.storage.entitlements() == []
    finally:
        release.set()
        service.close()
