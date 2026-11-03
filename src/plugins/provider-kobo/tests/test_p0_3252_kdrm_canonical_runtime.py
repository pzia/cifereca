"""P0 #3252: Kobo KDRM uses only an exact Core-guarded DRM runtime.

Eight synthetic owned KEPUBs, one selected offer per product. Never touch
Kobo's live API, credentials, ContentKeys, ADEPT or the user's library.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError, HTTPX_TRANSPORT
from cifereca_kobo_delivery_offer import exact_kobo_offer_id
from tests.test_p1_3252_kobo_selected_candidate import epub_bytes
from tests.test_p1_3252_kobo_selected_delivery_offer import (
    SIGNED, descriptor, setup,
)


PRIVATE = "PRIVATE_DELEGATED_BEARER_MUST_NOT_LEAK"


def kdrm_candidate(product):
    return {
        "kind": "http", "transport": "https", "method": "GET",
        "url": SIGNED.replace("ebook.epub", product + ".kepub"),
        "handling": "kobo_kdrm", "media_format": "kepub",
        "source_format": "KEPUB",
        "drm": {"raw_type": "KDRM", "family": "kobo_kdrm", "protected": True},
        "kobo": {"source": "library_sync", "display_profile": "Android"},
    }


def delegated(product, *, account_id="alice", keys=False):
    query = {"DisplayProfile": "Android"}
    if keys:
        query["ContentKeys"] = PRIVATE
    return {
        "provider": "kobo", "acquisition": {
            "product_id": product, "account_id": account_id,
            "mode": "delegated", "delegation": {
                "content_access_request": {
                    "url": "https://api.kobo.com/contentaccess/books/" + product,
                    "method": "GET",
                    "headers": {"Authorization": "Bearer " + PRIVATE},
                    "query": query,
                },
                "session_context": {
                    "device_id": "device-synthetic", "user_id": "user-synthetic",
                },
            },
        },
    }


def ready(session, vault, *, delegated_error=None):
    session.runtime_capabilities = NS(
        acquisition=lambda provider: NS(
            available=True, compatible=True, operational=True,
            modes=("kobo_kdrm",),
        )
    )
    calls = []

    def acquisition(product, **kwargs):
        mode = kwargs.get("mode")
        calls.append((product, mode))
        if mode == "descriptor":
            return descriptor(product=product, candidate=kdrm_candidate(product))
        if mode == "delegated":
            if delegated_error == "foreign":
                return delegated(product, account_id="other-account")
            if delegated_error == "keys":
                return delegated(product, keys=True)
            if delegated_error == "missing":
                return {"provider": "kobo", "acquisition": {
                    "product_id": product, "mode": "delegated",
                }}
            return delegated(product)
        raise AssertionError("unexpected Kobo acquisition mode")

    vault.acquisition = acquisition
    return calls


def selected(session, account, product):
    return session.rights_delivery_offer_snapshot(
        account, product, explicit_user_action=True,
    ).rights[0]


def deliver(session, account, product, offer, target, runtime, *, allowed=lambda: True):
    return session.downloadable_selected_delivery_candidate_runtime_guarded(
        account, edition_id=product, resource_id=product,
        kind="purchase", branch="personal_manifest",
        selected_offer_id=offer, target_dir=target,
        explicit_user_action=True, admission_current=allowed,
        execute_runtime=runtime,
    )


def test_eight_kdrm_purchases_get_private_runtime_recipe_not_public_http(tmp_path, monkeypatch):
    session, vault, broker, account, _ = setup()
    products = [f"book-{n}" for n in range(1, 9)]
    vault.rows = [
        {"product_id": product, "account_id": "alice", "title": product,
         "owned": True, "downloadable": True}
        for product in products
    ]
    calls = ready(session, vault)
    delivered = []

    def forbidden(*args, **kwargs):
        pytest.fail("KDRM must not be downloaded as HTTP plaintext")

    monkeypatch.setattr(HTTPX_TRANSPORT, "attested_https_download", forbidden)

    def runtime(request):
        assert request["source"]["type"] == "kobo"
        assert request["source"]["candidate"]["drm_type"] == "KDRM"
        assert request["source"]["candidate"]["delivery_source"] == "library_sync"
        assert request["source"]["kdrm"] == {
            "device_id": "device-synthetic", "user_id": "user-synthetic",
        }
        assert request["source"]["request"]["headers"]["Authorization"] == "Bearer " + PRIVATE
        assert "ContentKeys" not in str(request)
        assert request["format"] == "epub"
        path = Path(request["destination"])
        path.write_bytes(epub_bytes())
        delivered.append(path)
        return {"ok": True, "artifact": {"path": str(path), "format": "epub"}}

    try:
        for product in products:
            offer = selected(session, account, product)
            assert offer.delivery == "available" and offer.provider_attested
            assert offer.delivery_offer_id.startswith("kobo.descriptor:")
            own_dir = tmp_path / product
            own_dir.mkdir()
            out = deliver(session, account, product,
                          offer.delivery_offer_id, own_dir, runtime)
            assert out.path == delivered[-1] and out.path.read_bytes() == epub_bytes()
            assert out.source.scope.identity_id == "identity-A"
            assert out.selected_offer_id == offer.delivery_offer_id
            assert not out.authorizes_publication
        assert len(delivered) == 8
        # Each selected offer and execution individually verifies both a
        # descriptor and a delegated replay; no global provider GET fallback.
        assert len(calls) == 32
        assert {mode for _, mode in calls} == {"descriptor", "delegated"}
    finally:
        session.close()


@pytest.mark.parametrize("error", ["foreign", "keys", "missing"])
def test_kdrm_cannot_be_offered_without_exact_replay_evidence(error):
    session, vault, broker, account, _ = setup()
    ready(session, vault, delegated_error=error)
    try:
        right = selected(session, account, "book-1")
        assert right.state == "active"
        assert right.delivery == "unknown"
        assert right.delivery_offer_id == ""
    finally:
        session.close()


def test_missing_runtime_leaves_kdrm_pending_without_delegated_post():
    session, vault, broker, account, _ = setup()
    calls = ready(session, vault)
    session.runtime_capabilities = None
    try:
        offer = selected(session, account, "book-1")
        assert offer.state == "active"
        assert offer.delivery == "unknown" and not offer.delivery_offer_id
        assert calls == [("book-1", "descriptor")]
    finally:
        session.close()


def test_core_revocation_during_kdrm_runtime_cleans_private_candidate(tmp_path, monkeypatch):
    session, vault, broker, account, _ = setup()
    ready(session, vault)
    monkeypatch.setattr(HTTPX_TRANSPORT, "attested_https_download",
                        lambda **_: pytest.fail("no plaintext KDRM"))
    consent = [True]
    try:
        offer = selected(session, account, "book-1")
        assert offer.delivery == "available"
        def runtime(request):
            Path(request["destination"]).write_bytes(epub_bytes())
            consent[0] = False
            return {"ok": True, "artifact": {"path": request["destination"]}}
        with pytest.raises(ProviderCapabilityError, match="admission_changed"):
            deliver(session, account, "book-1", offer.delivery_offer_id, tmp_path,
                    runtime, allowed=lambda: consent[0])
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


def test_kdrm_candidate_without_core_runtime_callback_never_downloads(tmp_path, monkeypatch):
    session, vault, broker, account, _ = setup()
    ready(session, vault)
    monkeypatch.setattr(HTTPX_TRANSPORT, "attested_https_download",
                        lambda **_: pytest.fail("no unsafe HTTP fallback"))
    try:
        offer = selected(session, account, "book-1")
        assert offer.delivery == "available"
        with pytest.raises(ProviderCapabilityError, match="runtime_unavailable"):
            session.rights_selected_delivery_candidate(
                account, edition_id="book-1", right_id="book-1",
                kind="purchase", branch="personal_manifest",
                delivery_offer_id=offer.delivery_offer_id, target_dir=tmp_path,
                explicit_user_action=True,
            )
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


def test_kdrm_foreign_identity_offer_not_reused_across_profiles():
    payload = descriptor(product="book-1", candidate=kdrm_candidate("book-1"))
    base = dict(identity_id="identity-A", credential_id="synthetic-card",
                account_id="alice", product_id="book-1", kdrm_runtime_ready=True)
    offer = exact_kobo_offer_id(payload, **base)
    assert offer.startswith("kobo.descriptor:")
    assert exact_kobo_offer_id(
        payload, **{**base, "identity_id": "identity-B"},
    ) != offer
    assert exact_kobo_offer_id(
        payload, **{**base, "kdrm_runtime_ready": False},
    ) == ""


@pytest.mark.parametrize("container", ["delegation", "request", "context"])
def test_nested_foreign_kdrm_replay_scope_cannot_qualify_offer(container):
    session, vault, broker, account, _ = setup()
    calls = ready(session, vault)
    original = vault.acquisition

    def foreign(product, **kwargs):
        raw = original(product, **kwargs)
        if kwargs.get("mode") == "delegated":
            delegation = raw["acquisition"]["delegation"]
            target = (
                delegation if container == "delegation"
                else delegation["content_access_request"] if container == "request"
                else delegation["session_context"]
            )
            target["account_id"] = "other-upstream-account"
        return raw

    vault.acquisition = foreign
    try:
        row = selected(session, account, "book-1")
        assert row.state == "active"
        assert row.delivery == "unknown" and row.delivery_offer_id == ""
        assert calls == [("book-1", "descriptor"), ("book-1", "delegated")]
    finally:
        session.close()


@pytest.mark.parametrize(("media", "upstream", "valid"), [
    ("kepub", "KEPUB", True),
    ("epub", "FLEPUB", True),
    ("epub", "EPUB", True),
    ("kepub", "FLEPUB", False),
    ("pdf", "FLEPUB", False),
    ("epub", "MOBI", False),
])
def test_kdrm_runtime_accepts_only_normative_media_and_upstream_pairs(
    media, upstream, valid,
):
    from cifereca_kobo_kdrm import scoped_kdrm_source

    candidate = kdrm_candidate("book-1")
    candidate["media_format"] = media
    candidate["source_format"] = upstream
    payload = descriptor(product="book-1", candidate=candidate)
    offer = exact_kobo_offer_id(
        payload, identity_id="identity-A",
        credential_id="synthetic-card", account_id="alice",
        product_id="book-1", kdrm_runtime_ready=True,
    )
    source = scoped_kdrm_source(
        delegated("book-1"), candidate,
        product_id="book-1", account_id="alice",
        identity_id="identity-A", credential_id="synthetic-card",
    )
    assert bool(offer) is valid and bool(source) is valid
    if valid:
        assert source["candidate"]["source_format"] == upstream
        assert source["candidate"]["media_format"] == media
