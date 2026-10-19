"""#2361: enabled EbooksGratuits must actually be called in federated search.

This is independent from historic local notices, OPDS availability and
downloadability; no real HTTP request or source credentials are needed.
"""
from __future__ import annotations

from unittest.mock import Mock

from cifereca.gui.query_model import QueryState, progressive_online_search
from tests.test_core043_progress_cache_stats import _service, _FakeSession


def test_live_source_selector_and_federated_work_agree(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        free = _FakeSession("ebooksgratuits")
        disabled = _FakeSession("baen")
        free.search = Mock(wraps=free.search)
        disabled.search = Mock(wraps=disabled.search)
        service._sessions = {"public:free": free, "public:disabled": disabled}
        monkeypatch.setattr(service, "_provider_enabled_for_identity",
                            lambda provider_id: provider_id == "ebooksgratuits")
        monkeypatch.setattr(service, "_mirror_remote_results",
                            lambda rows, **kw: len(rows))
        service.storage.catalog_entry_put({
            "catalog_entry_id": "historical-baen",
            "title": "Baen old catalog notice",
            "author": "Fixture",
            "provider_id": "baen",
            "identity_id": "alice",
            "item_id": "old-baen",
            "local_state": "remote_catalog",
            "media_format": "epub",
            "metadata": {},
        })
        assert dict(service.searchable_providers()) == {
            "ebooksgratuits": "Ebooksgratuits",
        }
        assert any(x.get("provider_id") == "baen" for x in service.catalog_entries())
        batches = []
        summary = progressive_online_search(
            service, QueryState(text="Progressive Book",
                                providers=("ebooksgratuits",)),
            batches.append,
        )
        assert summary.providers_total == 1
        assert summary.providers_failed == 0
        assert {b.provider_id for b in batches} == {"ebooksgratuits"}
        assert all(b.cached is False for b in batches)
        assert batches[0].result_status == "live"
        assert len(batches[0].results) == 1
        assert batches[0].results[0].source_provider == "ebooksgratuits"
        assert free.search.call_count > 0  # actual force=True provider work
        disabled.search.assert_not_called()

        # An obsolete selected Baen option cannot secretly search an inactive
        # source; its local historical record still exists.
        excluded = []
        stop = progressive_online_search(
            service, QueryState(text="Progressive Book",
                                providers=("baen",)), excluded.append,
        )
        assert stop.providers_total == 0 and excluded == []
        disabled.search.assert_not_called()
        assert any(x.get("provider_id") == "baen" for x in service.catalog_entries())
    finally:
        service.close()


def test_disconnected_ebooksgratuits_is_not_falsely_present_in_live_selector(tmp_path):
    service = _service(tmp_path)
    try:
        free = _FakeSession("ebooksgratuits")
        free.connected = False
        service._sessions = {"public:free": free}
        assert "ebooksgratuits" not in dict(service.searchable_providers())
        batches = []
        summary = progressive_online_search(
            service, QueryState(text="Progressive Book",
                                providers=("ebooksgratuits",)), batches.append,
        )
        assert summary.providers_total == 0 and batches == []
    finally:
        service.close()
