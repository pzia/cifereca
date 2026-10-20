"""#1916: a supplier or a legacy cache cannot impersonate Core's stale fallback."""
from __future__ import annotations

from dataclasses import asdict, replace

from cifereca.core.search_orchestration import _provider_search_row
from tests.test_core043_progress_cache_stats import _FakeSession, _service


def test_sanitizer_does_not_mutate_a_provider_owned_row():
    original = _FakeSession("public").search("Progressive Book", "all")[0]
    hostile = replace(original, metadata={
        "search_provenance": "known_catalog_stale", "publisher": "Public",
    })
    cleaned = _provider_search_row(hostile)
    assert cleaned is not hostile
    assert cleaned.metadata == {"publisher": "Public"}
    assert hostile.metadata["search_provenance"] == "known_catalog_stale"
    assert _provider_search_row(cleaned) is cleaned


def test_live_provider_cannot_forge_offline_label_in_any_search_path(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        session = _FakeSession("public")
        original_search = session.search
        original_rows = []

        def forged(*args, **kwargs):
            row = original_search(*args, **kwargs)[0]
            fake = replace(row, metadata={
                **(row.metadata or {}),
                "search_provenance": "known_catalog_stale",
                "publisher": "Public",
            })
            original_rows.append(fake)
            return [fake]

        session.search = forged
        service._sessions = {"public": session}
        monkeypatch.setattr(service, "_mirror_remote_results", lambda *a, **kw: 0)

        seen = []
        service.search_progressive("Progressive Book", force=True, on_batch=seen.append)
        assert len(seen) == 1
        assert seen[0].result_status == "live"
        assert seen[0].results[0].metadata.get("publisher") == "Public"
        assert "search_provenance" not in seen[0].results[0].metadata

        cached = []
        service.search_progressive("Progressive Book", on_batch=cached.append)
        assert len(cached) == 1 and cached[0].result_status == "cached"
        assert "search_provenance" not in cached[0].results[0].metadata

        rows = service.search("Progressive Book", force=True)
        assert len(rows) == 1
        assert "search_provenance" not in rows[0].metadata
        assert original_rows
        assert all(row.metadata["search_provenance"] == "known_catalog_stale"
                   for row in original_rows)
    finally:
        service.close()


def test_old_cache_cannot_resurrect_supplied_stale_marker(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        session = _FakeSession("public")
        row = session.search("Progressive Book", "all")[0]
        stale = replace(row, metadata={"search_provenance": "known_catalog_stale"})
        service._sessions = {"public": session}
        def no_network(*args, **kwargs):
            raise AssertionError("a cache hit must not call the source")
        session.search = no_network
        monkeypatch.setattr(
            service.storage, "cache_get", lambda *_a, **_kw: [asdict(stale)],
        )
        seen = []
        service.search_progressive("Progressive Book", on_batch=seen.append)
        assert len(seen) == 1 and seen[0].result_status == "cached"
        assert "search_provenance" not in seen[0].results[0].metadata
        rows = service.search("Progressive Book")
        assert len(rows) == 1
        assert "search_provenance" not in rows[0].metadata
    finally:
        service.close()
