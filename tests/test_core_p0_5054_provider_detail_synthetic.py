"""P0 #5054: one-shot metadata enrichment is a generic provider contract."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.provider_explicit_enrichment import enrich_provider_detail_once
from cifereca.models import SearchResult
from cifereca.plugin_api import ProviderCapabilityError


def _enrich(service, row, *, expected_identity_id):
    return enrich_provider_detail_once(
        service, row, expected_identity_id=expected_identity_id,
        selected_identity_id=service.current_identity.identity_id,
        expected_context_epoch=service._account_context_epoch,
    )


def _item(credential="fixture-card", provider="fixture-library"):
    return SearchResult(
        id="fixture:42", record_id="fixture:42", docbase="FIXTURE",
        medium="digital", title="Book", author=None, type="Digital",
        format=None, publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url="https://fictional.invalid/books/42",
        scenario="FIXTURE_PRIVATE", source_provider=provider,
        source_credential=credential, source_collection="library",
        owned=True, downloadable=True, download_url=None, metadata={},
    )


def _setup():
    item = _item()
    account = NS(provider_id="fixture-library", credential_id="fixture-card")
    service = NS(
        current_identity=NS(identity_id="reader"), accounts=[account],
        _source_context_epoch=7, _account_context_epoch=4,
        accounts_for_identity=lambda identity: [account] if identity == "reader" else [],
    )
    calls = []
    session = NS()
    def enrich(row):
        calls.append(row.record_id)
        return replace(row, publisher="Metadata only")
    session.enrich_owned_public_detail = enrich
    service._session_for_source = lambda *_args: session
    return service, item, session, calls


def test_synthetic_provider_details_preserve_offer_and_scope():
    service, item, _session, calls = _setup()
    enriched = _enrich(
        service, item, expected_identity_id="reader",
    )
    assert enriched.publisher == "Metadata only"
    assert (enriched.source_provider, enriched.source_credential,
            enriched.record_id, enriched.owned, enriched.downloadable) == (
            "fixture-library", "fixture-card", "fixture:42", True, True,
        )
    assert calls == ["fixture:42"]


@pytest.mark.parametrize("damage", [
    "identity", "account", "epoch", "provider", "offer", "download_url",
])
def test_synthetic_enrichment_rejects_rebound_or_privilege_change(damage):
    service, item, session, calls = _setup()
    def malicious(row):
        calls.append(row.record_id)
        if damage == "identity":
            service.current_identity = NS(identity_id="foreign")
            service._account_context_epoch += 1
        elif damage == "account":
            service.accounts_for_identity = lambda identity: []
        elif damage == "epoch":
            service._source_context_epoch += 1
        elif damage == "provider":
            return replace(row, source_provider="other-provider")
        elif damage == "offer":
            return replace(row, downloadable=False)
        elif damage == "download_url":
            return replace(row, download_url="https://private.invalid/access?secret=1")
        return replace(row, publisher="Not admitted")
    session.enrich_owned_public_detail = malicious
    with pytest.raises(ProviderCapabilityError, match="explicit_public_enrichment_unavailable"):
        _enrich(service, item, expected_identity_id="reader")
    assert calls == ["fixture:42"]


def test_foreign_account_rejected_before_supplier_method():
    service, item, _session, calls = _setup()
    with pytest.raises(ProviderCapabilityError):
        _enrich(
            service, replace(item, source_credential="foreign"),
            expected_identity_id="reader",
        )
    assert calls == []
