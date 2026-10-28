"""#3252: contradictory selected Kobo media claims cannot grant delivery.

The published offer and the candidate executor must agree on the exact
HTTP/Adobe route. Synthetic Vault only, no real content GET or ADEPT.
"""
from copy import deepcopy

import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_kobo_delivery_offer import exact_kobo_offer_id
from tests.test_p1_3252_kobo_descriptor_response_scope import (
    SCOPE, descriptor,
)
from tests.test_p1_3252_kobo_selected_delivery_offer import setup
from tests.test_p1_3252_kobo_selected_candidate import (
    admitted, execute,
)


@pytest.mark.parametrize("source_format", [
    "pdf", "acsm", "kepub", "unknown", "", 2, False, {},
])
def test_explicit_incompatible_or_malformed_source_format_is_not_offer(
    source_format,
):
    value = descriptor()
    value["acquisition"]["candidates"][0]["source_format"] = source_format
    assert exact_kobo_offer_id(value, **SCOPE) == ""


@pytest.mark.parametrize("media_format", ["pdf", "acsm", "kepub", 3, ""])
def test_explicit_incompatible_media_format_is_not_offer(media_format):
    value = descriptor()
    candidate = value["acquisition"]["candidates"][0]
    candidate["source_format"] = "epub"
    candidate["media_format"] = media_format
    assert exact_kobo_offer_id(value, **SCOPE) == ""


def test_agreeing_format_claims_keep_existing_exact_offer():
    value = descriptor()
    ordinary = exact_kobo_offer_id(value, **SCOPE)
    value["acquisition"]["candidates"][0]["source_format"] = "  EPUB "
    assert ordinary and exact_kobo_offer_id(value, **SCOPE) == ordinary


def test_valid_source_format_without_media_format_remains_supported():
    value = descriptor()
    candidate = value["acquisition"]["candidates"][0]
    del candidate["media_format"]
    candidate["source_format"] = "epub"
    assert exact_kobo_offer_id(value, **SCOPE).startswith("kobo.descriptor:")


def test_changed_format_claim_between_preflight_and_candidate_has_zero_get(
    tmp_path, monkeypatch,
):
    from cifereca.plugin_api import HTTPX_TRANSPORT
    session, vault, broker, account, posts = setup()
    attempted = []
    def prohibited(*, producer, timeout):
        attempted.append("HTTP GET CLIENT")
        raise AssertionError("No GET for contradictory formats")
    monkeypatch.setattr(HTTPX_TRANSPORT, "public_client", prohibited)
    try:
        offer = admitted(session, account)
        def conflicted(product_id, **kwargs):
            posts.append((product_id, kwargs))
            value = deepcopy(
                __import__(
                    "tests.test_p1_3252_kobo_selected_delivery_offer",
                    fromlist=["descriptor"],
                ).descriptor(),
            )
            value["acquisition"]["candidates"][0]["source_format"] = "pdf"
            return value
        vault.acquisition = conflicted
        with pytest.raises((ProviderCapabilityError, ProviderError)):
            execute(session, account, offer, tmp_path)
        assert attempted == [] and not list(tmp_path.iterdir())
        assert len(posts) == 2
    finally:
        session.close()
