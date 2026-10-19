"""#1916: truthful, bounded, identity-scoped pagination of stale public catalog."""
from __future__ import annotations

import time
import pytest

from cifereca.core.known_catalog_fallback import (
    KnownPublicFallbackPage, known_public_fallback, known_public_fallback_page,
)
from cifereca.core import known_catalog_fallback as fallback
from tests.test_core043_progress_cache_stats import _service
from tests.test_core_p1_1916_stale_public_fallback import _known


def _page(service, *, identity="alice", provider="good", page=0, page_size=50,
          query="maritime", field="all", scope="digital", types=("epub",)):
    return known_public_fallback_page(
        service.storage, provider_id=provider, identity_id=identity,
        query=query, field=field, scope=scope, content_types=types,
        page=page, page_size=page_size,
    )


def test_three_pages_no_duplicate_source_or_identity_and_legacy_first_page(tmp_path):
    service = _service(tmp_path)
    try:
        for i in range(113):
            _known(service, key=f"item{i:03d}", title="Aventure maritime")
        for i in range(4):
            _known(service, identity="bob", key=f"foreign{i}")
            _known(service, provider="other", key=f"foreign{i}")
        p0 = _page(service)
        p1 = _page(service, page=1)
        p2 = _page(service, page=2)
        assert all(isinstance(x, KnownPublicFallbackPage) for x in (p0, p1, p2))
        assert [len(p.results) for p in (p0, p1, p2)] == [50, 50, 13]
        assert all(p.matched_count == 113 for p in (p0, p1, p2))
        assert [p.has_more for p in (p0, p1, p2)] == [True, True, False]
        assert all(p.status == "known_catalog_stale" for p in (p0, p1, p2))
        ids = [r.record_id for p in (p0, p1, p2) for r in p.results]
        assert len(ids) == len(set(ids)) == 113
        assert all(r.source_provider == "good" for p in (p0, p1, p2) for r in p.results)
        assert all(r.metadata["search_provenance"] == "known_catalog_stale"
                   for p in (p0, p1, p2) for r in p.results)
        assert all(r.owned is False and r.downloadable is False
                   and r.download_url is None for p in (p0, p1, p2)
                   for r in p.results)
        assert known_public_fallback(
            service.storage, provider_id="good", identity_id="alice",
            query="maritime", scope="digital", field="all",
            content_types=("epub",),
        ) == p0.results
        assert _page(service, page=3).status == "page_out_of_range"
        assert _page(service, page=3).matched_count == 113
        assert _page(service, page=3).results == ()
        assert _page(service, identity="bob").matched_count == 4
    finally:
        service.close()


@pytest.mark.parametrize("page,size", [
    (None, 50), (True, 50), (-1, 50), ("0", 50), (10001, 1),
    (0, False), (0, 0), (0, -1), (0, 51), (0, "2"),
    (200, 50),
])
def test_bad_page_request_cannot_bypass_bounded_search(tmp_path, page, size):
    service = _service(tmp_path)
    try:
        _known(service)
        p = _page(service, page=page, page_size=size)
        assert p.status == "invalid_page"
        assert not p.results
        assert p.matched_count == 0
        assert p.has_more is False
    finally:
        service.close()


def test_source_over_budget_is_not_mistaken_for_zero_matches(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        monkeypatch.setattr(fallback, "_MAX_CATALOG_ROWS", 3)
        for i in range(4):
            _known(service, key=f"item{i}")
        p = _page(service, page_size=2)
        assert p.status == "scope_budget_exceeded"
        assert p.results == ()
        assert p.matched_count == 0
        assert p.has_more is False
        # Existing UI call keeps its longstanding conservative empty result.
        assert known_public_fallback(
            service.storage, provider_id="good", identity_id="alice",
            query="maritime", scope="digital", field="all", content_types=(),
        ) == ()
        for i in range(3):
            _known(service, provider="other", key=f"other-{i}")
        assert _page(service, page_size=2).status == "scope_budget_exceeded"
    finally:
        service.close()


def test_scope_field_formats_and_age_evidence_filters_precede_pagination(tmp_path):
    service = _service(tmp_path)
    try:
        _known(service, key="first", title="Aventure maritime")
        _known(service, key="second", title="Romans terrestres")
        _known(service, key="old", mirrored=int(time.time()) - 100 * 86400)
        _known(service, key="private", collection="personal_library")
        _known(service, key="owned", state="remote_owned")
        title = _page(service, page_size=1, query="maritime", field="title")
        assert title.status == "known_catalog_stale"
        assert title.matched_count == 1 and not title.has_more
        assert [r.record_id for r in title.results] == ["public-first"]
        assert _page(service, query="nothere").status == "no_known_results"
        assert _page(service, types=("pdf",)).status == "no_known_results"
        assert _page(service, identity="missing").status == "no_known_results"
        assert _page(service, identity=None).status == "unavailable"
    finally:
        service.close()


def test_explicit_small_page_size_and_stable_repeated_page_without_network(tmp_path):
    service = _service(tmp_path)
    try:
        for i in range(9):
            _known(service, key=f"same{i}")
        service._sessions = {
            "good": object(), "not-authorized": object(),
        }
        a = _page(service, page=2, page_size=3)
        b = _page(service, page=2, page_size=3)
        assert a == b
        assert len(a.results) == 3
        assert a.matched_count == 9
        assert a.has_more is False
        assert all("PRIVATE-CREDENTIAL" not in repr(row) for row in a.results)
    finally:
        service.close()
