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
from cifereca.core import numilog_search_canonical as search
from cifereca.core import numilog_materialization_checkpoint as legacy_receipts


ROOT = Path(__file__).resolve().parents[1]


def _src(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_legacy_direct_module_physically_removed_and_core_uses_v2():
    assert not (ROOT / "src/cifereca/core/numilog_direct_acquisition.py").exists()
    services = _src("src/cifereca/services.py")
    assert "from .core.numilog_direct_acquisition import" not in services
    assert "from .core.numilog_search_canonical import obtain" in services
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


def test_explicit_search_requires_one_right_and_guarded_v2(monkeypatch):
    item = _item()
    session = NS(downloadable_selected_delivery_candidate_guarded=lambda **_k: None)
    monkeypatch.setattr(search, "_scoped_record_id", lambda *_a: "exact")
    monkeypatch.setattr(search, "has_complete_delivery_abi", lambda *_a: True)
    monkeypatch.setattr(
        search, "require_source",
        lambda *_a, **_kw: (NS(identity_id="owner"), session, "borrower"),
    )
    evidence = {"entitlements": [{
        "entitlement_id": "owned-right", "identity_id": "owner",
        "credential_id": "card", "account_id": "borrower", "item_id": "edition",
        "entitlement_type": "owned", "state": "active",
        "loan_id": None, "expires_at": None,
        "metadata": {"source": "numilog.authenticated_library"},
    }], "acquisitions": [], "placement_conflict": False}
    service = NS(storage=NS(
        numilog_purchase_preflight_evidence=lambda **_kw: evidence,
    ))
    from cifereca.core import numilog_owned_download as owned
    target = NS(
        session=session, account_id="borrower", credential_id="card",
        item_id="edition",
    )
    monkeypatch.setattr(owned, "_resolve", lambda *_a: (target, item))
    monkeypatch.setattr(owned, "obtain", lambda *_a: "canonical-result")
    assert search.obtain(service, item) == "canonical-result"
    for changed in (
        {"acquisitions": [{"acquisition_id": "legacy"}]},
        {"entitlements": []},
        {"placement_conflict": True},
    ):
        altered = dict(evidence, **changed)
        service.storage.numilog_purchase_preflight_evidence = lambda **_kw: altered
        with pytest.raises(ProviderConfigurationError, match="numilog_canonical"):
            search.obtain(service, item)
