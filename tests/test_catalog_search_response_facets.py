from dataclasses import replace
from threading import Event, Thread

import pytest

from cifereca.plugin_api import (
    CatalogSearchFacet, CatalogSearchFacetValue, ProviderSearchResponse,
)
from tests.test_core043_progress_cache_stats import _FakeSession, _service


FACET = CatalogSearchFacet(
    "CATALOGUE", 0, "subject", "Sujet",
    (CatalogSearchFacetValue("Théâtre", 4, False),),
)


class FacetedSession(_FakeSession):
    def search_with_response_facets(self, query, scope, *, field, content_types):
        return ProviderSearchResponse(
            self.search(query, scope, field=field, content_types=content_types),
            (FACET, replace(FACET, scenario="EBOOKS", page=1, values=None)),
        )


def test_live_batch_scopes_descriptive_facets_to_source_and_does_not_cache_them(tmp_path):
    service = _service(tmp_path)
    try:
        service._sessions = {
            "faceted": FacetedSession("faceted"), "legacy": _FakeSession("legacy"),
        }
        live = {}
        service.search_progressive(
            "Progressive Book", force=True, on_batch=lambda batch: live.update({batch.provider_id: batch}),
        )
        assert live["faceted"].response_facets == (
            FACET, replace(FACET, scenario="EBOOKS", page=1, values=None),
        )
        assert FACET.filter_capability == "unverified"
        assert not live["legacy"].response_facets
        assert all("response_facets" not in row.metadata for row in service.last_search)

        cached = []
        service.search_progressive("Progressive Book", on_batch=cached.append)
        assert len(cached) == 2
        assert all(batch.cached and not batch.response_facets for batch in cached)
    finally:
        service.close()


def test_empty_response_keeps_facets_but_query_variants_never_replace_them(tmp_path):
    service = _service(tmp_path)
    session = FacetedSession("faceted")
    calls = []
    original_search = session.search

    def with_facets(query, _scope, **_options):
        calls.append(("faceted", query))
        return ProviderSearchResponse([], (FACET,))

    def legacy_search(query, scope, **options):
        calls.append(("legacy", query))
        return original_search(query, scope, **options)

    session.search_with_response_facets = with_facets
    session.search = legacy_search
    service._sessions = {"faceted": session}
    try:
        batches = []
        service.search_progressive("Exact Phrase", force=True, on_batch=batches.append)
        assert calls[0] == ("faceted", "Exact Phrase")
        assert all(method == "legacy" for method, _query in calls[1:])
        assert batches[0].results and batches[0].response_facets == (FACET,)

        batches.clear()
        service.search_progressive("Exact", force=True, on_batch=batches.append)
        assert not batches[0].results and batches[0].response_facets == (FACET,)
    finally:
        service.close()


@pytest.mark.parametrize("count", [True, -1, 2**63])
def test_malformed_facet_values_stay_unverified_without_dropping_notices(tmp_path, count):
    service = _service(tmp_path)
    session = FacetedSession("faceted")
    original = session.search_with_response_facets

    def malformed(*args, **kwargs):
        result = original(*args, **kwargs)
        value = replace(FACET.values[0], count=count)
        return replace(result, facets=(
            replace(FACET, values=(value,)), replace(FACET, page=2**31), object(),
        ))

    session.search_with_response_facets = malformed
    service._sessions = {"faceted": session}
    try:
        batches = []
        service.search_progressive("Progressive Book", force=True, on_batch=batches.append)
        assert batches[0].results
        assert batches[0].response_facets == (replace(FACET, values=None),)
    finally:
        service.close()


@pytest.mark.parametrize("partial", [True, False])
def test_batch_coverage_comes_from_the_same_typed_response(tmp_path, partial):
    service = _service(tmp_path)
    session = FacetedSession("faceted")
    session.last_search_warnings = [] if partial else ["Another request's warning"]
    session.search_with_response_facets = lambda *args, **kwargs: ProviderSearchResponse(
        [], (FACET,), partial=partial,
    )
    service._sessions = {"faceted": session}
    try:
        batches = []
        service.search_progressive("Exact", force=True, on_batch=batches.append)
        batch, = batches
        assert batch.response_facets == (FACET,)
        assert batch.result_status == ("partial" if partial else "live")
    finally:
        service.close()


def test_superseded_search_cannot_publish_facets_from_its_response(tmp_path):
    service = _service(tmp_path)
    session = FacetedSession("faceted")
    started = Event()
    release = Event()

    def with_facets(query, *_args, **_options):
        if query == "Old":
            started.set()
            assert release.wait(5)
        return ProviderSearchResponse([], (replace(FACET, label=query),))

    session.search_with_response_facets = with_facets
    service._sessions = {"faceted": session}
    old_batches = []
    old_summaries = []
    worker = Thread(target=lambda: old_summaries.append(
        service.search_progressive("Old", force=True, on_batch=old_batches.append)
    ))
    worker.start()
    try:
        assert started.wait(5)
        new_batches = []
        service.search_progressive("New", force=True, on_batch=new_batches.append)
        assert [facet.label for facet in new_batches[0].response_facets] == ["New"]
    finally:
        release.set()
        worker.join(5)
        service.close()
    assert not worker.is_alive()
    assert not old_batches and old_summaries[0].cancelled
