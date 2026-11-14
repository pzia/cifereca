"""#5975 / #5851: Core never infers a connection owner from navigation."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.identities import Identity
from cifereca.models import SearchResult, Loan
from cifereca.providers import ProviderConfigurationError
from cifereca.services import LibraryService


def _host(owner):
    service = LibraryService.__new__(LibraryService)
    service.current_identity = Identity("bob", "Selected GTK profile")
    service.identities = [
        Identity("alice", "Persisted owner"),
        Identity("bob", "Selected GTK profile"),
    ]
    calls = []
    service.storage = NS(
        identity_for_connection=lambda p, c: (
            calls.append((p, c)) or owner
        ),
    )
    return service, calls


def _item():
    return SearchResult(
        id="public", record_id="edition", docbase="public", medium="digital",
        title="Public item", author=None, type=None, format="epub",
        publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario="public",
        source_provider="synthetic", source_credential="card",
    )


@pytest.mark.parametrize("owner", [
    None, "", False, 123, [], {}, "deleted",
])
def test_exact_owner_resolver_refuses_missing_or_foreign_owner_even_if_gtk_selected(owner):
    service, calls = _host(owner)
    with pytest.raises(ProviderConfigurationError, match="provider_connection_identity_unproven"):
        service.identity_for_connection("synthetic", "card")
    assert calls == [("synthetic", "card")]
    assert service.current_identity.identity_id == "bob"


@pytest.mark.parametrize("provider,credential", [
    (None, "card"), ("", "card"), (False, "card"),
    ("synthetic", None), ("synthetic", ""), ("synthetic", 7),
])
def test_bad_source_coordinates_reject_before_storage(provider, credential):
    service, calls = _host("alice")
    with pytest.raises(ProviderConfigurationError, match="provider_connection_identity_unproven"):
        service.identity_for_connection(provider, credential)
    assert calls == []


def test_exact_owner_resolver_ignores_gtk_selection_and_returns_bound_identity():
    service, calls = _host("alice")
    selected = service.identity_for_connection("synthetic", "card")
    assert selected.identity_id == "alice"
    assert selected is service.identities[0]
    assert calls == [("synthetic", "card")]


def test_direct_acquisition_telemetry_never_attributes_unbound_source_to_gtk_identity():
    service, calls = _host(None)
    service._sessions = {}
    service._session_key = lambda p, c: (p, c)
    service._acquire_direct_item_impl = lambda item: NS(path=None)
    records = []
    service._record_action = lambda identity, action, status, **kwargs: (
        records.append((identity, action, status, kwargs))
    )
    result = service.acquire_direct_item(_item())
    assert result.path is None
    assert len(calls) == 1
    assert records == [(
        None, "acquisition", "success",
        {"provider_id": "synthetic", "facts": {"record_id": "edition"}},
    )]


def test_legacy_direct_download_denied_without_persisted_source_owner():
    service, calls = _host(None)
    service._sessions = {}
    service._session_key = lambda p, c: (p, c)
    effects = []
    def forbidden(*_args, **_kwargs):
        effects.append("download")
        raise AssertionError("unbound source must not GET or write a book")
    service._session_for_source = lambda p, c: NS(download_direct=forbidden)
    service.acquisition = NS(
        content_type_for=lambda _item: "ebook",
        target_dir=forbidden,
        override_command=forbidden,
    )
    service.library_identity_provider_dir = forbidden
    with pytest.raises(ProviderConfigurationError, match="provider_connection_identity_unproven"):
        service._acquire_direct_item_impl(_item())
    assert calls == [("synthetic", "card")]
    assert effects == []


def test_unbound_non_paris_return_projection_rejects_without_ledger_reads():
    service, calls = _host(None)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("return status must not inspect foreign entitlements")
    service.storage.entitlements = forbidden
    service.storage.acquisitions = forbidden
    loan = Loan(
        id="loan", provider="upstream", medium="digital",
        record_id="edition", title="Private loan",
        source_provider="synthetic", source_credential="card",
    )
    assert service.adept_return_status_for_loan(loan) == {
        "status": "adept_identity_unproven", "return_available": False,
    }
    assert calls == [("synthetic", "card")]
