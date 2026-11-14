"""Supplier-neutral private-offer visibility is passive and never grants a GET."""
from dataclasses import replace
import hashlib
from types import SimpleNamespace as NS

import pytest

from cifereca.models import SearchResult
from cifereca.core.private_inventory_row_scope import selected_private_offer_hint


PROVIDER = "future-store"
CREDENTIAL = "credential-A"


def _row(**changes):
    digest = hashlib.sha256(CREDENTIAL.encode()).hexdigest()[:20]
    record = f"{PROVIDER}:{digest}:edition-1:{'a' * 16}"
    return replace(SearchResult(
        id=record, record_id=record, docbase="FUTURE",
        medium="digital", title="Synthetic title", author=None,
        type=None, format=None, publisher=None, published_at=None,
        description=None, thumbnail_url=None, friendly_url=None,
        scenario="PRIVATE_LEDGER", source_provider=PROVIDER,
        source_credential=CREDENTIAL, source_collection="library",
        owned=True, downloadable=True,
    ), **changes)


def _fixture(*, identity="alice", accounts=None, contract=None, cap=True):
    session = NS(
        selected_purchase_branch="purchases",
        private_inventory_row_contract=contract if contract is not None
        else ("library", "PRIVATE_LEDGER", "credential_scoped_record_v1"),
        capabilities=NS(direct_download=cap),
    )
    source_calls = []
    def forbidden(*_args):
        source_calls.append("provider")
        pytest.fail("UI visibility must not query a provider")
    return NS(
        _sessions={f"{PROVIDER}:{CREDENTIAL}": session},
        _session_key=lambda p, c: f"{p}:{c}",
        _session_for_source=forbidden,
        current_identity=NS(identity_id=identity),
        accounts=accounts if accounts is not None else [
            NS(provider_id=PROVIDER, credential_id=CREDENTIAL, identity_id="alice")
        ],
        source_calls=source_calls,
    )


def test_selected_private_provider_visible_without_supplier_request():
    service = _fixture()
    assert selected_private_offer_hint(service, _row(), identity_id="alice") is True
    assert service.source_calls == []


@pytest.mark.parametrize("changes", [
    {"source_collection": "catalogue"},
    {"source_collection": "public_catalog"},
    {"scenario": "PUBLIC"},
    {"owned": False},
    {"owned": 1},
    {"downloadable": False},
    {"downloadable": 1},
    {"download_url": "https://private.invalid/?token=secret"},
    {"medium": "physical"},
    {"id": "unbound"},
    {"record_id": "unscoped"},
    {"source_credential": "__public__"},
    {"source_credential": "foreign"},
    {"source_provider": "future-catalog"},
])
def test_private_hint_refuses_nonmatching_source_without_provider_call(changes):
    service = _fixture()
    assert selected_private_offer_hint(service, _row(**changes), identity_id="alice") is False
    assert service.source_calls == []


@pytest.mark.parametrize("identity,accounts", [
    ("bob", None), ("", None), ("alice", []),
    ("alice", [NS(provider_id=PROVIDER, credential_id=CREDENTIAL, identity_id="bob")]),
    ("alice", [NS(provider_id=PROVIDER, credential_id=CREDENTIAL, identity_id="alice"),
               NS(provider_id=PROVIDER, credential_id=CREDENTIAL, identity_id="bob")]),
])
def test_private_hint_requires_unique_selected_account(identity, accounts):
    service = _fixture(identity=identity, accounts=accounts)
    assert selected_private_offer_hint(service, _row(), identity_id=identity) is False


@pytest.mark.parametrize("contract", [
    ("library", "PRIVATE_LEDGER", "unknown"),
    ("library", "PUBLIC", "credential_scoped_record_v1"),
    ("library", "PRIVATE_LEDGER"),
    "INVALID",
])
def test_invalid_contract_fails_closed(contract):
    service = _fixture(contract=contract)
    assert selected_private_offer_hint(service, _row(), identity_id="alice") is False


def test_missing_session_private_row_cannot_enable_a_legacy_direct_get():
    service = _fixture()
    service._sessions.clear()
    assert selected_private_offer_hint(service, _row(), identity_id="alice") is False
    assert service.source_calls == []


def test_non_selected_public_offer_keeps_existing_legacy_visibility():
    service = _fixture()
    service._sessions.clear()
    item = _row(
        source_collection="public_catalog", owned=False,
        download_url="https://example.invalid/a.epub",
    )
    assert selected_private_offer_hint(service, item, identity_id="alice") is None


def test_private_capability_is_not_an_offer():
    assert selected_private_offer_hint(_fixture(cap=False), _row(), identity_id="alice") is False


def test_headless_private_offer_requires_explicit_identity_without_navigation_read():
    service = _fixture()

    class Headless:
        def __getattr__(self, name):
            if name == "current_identity":
                pytest.fail("Core private offer worker read GTK selection")
            return getattr(service, name)

    worker = Headless()
    assert selected_private_offer_hint(worker, _row()) is False
    assert selected_private_offer_hint(worker, _row(), identity_id=False) is False
    assert selected_private_offer_hint(worker, _row(), identity_id="bob") is False
    assert selected_private_offer_hint(worker, _row(), identity_id="alice") is True
    assert service.source_calls == []
