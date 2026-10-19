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
        BnfCatalogueSession.parse_records(envelope(inner))
    assert "token=" not in str(caught.value)


def test_explicit_zero_is_valid_no_results():
    assert BnfCatalogueSession.parse_records(
        envelope("<numberOfRecords>0</numberOfRecords>")
    ) == []


def test_service_diagnostic_remains_distinct_from_zero():
    with pytest.raises(ProviderError, match="requête refusée"):
        BnfCatalogueSession.parse_records(envelope(
            '<numberOfRecords>0</numberOfRecords><diagnostics>'
            '<diagnostic><message>PRIVATE_TOKEN</message></diagnostic>'
            '</diagnostics>'
        ))
