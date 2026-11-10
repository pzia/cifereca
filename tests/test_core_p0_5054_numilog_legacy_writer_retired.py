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


@pytest.mark.parametrize("mode", ("on_start", "manual", "", "unexpected"))
def test_old_startup_optin_cannot_issue_new_consent(mode):
    from cifereca.core.numilog_materialization_consent import prepare, confirm
    assert prepare(NS(), "sync:test").ok is False
    assert confirm(NS(), "sync:test", "old-token", mode=mode).ok is False


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


def test_exact_owned_search_delegates_to_same_canonical_library_target(monkeypatch):
    item = _item()
    account = Account(provider_id="numilog", credential_id="card",
                      unique_id="borrower", identity_id="owner")
    right = {
        "entitlement_id": "owned-right", "identity_id": "owner",
        "credential_id": "card", "account_id": "borrower",
        "item_id": "edition", "entitlement_type": "owned",
        "state": "active", "loan_id": None, "expires_at": None,
    }
    target = NS(provider_id="numilog", identity_id="owner",
                item_id="edition")
    store = NS(
        provider_scope_account_bound=lambda **_kw: True,
        entitlements=lambda **_kw: [right],
    )
    service = NS(
        current_identity=NS(identity_id="owner"),
        accounts=[account], storage=store,
    )
    from cifereca.core import owned_library_download as owned
    selections = []
    monkeypatch.setattr(owned, "owned_library_download_target",
                        lambda _service, entry: selections.append(entry) or target)
    monkeypatch.setattr(owned, "obtain_owned_library_item",
                        lambda _service, selected: (
                            "canonical-result" if selected is target else
                            pytest.fail("retargeted owned action")
                        ))
    assert search.obtain_selected_owned(service, item) == "canonical-result"
    assert len(selections) == 1
    assert selections[0]["possessions"][0]["entitlement_id"] == "owned-right"

    store.entitlements = lambda **_kw: [right, {**right, "entitlement_id": "other"}]
    with pytest.raises(ProviderConfigurationError, match="canonical_owned"):
        search.obtain_selected_owned(service, item)
    store.entitlements = lambda **_kw: [right]
    target.operation = "resume_index"
    with pytest.raises(ProviderConfigurationError, match="canonical_owned"):
        search.obtain_selected_owned(service, item)
