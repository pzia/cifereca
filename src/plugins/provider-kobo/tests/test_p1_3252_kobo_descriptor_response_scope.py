"""#3252 Kobo delivery preflight: conflicting descriptor scopes never become A offers.

No Vault HTTP, no file GET, no real accounts, no bearer token or local Core
state. Covers the pure provider-controlled POST-response admission boundary.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca_kobo_delivery_offer import exact_kobo_offer_id

SCOPE = {
    "identity_id": "identity-A", "credential_id": "card-A",
    "account_id": "reader-A", "product_id": "edition-A",
}
SIGNED_URL = "https://cdn.kobo.com/book.epub?token=FAKE_SIGNATURE"


def descriptor():
    return {
        "provider": "kobo",
        "schema_version": "1.4",
        "acquisition": {
            "mode": "descriptor",
            "product_id": "edition-A",
            "account_id": "reader-A",
            "candidates": [
                {
                    "kind": "http", "transport": "https", "method": "GET",
                    "url": SIGNED_URL, "media_format": "epub",
                    "handling": "http_plain",
                    "drm": {"raw_type": "SignedNoDrm"},
                },
            ],
        },
    }


def offer(payload):
    return exact_kobo_offer_id(payload, **SCOPE)


def test_exact_scoped_descriptor_returns_opaque_non_url_offer():
    payload = descriptor()
    result = offer(payload)
    assert result.startswith("kobo.descriptor:")
    assert SIGNED_URL not in result and "FAKE_SIGNATURE" not in result
    assert offer(deepcopy(payload)) == result
    without_optional_fields = deepcopy(payload)
    del without_optional_fields["provider"]
    del without_optional_fields["acquisition"]["mode"]
    del without_optional_fields["acquisition"]["account_id"]
    assert offer(without_optional_fields) == result


@pytest.mark.parametrize(("location", "field", "foreign"), [
    ("root", "provider", "paris"),
    ("root", "source_provider", "paris"),
    ("root", "product_id", "edition-B"),
    ("root", "account_id", "reader-B"),
    ("root", "credential_id", "card-B"),
    ("root", "identity_id", "identity-B"),
    ("acquisition", "provider", "numilog"),
    ("acquisition", "source_provider", "numilog"),
    ("acquisition", "product_id", "edition-B"),
    ("acquisition", "account_id", "reader-B"),
    ("acquisition", "credential_id", "card-B"),
    ("acquisition", "identity_id", "identity-B"),
    ("acquisition", "mode", "delegated"),
    ("candidate", "provider", "paris"),
    ("candidate", "source_provider", "paris"),
    ("candidate", "product_id", "edition-B"),
    ("candidate", "account_id", "reader-B"),
    ("candidate", "credential_id", "card-B"),
    ("candidate", "identity_id", "identity-B"),
])
def test_explicitly_foreign_scope_or_mode_cannot_claim_offer(location, field, foreign):
    payload = descriptor()
    if location == "root":
        target = payload
    elif location == "acquisition":
        target = payload["acquisition"]
    else:
        target = payload["acquisition"]["candidates"][0]
    target[field] = foreign
    assert offer(payload) == ""


@pytest.mark.parametrize("foreign", [None, True, 0, "", [], {}, "reader-B"])
def test_wrong_type_or_missing_exact_account_id_is_not_a_match(foreign):
    payload = descriptor()
    payload["acquisition"]["account_id"] = foreign
    assert offer(payload) == ""


def test_mixed_valid_and_foreign_candidate_does_not_select_valid_sibling():
    payload = descriptor()
    foreign = deepcopy(payload["acquisition"]["candidates"][0])
    foreign["account_id"] = "reader-B"
    foreign["method"] = "POST"  # unusable itself, still a contradicting scope
    payload["acquisition"]["candidates"].append(foreign)
    assert offer(payload) == ""


def test_explicit_correct_scope_fields_are_not_required_but_are_supported():
    payload = descriptor()
    for key, value in (
        ("product_id", "edition-A"), ("account_id", "reader-A"),
        ("credential_id", "card-A"), ("identity_id", "identity-A"),
        ("source_provider", "kobo"),
    ):
        payload[key] = value
        payload["acquisition"][key] = value
        payload["acquisition"]["candidates"][0][key] = value
    assert offer(payload).startswith("kobo.descriptor:")


def test_signature_rotation_preserves_opaque_offer_not_rights_or_access():
    payload = descriptor()
    other = deepcopy(payload)
    other["acquisition"]["candidates"][0]["url"] = (
        "https://cdn.kobo.com/book.epub?token=NEW_FAKE_SIGNATURE"
    )
    assert offer(payload) == offer(other)
    assert offer(payload) != exact_kobo_offer_id(
        payload, **{**SCOPE, "identity_id": "identity-B"}
    )
