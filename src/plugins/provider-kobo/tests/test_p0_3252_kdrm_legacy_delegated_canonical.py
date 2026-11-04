"""#3252: restore historical KDRM ContentAccess replay in canonical sync.

Synthetic Vault and runtime only. No real HTTP, Kobo login or ContentKeys.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS
from copy import deepcopy

import pytest

from cifereca.plugin_api import HTTPX_TRANSPORT
from cifereca_kobo_delivery_offer import (
    exact_kobo_offer_id, kdrm_delegated_runtime_ready,
)
from tests.test_p0_3252_kdrm_canonical_runtime import (
    delegated, kdrm_candidate, ready, selected, deliver,
)
from tests.test_p1_3252_kobo_selected_candidate import epub_bytes
from tests.test_p1_3252_kobo_selected_delivery_offer import descriptor, setup


def legacy_candidate(product, media="kepub", upstream="KEPUB"):
    result = kdrm_candidate(product)
    result["kobo"] = {"source": "content_access_book", "display_profile": "Android"}
    result["media_format"] = media
    result["source_format"] = upstream
    return result


def legacy_setup(media="kepub", upstream="KEPUB", *, runtime_modes=None,
                 malformed=None, siblings=None):
    session, vault, broker, account, _ = setup()
    calls = ready(session, vault)
    session.runtime_capabilities = NS(
        acquisition=lambda provider: NS(
            available=True, compatible=True, operational=True,
            modes=(runtime_modes if runtime_modes is not None
                   else ("kobo_delegated", "kobo_kdrm")),
        )
    )

    def acquisition(product, **options):
        mode = options["mode"]
        calls.append((product, mode))
        candidate = legacy_candidate(product, media, upstream)
        if mode == "descriptor":
            data = descriptor(product=product, candidate=candidate)
            for sibling in siblings or []:
                data["acquisition"]["candidates"].append(deepcopy(sibling))
            return data
        assert mode == "delegated"
        data = delegated(product)
        data["acquisition"]["candidates"] = [deepcopy(candidate)]
        if malformed == "foreign":
            data["acquisition"]["candidates"][0]["account_id"] = "another-account"
        if malformed == "wrong_format":
            data["acquisition"]["candidates"][0]["source_format"] = "MOBI"
        if malformed == "ambiguous":
            data["acquisition"]["candidates"].append(deepcopy(candidate))
        if malformed == "keys":
            data["acquisition"]["delegation"]["content_access_request"]["query"]["ContentKeys"] = "synthetic-key"
        return data

    vault.acquisition = acquisition
    calls.clear()
    return session, vault, account, calls


@pytest.mark.parametrize(("media", "upstream"), [
    ("kepub", "KEPUB"),
    ("epub", "EPUB3"),
    ("epub", "EPUB3FL"),
])
def test_content_access_kdrm_uses_bound_legacy_runtime_not_http(
    tmp_path, monkeypatch, media, upstream,
):
    session, _vault, account, calls = legacy_setup(media, upstream)
    monkeypatch.setattr(
        HTTPX_TRANSPORT, "attested_https_download",
        lambda **kw: pytest.fail("KDRM must not be delivered as HTTP plaintext"),
    )
    seen = []

    def runtime(request):
        assert request["source"]["type"] == "kobo"
        assert request["source"]["provider"] == "kobo"
        assert "candidate" not in request["source"]
        assert request["source"]["selection"] == {
            "drm_type": "KDRM", "url_format": upstream,
        }
        assert request["source"]["kdrm"] == {
            "device_id": "device-synthetic", "user_id": "user-synthetic",
        }
        assert request["source"]["request"]["headers"]["Authorization"].startswith("Bearer ")
        assert "ContentKeys" not in str(request)
        assert request["format"] == "epub"
        destination = Path(request["destination"])
        destination.write_bytes(epub_bytes())
        seen.append(destination)
        return {"ok": True, "artifact": {"path": str(destination), "format": "epub"}}

    try:
        offer = selected(session, account, "book-1")
        assert offer.delivery == "available"
        assert offer.delivery_offer_id.startswith("kobo.runtime:")
        assert calls == [("book-1", "descriptor"), ("book-1", "delegated")]
        result = deliver(session, account, "book-1", offer.delivery_offer_id,
                         tmp_path, runtime)
        assert result.path == seen[0] and result.path.read_bytes() == epub_bytes()
        assert not result.authorizes_publication
        assert calls == [
            ("book-1", "descriptor"), ("book-1", "delegated"),
            ("book-1", "descriptor"), ("book-1", "delegated"),
        ]
    finally:
        session.close()


def test_legacy_replay_requires_an_explicit_runtime_mode_and_no_delegation_post():
    session, _vault, account, calls = legacy_setup(
        runtime_modes=("kobo_kdrm",),
    )
    try:
        assert kdrm_delegated_runtime_ready(session) is False
        row = selected(session, account, "book-1")
        assert row.delivery == "unknown" and not row.delivery_offer_id
        assert calls == [("book-1", "descriptor")]
    finally:
        session.close()


@pytest.mark.parametrize("malformed", ["foreign", "wrong_format", "ambiguous", "keys"])
def test_legacy_replay_refuses_invalid_or_unbound_delegated_evidence(malformed):
    session, _vault, account, calls = legacy_setup(malformed=malformed)
    try:
        row = selected(session, account, "book-1")
        assert row.delivery == "unknown" and row.delivery_offer_id == ""
        assert calls == [("book-1", "descriptor"), ("book-1", "delegated")]
    finally:
        session.close()


def test_legacy_replay_does_not_override_pinned_library_sync():
    pinned = kdrm_candidate("book-1")
    session, vault, account, calls = legacy_setup(siblings=[pinned])
    try:
        offer = selected(session, account, "book-1")
        pinned_id = exact_kobo_offer_id(
            descriptor(product="book-1", candidate=pinned),
            identity_id="identity-A", credential_id="synthetic-card",
            account_id="alice", product_id="book-1",
            kdrm_runtime_ready=True, kdrm_delegated_ready=True,
        )
        assert offer.delivery == "available"
        assert offer.delivery_offer_id == pinned_id
        assert calls == [("book-1", "descriptor"), ("book-1", "delegated")]
    finally:
        session.close()


def test_legacy_replay_does_not_hide_plain_download():
    plain = descriptor(product="book-1")["acquisition"]["candidates"][0]
    session, vault, account, calls = legacy_setup(siblings=[plain])

    def descriptor_only(product, **options):
        assert options["mode"] == "descriptor"
        calls.append((product, "descriptor"))
        data = descriptor(product=product, candidate=legacy_candidate(product))
        data["acquisition"]["candidates"].append(deepcopy(plain))
        return data

    vault.acquisition = descriptor_only
    try:
        offer = selected(session, account, "book-1")
        assert offer.delivery == "available"
        assert offer.delivery_offer_id.startswith("kobo.descriptor:")
        assert calls == [("book-1", "descriptor")]
    finally:
        session.close()


def test_two_independent_legacy_routes_are_not_arbitrarily_selected():
    other = legacy_candidate("book-1", "epub", "EPUB3")
    session, _vault, account, calls = legacy_setup(siblings=[other])
    try:
        row = selected(session, account, "book-1")
        assert row.delivery == "unknown"
        assert row.delivery_offer_id == ""
        assert calls == [("book-1", "descriptor")]
    finally:
        session.close()
