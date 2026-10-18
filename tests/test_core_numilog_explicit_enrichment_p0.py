"""P0 #1072: no-network Core boundary for an explicit Numilog book action."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cifereca.core.numilog_explicit_enrichment import (
    can_enrich_numilog_result, enrich_numilog_result_once,
)
from cifereca.models import SearchResult
from cifereca.plugin_api import ProviderCapabilityError


def item(**kwargs):
    values = dict(
        id="numilog:scope:42:()", record_id="numilog:scope:42:()",
        docbase="NUMILOG", medium="digital", title="Livre acheté",
        author=None, type="Livre numérique", format=None, publisher=None,
        published_at=None, description=None, thumbnail_url=None,
        friendly_url="https://www.numilog.com/1958476/book.ebook",
        scenario="NUMILOG_LIBRARY", source_provider="numilog",
        source_credential="alice", source_collection="library",
        owned=True, downloadable=True, download_url=None, metadata={},
    )
    values.update(kwargs)
    return SearchResult(**values)


class Service:
    def __init__(self, accounts=()):
        self.current_identity = SimpleNamespace(identity_id="identity-A")
        self.accounts = list(accounts)
        self.calls = []
        self.result = None
        self.on_call = None

    def accounts_for_identity(self, identity_id):
        self.calls.append(("accounts", identity_id))
        return self.accounts if identity_id == "identity-A" else []

    def _session_for_source(self, provider, credential):
        self.calls.append(("session", provider, credential))
        def do(item):
            self.calls.append(("enrich", item.record_id))
            if self.on_call:
                self.on_call()
            return self.result if self.result is not None else replace(
                item, publisher="Éditeur public",
            )
        return SimpleNamespace(
            enrich_owned_public_detail=do,
            enrich_public_detail=do,
        )


OWNED_ACCOUNT = SimpleNamespace(provider_id="numilog", credential_id="alice")


def test_owned_action_is_one_shot_and_does_not_change_download_rights():
    service = Service([OWNED_ACCOUNT])
    row = item()
    assert can_enrich_numilog_result(row)
    value = enrich_numilog_result_once(service, row, expected_identity_id="identity-A")
    assert value.publisher == "Éditeur public"
    assert value.owned is True and value.downloadable is True
    assert value.download_url is None and value.format is None
    assert service.calls.count(("enrich", row.record_id)) == 1
    assert row.publisher is None


def test_public_catalogue_isolated_without_private_account_query():
    service = Service()
    row = item(
        id="numilog:public:1958476", record_id="numilog:public:1958476",
        scenario="NUMILOG_PUBLIC", source_provider="numilog-catalog",
        source_credential="__public__", source_collection="public_catalog",
        owned=False, downloadable=False,
    )
    value = enrich_numilog_result_once(service, row, expected_identity_id="identity-A")
    assert value.owned is False and value.downloadable is False
    assert not [x for x in service.calls if x[0] == "accounts"]
    assert service.calls.count(("enrich", row.record_id)) == 1


@pytest.mark.parametrize("changes", [
    {"source_credential": "bob"}, {"source_provider": "other"},
    {"owned": False}, {"source_collection": "public_catalog"},
    {"download_url": "https://example.invalid/private-token"},
])
def test_invalid_purchase_does_not_start_any_provider_request(changes):
    service = Service([OWNED_ACCOUNT])
    with pytest.raises(ProviderCapabilityError, match="explicit_public_enrichment_unavailable"):
        enrich_numilog_result_once(
            service, item(**changes), expected_identity_id="identity-A",
        )
    assert not any(entry[0] in {"session", "enrich"} for entry in service.calls)


def test_wrong_active_identity_rejects_before_any_session_lookup():
    service = Service([OWNED_ACCOUNT])
    with pytest.raises(ProviderCapabilityError):
        enrich_numilog_result_once(
            service, item(), expected_identity_id="identity-B",
        )
    assert service.calls == []


def test_changing_profile_mid_request_discards_result():
    service = Service([OWNED_ACCOUNT])
    service.on_call = lambda: setattr(
        service, "current_identity", SimpleNamespace(identity_id="identity-B"),
    )
    with pytest.raises(ProviderCapabilityError):
        enrich_numilog_result_once(
            service, item(), expected_identity_id="identity-A",
        )
    assert service.calls.count(("enrich", item().record_id)) == 1


@pytest.mark.parametrize("changes", [
    {"owned": False}, {"downloadable": False},
    {"download_url": "https://example.invalid/fulfillment?auth=PRIVATE_TOKEN"},
    {"format": "EPUB"}, {"source_credential": "bob"},
    {"record_id": "numilog:scope:other:()"},
    {"source_provider": "numilog-catalog"},
])
def test_provider_must_not_promote_acquisition_or_cross_reference(changes):
    service = Service([OWNED_ACCOUNT])
    service.result = replace(item(), **changes)
    with pytest.raises(ProviderCapabilityError, match="explicit_public_enrichment_unavailable"):
        enrich_numilog_result_once(
            service, item(), expected_identity_id="identity-A",
        )


def test_legacy_can_be_a_real_credential_id_if_account_matches():
    service = Service([SimpleNamespace(provider_id="numilog", credential_id="legacy")])
    row = item(source_credential="legacy")
    result = enrich_numilog_result_once(service, row, expected_identity_id="identity-A")
    assert result.source_credential == "legacy"
