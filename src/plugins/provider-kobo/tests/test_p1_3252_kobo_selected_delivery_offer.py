"""#3252: Kobo private descriptor offer is selected, optional and no content GET.

Synthetic Vault responses; real KoboSession rights bridge and account broker.
The acquisition POST is counted separately from manifest and file GET.
"""
from dataclasses import asdict
from dataclasses import replace
import json

import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_kobo_delivery_offer import exact_kobo_offer_id
from tests.test_p1_3252_rights_observation_snapshot import _rig, OwnerBroker


PRIVATE = "SYNTHETIC_PRIVATE_SIGNATURE_DO_NOT_EXPORT"
SIGNED = "https://cdn.kobo.com/signed/ebook.epub?token=" + PRIVATE


def descriptor(*, product="book-1", candidate=None, account_id="alice"):
    if candidate is None:
        candidate = {
            "kind": "http", "transport": "https", "method": "GET",
            "url": SIGNED, "handling": "http_plain", "media_format": "epub",
            "drm": {"raw_type": "SignedNoDrm"},
            "kobo": {"source": "content_access_book"},
        }
    return {"provider": "kobo", "schema_version": "1.4",
            "acquisition": {
                "product_id": product, "account_id": account_id,
                "mode": "descriptor", "candidates": [candidate],
            }}


def setup():
    session, vault, broker, account = _rig([
        {"product_id": "book-1", "account_id": "alice",
         "owned": True, "downloadable": True, "title": "Synthetic"},
        {"product_id": "book-2", "account_id": "alice",
         "owned": True, "downloadable": False, "title": "Archived"},
    ])
    requests = []
    def acquisition(product_id, **kwargs):
        requests.append((product_id, kwargs))
        return descriptor(product=product_id)
    vault.acquisition = acquisition
    return session, vault, broker, account, requests


def test_no_selection_has_zero_network_access_not_even_inventory():
    s, vault, broker, a, requests = setup()
    try:
        with pytest.raises(ProviderCapabilityError, match="not_admitted"):
            s.rights_delivery_offer_snapshot(a, "book-1")
        assert vault.calls == 0 and requests == []
    finally:
        s.close()


def test_one_selected_manifest_then_one_descriptor_post_without_file_get():
    s, vault, broker, a, requests = setup()
    try:
        rights = s.rights_observation_snapshot(a)
        assert vault.calls == 1
        assert all(r.delivery == "unknown" for r in rights.rights)
        assert requests == []
        offer = s.rights_delivery_offer_snapshot(
            a, "book-1", explicit_user_action=True,
        )
        assert vault.calls == 2
        assert len(requests) == 1
        assert requests[0][0] == "book-1"
        assert requests[0][1]["mode"] == "descriptor"
        assert len(offer.rights) == 1
        assert offer.rights[0].kind == "purchase"
        assert offer.rights[0].state == "active"
        assert offer.rights[0].provider_attested is True
        assert offer.rights[0].delivery == "available"
        assert offer.rights[0].delivery_offer_id.startswith("kobo.descriptor:")
        assert not offer.authorizes_mutation
        assert offer.coverage == "partial"
        exported = json.dumps(asdict(offer))
        assert PRIVATE not in exported and "https://" not in exported
    finally:
        s.close()


def test_unowned_product_never_gets_descriptor_post():
    s, vault, broker, a, requests = setup()
    try:
        with pytest.raises(ProviderCapabilityError, match="right_unobserved"):
            s.rights_delivery_offer_snapshot(a, "book-absent", explicit_user_action=True)
        assert vault.calls == 1 and requests == []
    finally:
        s.close()


@pytest.mark.parametrize("payload", [
    descriptor(product="wrong-edition"),
    descriptor(account_id="bob"),
    descriptor(candidate={"kind": "http", "transport": "http", "method": "GET",
                          "url": "http://cdn.kobo.com/book.epub",
                          "handling": "http_plain", "drm": {"raw_type": "SignedNoDrm"},
                          "media_format": "epub"}),
    descriptor(candidate={"kind": "http", "transport": "https", "method": "POST",
                          "url": SIGNED, "handling": "http_plain",
                          "drm": {"raw_type": "SignedNoDrm"}, "media_format": "epub"}),
    descriptor(candidate={"kind": "http", "transport": "https", "method": "GET",
                          "url": SIGNED, "handling": "kobo_kdrm",
                          "drm": {"raw_type": "KDRM"}, "media_format": "kepub"}),
    descriptor(candidate={"kind": "http", "transport": "https", "method": "GET",
                          "url": SIGNED, "handling": "http_plain",
                          "drm": {"raw_type": "SignedNoDrm"}, "media_format": "unknown"}),
    {"acquisition": {"product_id": "book-1", "candidates": []}},
])
def test_unverified_descriptors_never_claim_available(payload):
    s, vault, broker, a, requests = setup()
    vault.acquisition = lambda product_id, **opts: (requests.append("POST") or payload)
    try:
        offer = s.rights_delivery_offer_snapshot(a, "book-1", explicit_user_action=True)
        assert offer.rights[0].state == "active"
        assert offer.rights[0].delivery == "unknown"
        assert offer.rights[0].delivery_offer_id == ""
        assert requests == ["POST"] and vault.calls == 1
    finally:
        s.close()


def test_ambiguous_candidate_routes_never_claim_one_offer():
    s, vault, broker, a, requests = setup()
    double = descriptor()
    double["acquisition"]["candidates"].append(
        dict(double["acquisition"]["candidates"][0])
    )
    vault.acquisition = lambda product_id, **opts: double
    try:
        offer = s.rights_delivery_offer_snapshot(a, "book-1", explicit_user_action=True)
        assert offer.rights[0].delivery == "unknown"
    finally:
        s.close()


@pytest.mark.parametrize("change", [
    "broker_rebind", "account_rebind", "account_replace", "client_rebind",
    "generation_change",
])
def test_rebinding_during_descriptor_post_cannot_publish_old_offer(change):
    s, vault, broker, a, requests = setup()
    def during_post(product_id, **kw):
        requests.append("POST")
        if change == "broker_rebind":
            broker.identities = ("identity-B",)
        elif change == "account_rebind":
            s._accounts[0].identity_id = "identity-B"
        elif change == "account_replace":
            s._accounts = [replace(s._accounts[0])]
        elif change == "client_rebind":
            s.client = object()
        else:
            s._personal_binding_generation += 1
        return descriptor()
    vault.acquisition = during_post
    try:
        with pytest.raises((ProviderCapabilityError, ProviderError)):
            s.rights_delivery_offer_snapshot(a, "book-1", explicit_user_action=True)
        assert requests == ["POST"] and vault.calls == 1
    finally:
        s.close()


def test_rotating_signed_url_does_not_change_opaque_offer_id():
    first = descriptor()
    second = descriptor()
    second["acquisition"]["candidates"][0]["url"] = (
        "https://cdn.kobo.com/signed/ebook.epub?token=ROTATED"
    )
    options = dict(identity_id="identity-A", credential_id="card-A",
                   account_id="alice", product_id="book-1")
    a = exact_kobo_offer_id(first, **options)
    b = exact_kobo_offer_id(second, **options)
    assert a and a == b
    assert a != exact_kobo_offer_id(first, **{**options, "identity_id": "identity-B"})
