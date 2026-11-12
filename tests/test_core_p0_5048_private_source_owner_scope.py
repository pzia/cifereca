"""#5048: no provider-specific exception to private holdings/offer owner checks."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.private_source_owner_scope import private_source_owner_matches
from cifereca.models import ItemDetail
from cifereca.services import LibraryService
from tests.test_core_p0_5056_neutral_public_holdings import row


class FakeStorage:
    def __init__(self, owners):
        self.owners = owners
        self.reads = []
    def identity_for_connection(self, provider, credential):
        self.reads.append((provider, credential))
        return self.owners.get((provider, credential))


@pytest.mark.parametrize("provider", ["synthetic", "kobo", "paris", "numilog"])
def test_exact_source_owner_is_required_even_if_catalogue_flags_claim_owned(provider):
    storage = FakeStorage({(provider, "credential-b"): "bob"})
    service = NS(storage=storage, current_identity=NS(identity_id="alice"))
    item = row(provider=provider, credential="credential-b", metadata={"owned": True})
    assert private_source_owner_matches(service, item) is False
    assert storage.reads == [(provider, "credential-b")]
    service.current_identity = NS(identity_id="bob")
    assert private_source_owner_matches(service, item) is True
    storage.owners[(provider, "credential-b")] = None
    assert private_source_owner_matches(service, item) is False


@pytest.mark.parametrize("provider", ["synthetic", "kobo", "paris", "numilog"])
def test_foreign_source_never_makes_private_holdings_get(provider):
    item = row(provider=provider, credential="foreign")
    host = NS(
        current_identity=NS(identity_id="alice"),
        storage=FakeStorage({(provider, "foreign"): "bob"}),
    )
    detail = LibraryService.holdings(host, item, force=True)
    assert detail.holdings == []
    assert detail.availability_status == "unknown"
    assert detail.source_provider == provider
    assert host.storage.reads == [(provider, "foreign")]


@pytest.mark.parametrize("provider", ["synthetic", "kobo", "paris", "numilog"])
def test_foreign_source_has_no_direct_download_visibility(provider):
    item = row(provider=provider, credential="foreign")
    host = NS(
        current_identity=NS(identity_id="alice"),
        storage=FakeStorage({(provider, "foreign"): "bob"}),
    )
    assert LibraryService.can_direct_download(host, item) is False
    assert host.storage.reads == [(provider, "foreign")]


def test_shared_read_only_metadata_cannot_become_private_when_owner_matches():
    item = row(provider="synthetic", credential="credential",
               metadata={"catalogue_access": "shared_read_only"})
    storage = FakeStorage({("synthetic", "credential"): "alice"})
    host = NS(current_identity=NS(identity_id="alice"), storage=storage)
    assert private_source_owner_matches(host, item) is False
    assert storage.reads == []


def test_missing_provider_or_connection_is_not_private_authority():
    item = row(provider="missing", credential="absent")
    host = NS(current_identity=NS(identity_id="alice"),
              storage=FakeStorage({}))
    assert private_source_owner_matches(host, item) is False
    assert LibraryService.can_direct_download(host, item) is False


def test_owner_rebound_during_live_holdings_never_publishes_or_caches_details():
    item = row(provider="synthetic", credential="alice-source")
    owner = {("synthetic", "alice-source"): "alice"}
    storage = FakeStorage(owner)
    storage.cache_put = lambda *_args: pytest.fail("revoked details must not be cached")
    calls = []

    def read_holdings(_item, _terms):
        calls.append("get")
        owner[("synthetic", "alice-source")] = "bob"
        return ItemDetail(
            record_id=item.record_id, docbase=item.docbase, title=item.title,
            author=None, type=None, year=None, thumbnail_url=None,
            availability_status="available", availability_label="PRIVATE",
            holdings=[], source_provider="synthetic",
            source_credential="alice-source",
        )

    host = NS(
        current_identity=NS(identity_id="alice"), storage=storage,
        config=NS(get=lambda *_args: 300),
        _session_for_source=lambda *_args: NS(holdings=read_holdings),
    )
    detail = LibraryService.holdings(host, item, force=True)
    assert calls == ["get"]
    assert detail.availability_status == "unknown"
    assert detail.availability_label != "PRIVATE"
    assert len(storage.reads) == 2
