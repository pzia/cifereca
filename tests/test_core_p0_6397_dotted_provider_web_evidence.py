"""Namespaced provider IDs bind exactly to the public parser marker (#6397)."""
import pytest

from cifereca.core.search_orchestration import _is_public_web_evidence, _public_catalog_url

URL = "https://catalogue.example.org/notice/42"


@pytest.mark.parametrize("provider", ["seller-a", "seller-a.tenant-7", "seller.a.b"])
def test_exact_producer_namespace(provider):
    proof = provider + ".public_search_record_identifier"
    assert _is_public_web_evidence(proof, provider)
    assert _public_catalog_url(URL, evidence=proof, provider_id=provider) == URL
    assert not _is_public_web_evidence(proof, provider + ".other")
    assert _public_catalog_url(URL, evidence=proof, provider_id=provider + ".other") is None


@pytest.mark.parametrize("provider", [".seller", "seller.", "seller..tenant", "seller/tenant",
                                      "seller.%2e", "Seller.tenant", "a" * 65])
def test_invalid_namespace_does_not_qualify_marker(provider):
    proof = provider + ".public_search_record_identifier"
    assert not _is_public_web_evidence(proof, provider)
    assert _public_catalog_url(URL, evidence=proof, provider_id=provider) is None


def test_namespaced_marker_does_not_weaken_url_policy():
    provider = "seller.tenant"
    proof = provider + ".public_search_record_identifier"
    for url in (URL + "?token=x", URL + "/%2e%2e", URL + ".epub",
                "https://catalogue.example.org/account/42", URL + "#secret"):
        assert _public_catalog_url(url, evidence=proof, provider_id=provider) is None
