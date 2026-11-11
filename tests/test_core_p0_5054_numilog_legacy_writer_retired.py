"""P0 #5054: no Numilog v1 new GET/writer remains reachable from Core.

The v1 receipt reader and its writer are retired. No private manifest,
on_start flag or old link grants another automatic supplier request.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account, SearchResult
from cifereca.providers import ProviderConfigurationError
from cifereca.core import selected_owned_search as search


ROOT = Path(__file__).resolve().parents[1]


def _src(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_legacy_direct_module_physically_removed_and_core_uses_v2():
    assert not (ROOT / "src/cifereca/core/numilog_direct_acquisition.py").exists()
    services = _src("src/cifereca/services.py")
    assert "from .core.numilog_direct_acquisition import" not in services
    assert "from .core.selected_owned_search import obtain_selected_owned" in services
    assert "numilog_canonical_explicit_action_required" in services


def test_historical_numilog_receipt_engine_physically_removed():
    assert not (ROOT / "src/cifereca/core/numilog_materialization_checkpoint.py").exists()
    source = _src("src/cifereca/core/owned_library_download.py")
    assert "numilog_materialization_checkpoint" not in source


def test_real_gtk_book_detail_does_not_recreate_retired_v1_receipt():
    # The GTK probe must exercise the targeted canonical action, not seed a
    # synthetic receipt through the v1 writer removed by #5054.
    source = _src("e2e/book_detail_probe.py")
    module = ast.parse(source)
    names = {node.name for node in ast.walk(module)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "begin_numilog_index_resume" not in names
    assert "from cifereca.core.numilog_materialization_checkpoint import record" not in source
    # Retired synthetic direct_download cannot be a positive GTK path.
    assert 'begin_owned_book_action("numilog")' not in source
    assert "P0_1347_owned_library_XTEST_exact_single_target" in source


def test_retired_v1_startup_has_no_core_dispatch():
    assert not (ROOT / "src/cifereca/core/numilog_materialization_startup.py").exists()
    broker = _src("src/cifereca/brokers.py")
    assert "synchronize_scheduled_numilog_materialization" not in broker



def _item():
    return SearchResult(
        id="edition", record_id="edition", docbase="NUMILOG", medium="digital",
        title="Fiction", author=None, type="Livre numérique possédé",
        format=None, publisher=None, published_at=None,
        description=None, thumbnail_url=None, friendly_url=None,
        scenario="NUMILOG_LIBRARY", source_provider="numilog",
        source_credential="card", source_collection="library",
        owned=True, downloadable=True, content_type="ebook", metadata={},
    )


def test_numilog_search_selects_exact_live_offer_without_entitlement(monkeypatch):
    item = _item()
    target = NS(provider_id="numilog", identity_id="owner", item_id="edition")
    calls = []
    from cifereca.core import selected_source_offer
    monkeypatch.setattr(selected_source_offer, "select_source_offer_target",
                        lambda _service, **kw: calls.append(("select", kw)) or target)
    monkeypatch.setattr(selected_source_offer, "obtain_source_offer",
                        lambda _service, selected: (
                            "canonical-result" if selected is target
                            else pytest.fail("foreign account target")
                        ))
    class Store:
        def entitlements(self, **_kw):
            pytest.fail("historical rights forbidden")
    service = NS(current_identity=NS(identity_id="owner"), storage=Store())
    assert search.obtain_selected_owned(service, item) == "canonical-result"
    assert calls == [("select", {
        "provider_id": "numilog", "credential_id": "card",
        "item_id": "edition",
    })]


@pytest.mark.parametrize("invalid", [
    {"source_collection": "public_catalog"},
    {"download_url": "https://example.invalid/signed?token=secret"},
    {"downloadable": False}, {"owned": False},
    {"id": "forged"}, {"source_credential": ""},
])
def test_unproven_search_never_requests_an_offer(monkeypatch, invalid):
    from dataclasses import replace
    from cifereca.core import selected_source_offer
    monkeypatch.setattr(selected_source_offer, "select_source_offer_target",
                        lambda *_a, **_kw: pytest.fail("no provider request"))
    with pytest.raises(ProviderConfigurationError, match="canonical_owned"):
        search.obtain_selected_owned(NS(), replace(_item(), **invalid))
