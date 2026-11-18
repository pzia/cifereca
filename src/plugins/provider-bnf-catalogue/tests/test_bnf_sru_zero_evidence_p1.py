"""#1917: only an explicit SRU zero is a legitimate empty BnF result.

Pure offline XML. Neither a malformed upstream response nor a diagnostic
can masquerade as a successful zero catalogue result.
"""
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_bnf_catalogue import BnfCatalogueSession

URI = "http://www.loc.gov/zing/srw/"


def envelope(inner):
    return (
        '<searchRetrieveResponse xmlns="' + URI + '">'
        + inner + '</searchRetrieveResponse>'
    ).encode()


def parse(content):
    # Parsing these synthetic empty SRU responses requires no connection,
    # HTTP client or account; there are no record rows to project.
    return BnfCatalogueSession.parse_records(
        object.__new__(BnfCatalogueSession), content,
    )


@pytest.mark.parametrize("inner", [
    "",
    "<numberOfRecords></numberOfRecords>",
    "<numberOfRecords>-1</numberOfRecords>",
    "<numberOfRecords>not-a-number</numberOfRecords>",
    "<numberOfRecords>0</numberOfRecords><numberOfRecords>0</numberOfRecords>",
    "<numberOfRecords>999999999999999999999999</numberOfRecords>",
])
def test_unqualified_or_malformed_sru_total_never_proves_zero(inner):
    with pytest.raises(ProviderError, match="invalid_sru_response") as caught:
        parse(envelope(inner))
    assert "token=" not in str(caught.value)


def test_explicit_zero_is_valid_no_results():
    assert parse(envelope("<numberOfRecords>0</numberOfRecords>")) == []


def test_service_diagnostic_remains_distinct_from_zero():
    with pytest.raises(ProviderError, match="requête refusée"):
        parse(envelope(
            '<numberOfRecords>0</numberOfRecords><diagnostics>'
            '<diagnostic><message>PRIVATE_TOKEN</message></diagnostic>'
            '</diagnostics>'
        ))


def test_sru_diagnostic_refusal_does_not_authorize_offline_fallback():
    from cifereca.search_failures import search_failure_code
    from cifereca.core.known_catalog_fallback import public_service_unavailable

    with pytest.raises(ProviderError, match="requête refusée") as caught:
        parse(envelope(
            '<numberOfRecords>0</numberOfRecords><diagnostics>'
            '<diagnostic><message>PRIVATE_SIGNED_TOKEN</message></diagnostic>'
            '</diagnostics>'
        ))
    assert search_failure_code(caught.value) is None
    assert not public_service_unavailable(caught.value)
    assert "PRIVATE_SIGNED_TOKEN" not in str(caught.value)


@pytest.mark.parametrize("encoding", ("utf-8", "utf-16"))
def test_sru_forbidden_dtd_never_proves_zero_or_authorizes_fallback(encoding):
    from cifereca.search_failures import search_failure_code
    from cifereca.core.known_catalog_fallback import public_service_unavailable
    payload = ('<!DOCTYPE searchRetrieveResponse [<!ENTITY count "0">]>'
               '<searchRetrieveResponse xmlns="' + URI + '">'
               '<numberOfRecords>&count;</numberOfRecords></searchRetrieveResponse>').encode(encoding)
    with pytest.raises(ProviderError, match="invalid_sru_response") as caught:
        parse(payload)
    assert search_failure_code(caught.value) is None
    assert not public_service_unavailable(caught.value)
    assert caught.value.__context__ is None


@pytest.mark.parametrize("payload", (
    envelope("<numberOfRecords>0</numberOfRecords>" + "<x>" * 100 + "</x>" * 100),
    envelope("<numberOfRecords>0</numberOfRecords>" + "<x/>" * 12001),
    envelope("<numberOfRecords>0</numberOfRecords>" + " " * (2 * 1024 * 1024)),
))
def test_sru_parser_budgets_are_not_attested_zero(payload):
    from cifereca.search_failures import search_failure_code
    with pytest.raises(ProviderError, match="invalid_sru_response") as caught:
        parse(payload)
    assert search_failure_code(caught.value) is None
    assert caught.value.__context__ is None
