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


@pytest.mark.parametrize("suffix,valid", [
    ("/details.php?book=3500", True),
    ("/detail.aspx?record=42", True),
    ("/details.php?book=3500&token=x", False),
    ("/details.php?book=3500&book=3501", False),
    ("/details.php?book=%33", False),
    ("/details.php?book=0", False),
    ("/details.php?session=3500", False),
    ("/details.php?book=3500#x", False),
    ("/search.aspx?id=42", False),
    ("/account/details.php?id=42", False),
    ("/details.php", False),
])
def test_query_locator_requires_closed_shape_and_exact_url_record_digest(suffix, valid):
    from hashlib import sha256

    provider = "seller.tenant"
    proof = provider + ".public_search_record_query"
    url = "https://catalogue.example.org" + suffix
    record = sha256(url.encode()).hexdigest()[:24]
    assert _public_catalog_url(url, evidence=proof, provider_id=provider,
                               record_id=record) == (url if valid else None)
    assert _public_catalog_url(url, evidence=proof, provider_id=provider,
                               record_id="0" * 24) is None
    assert _public_catalog_url(url, evidence=proof, provider_id=provider) is None
    if valid:
        assert _public_catalog_url(url, evidence=provider + ".public_search_record_identifier",
                                   provider_id=provider, record_id=record) is None
