"""P0 #5054: no Numilog v1 new GET/writer remains reachable from Core.

The historical receipt reader is intentionally kept for offline-only local
reconciliation; a private manifest, on_start flag, or old link never grants
another automatic supplier request.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account, SearchResult
from cifereca.providers import ProviderConfigurationError
from cifereca.core import numilog_materialization_startup as startup
from cifereca.core import selected_owned_search as search
from cifereca.core import numilog_materialization_checkpoint as legacy_receipts


ROOT = Path(__file__).resolve().parents[1]


def _src(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_legacy_direct_module_physically_removed_and_core_uses_v2():
    assert not (ROOT / "src/cifereca/core/numilog_direct_acquisition.py").exists()
    services = _src("src/cifereca/services.py")
    assert "from .core.numilog_direct_acquisition import" not in services
    assert "from .core.selected_owned_search import obtain_selected_owned" in services
    assert "numilog_canonical_explicit_action_required" in services


def test_historical_numilog_receipts_have_no_delivery_writer():
    source = _src("src/cifereca/core/numilog_materialization_checkpoint.py")
    methods = {n.name for n in ast.parse(source).body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "record" not in methods
    assert "_record" not in methods
    assert {"index_delivered", "resume", "_checked_receipt", "target_for_entry"} <= methods
    assert "download_direct(" not in source
    assert "rights_selected_delivery_candidate" not in source


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


def test_historical_startup_is_passive_and_never_performs_supplier_io():
    session = NS(
        library=lambda: pytest.fail("legacy private library must never run"),
        download_direct=lambda *_a: pytest.fail("legacy GET must never run"),
    )
    account = Account(
        provider_id="numilog", credential_id="card",
        unique_id="borrower", identity_id="owner",
    )
    broker = NS(
        service=NS(accounts=[account], storage=NS(
            metadata_get=lambda key: "on_start:v1",
        )),
        _source_id=lambda *_a: "sync:" + "a" * 24,
    )
    assert startup.eligible_source_ids(broker, "owner") == ()
    status = startup.execute(broker, "owner", "startup")
    assert status.ok is False
    assert status.facts["status"] == "migration_required"
    assert status.facts["acquisition_authorized"] is False
    assert status.changed == 0
    assert session.library is not None


def test_retired_startup_with_no_consent_is_inert():
    account = Account(
        provider_id="numilog", credential_id="card",
        unique_id="borrower", identity_id="owner",
    )
    broker = NS(
        service=NS(accounts=[account], storage=NS(metadata_get=lambda *_a: None)),
        _source_id=lambda *_a: "sync:" + "a" * 24,
    )
    result = startup.execute(broker, "owner", "startup")
    assert result.ok and result.facts["status"] == "not_due"


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
