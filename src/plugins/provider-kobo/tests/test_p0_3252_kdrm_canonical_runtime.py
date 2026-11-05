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
    # The pre-existing KDRM suite starts from a synthetic connected session
    # rather than connect(). Model the new exact bearer-scoped attestation
    # explicitly; the actual handshake is tested separately in the P0 scope
    # matrix. Neither credential nor token ever leaves this in-memory fake.
    from cifereca_kobo import _vault_token_fingerprint
    vault.token = "SYNTHETIC_ATTESTED_BEARER"
    session.supports_acquisition_delegation = True
    session._delegation_bound_client = vault
    session._delegation_bound_generation = session._personal_binding_generation
    session._delegation_token_fingerprint = _vault_token_fingerprint(vault)
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
    emitted_epubs = []

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
        # ZIP member timestamps can change across the next clock tick.
        # Compare the actual runtime bytes, not a freshly rebuilt fixture.
        emitted = epub_bytes()
        path.write_bytes(emitted)
        delivered.append(path)
        emitted_epubs.append(emitted)
        return {"ok": True, "artifact": {"path": str(path), "format": "epub"}}

    try:
        for product in products:
            offer = selected(session, account, product)
            assert offer.delivery == "available" and offer.provider_attested
            assert offer.delivery_offer_id.startswith("kobo.runtime:")
            own_dir = tmp_path / product
            own_dir.mkdir()
            out = deliver(session, account, product,
                          offer.delivery_offer_id, own_dir, runtime)
            assert out.path == delivered[-1] and out.path.read_bytes() == emitted_epubs[-1]
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
    assert offer.startswith("kobo.runtime:")
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
    from cifereca_kobo_provider_kdrm import scoped_kdrm_source

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


def test_kdrm_opaque_offer_pins_flepub_vs_epub_upstream_route():
    candidate = kdrm_candidate("book-1")
    candidate["media_format"] = "epub"
    candidate["source_format"] = "FLEPUB"
    def qualify(row):
        return exact_kobo_offer_id(
            descriptor(product="book-1", candidate=row),
            identity_id="identity-A", credential_id="synthetic-card",
            account_id="alice", product_id="book-1", kdrm_runtime_ready=True,
        )
    pinned = qualify(candidate)
    assert pinned.startswith("kobo.runtime:")
    from copy import deepcopy
    modified = deepcopy(candidate)
    modified["source_format"] = "EPUB"
    other = qualify(modified)
    assert other.startswith("kobo.runtime:")
    assert pinned != other


@pytest.mark.parametrize("container", ("top", "acquisition", "delegation", "request", "context"))
@pytest.mark.parametrize("field,value", (
    ("provider", "another-supplier"),
    ("source_provider", "another-supplier"),
    ("provider", True),
))
def test_kdrm_delegated_recipe_rejects_foreign_or_malformed_provider_at_any_level(
    container, field, value,
):
    """A later delegated response cannot contradict the attested Kobo offer."""
    from cifereca_kobo_provider_kdrm import scoped_kdrm_source

    payload = delegated("book-1")
    delegation = payload["acquisition"]["delegation"]
    target = {
        "top": payload,
        "acquisition": payload["acquisition"],
        "delegation": delegation,
        "request": delegation["content_access_request"],
        "context": delegation["session_context"],
    }[container]
    target[field] = value
    assert scoped_kdrm_source(
        payload, kdrm_candidate("book-1"),
        product_id="book-1", account_id="alice",
        identity_id="identity-A", credential_id="synthetic-card",
    ) is None


def test_foreign_delegated_provider_never_starts_kdrm_runtime_or_plain_http(
    tmp_path, monkeypatch,
):
    session, vault, broker, account, _ = setup()
    calls = ready(session, vault)
    original = vault.acquisition

    def foreign(product, **kwargs):
        payload = original(product, **kwargs)
        if kwargs.get("mode") == "delegated":
            payload["provider"] = "foreign-provider"
        return payload

    monkeypatch.setattr(
        HTTPX_TRANSPORT, "attested_https_download",
        lambda **kwargs: pytest.fail("foreign provider must not GET plaintext"),
    )
    runtime_calls = []

    def forbidden_runtime(request):
        runtime_calls.append(request)
        pytest.fail("foreign provider must not execute DRM")

    try:
        # The first exact selection was legitimate. A different delegated
        # producer must still be rejected by the later delivery boundary.
        observed = selected(session, account, "book-1")
        assert observed.delivery == "available"
        vault.acquisition = foreign
        with pytest.raises(ProviderCapabilityError, match="kdrm_recipe_unavailable"):
            deliver(session, account, "book-1", observed.delivery_offer_id,
                    tmp_path, forbidden_runtime)
        assert runtime_calls == [] and list(tmp_path.iterdir()) == []
        assert any(mode == "delegated" for _, mode in calls)
    finally:
        session.close()


def test_kobo_attested_offer_route_is_local_and_per_product():
    session, vault, broker, account, _ = setup()
    ready(session, vault)
    try:
        runtime_offer = selected(session, account, "book-1").delivery_offer_id
        assert runtime_offer.startswith("kobo.runtime:")
        assert session.downloadable_selected_delivery_requires_runtime(
            account, edition_id="book-1", resource_id="book-1",
            kind="purchase", branch="personal_manifest",
            selected_offer_id=runtime_offer,
        ) is True
        with pytest.raises(ProviderCapabilityError, match="route_unconfirmed"):
            session.downloadable_selected_delivery_requires_runtime(
                account, edition_id="another-book", resource_id="book-1",
                kind="purchase", branch="personal_manifest",
                selected_offer_id=runtime_offer,
            )
        with pytest.raises(ProviderCapabilityError, match="route_unconfirmed"):
            session.downloadable_selected_delivery_requires_runtime(
                account, edition_id="book-1", resource_id="book-1",
                kind="purchase", branch="personal_manifest",
                selected_offer_id="kobo.runtime:tampered",
            )
    finally:
        session.close()


def test_kdrm_offer_cannot_be_reinterpreted_as_plain_https(
    tmp_path, monkeypatch,
):
    session, vault, broker, account, _ = setup()
    ready(session, vault)
    monkeypatch.setattr(HTTPX_TRANSPORT, "attested_https_download",
                        lambda **_: pytest.fail("KDRM must not GET plaintext"))
    try:
        runtime_offer = selected(session, account, "book-1").delivery_offer_id
        forged_direct = "kobo.descriptor:" + runtime_offer.split(":", 1)[1]
        assert session.downloadable_selected_delivery_requires_runtime(
            account, edition_id="book-1", resource_id="book-1",
            kind="purchase", branch="personal_manifest",
            selected_offer_id=forged_direct,
        ) is False
        with pytest.raises(ProviderCapabilityError):
            session.downloadable_selected_delivery_candidate_guarded(
                account, edition_id="book-1", resource_id="book-1",
                kind="purchase", branch="personal_manifest",
                selected_offer_id=forged_direct, target_dir=tmp_path,
                explicit_user_action=True, admission_current=lambda: True,
            )
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


def test_plain_offer_remains_direct_even_on_runtime_capable_kobo_session():
    session, vault, broker, account, _ = setup()
    session.runtime_capabilities = NS(
        acquisition=lambda provider: NS(
            available=True, compatible=True, operational=True,
            modes=("kobo_kdrm",),
        )
    )
    try:
        direct = selected(session, account, "book-1").delivery_offer_id
        assert direct.startswith("kobo.descriptor:")
        assert session.downloadable_selected_delivery_requires_runtime(
            account, edition_id="book-1", resource_id="book-1",
            kind="purchase", branch="personal_manifest",
            selected_offer_id=direct,
        ) is False
    finally:
        session.close()


def test_kvault_content_access_kdrm_does_not_hide_exact_pinned_runtime_offer(
    tmp_path, monkeypatch,
):
    """KVault can return content_access_book KDRM BEFORE library_sync KDRM.

    Only the latter is an attested pinned candidate; the first must not
    poison the selected delegated content-key replay or staging.
    """
    session, vault, _broker, account, _ = setup()
    calls = ready(session, vault)
    original = vault.acquisition

    def both_candidates(product, **options):
        response = original(product, **options)
        if options.get("mode") == "descriptor":
            unpinned = kdrm_candidate(product)
            unpinned["kobo"] = {
                "source": "content_access_book",
                "display_profile": "Android",
            }
            response["acquisition"]["candidates"].insert(0, unpinned)
        return response

    vault.acquisition = both_candidates
    monkeypatch.setattr(
        HTTPX_TRANSPORT, "attested_https_download",
        lambda **_kw: pytest.fail("KDRM must not become plaintext HTTP"),
    )
    runtime_calls = []

    def runtime(request):
        runtime_calls.append(request)
        assert request["source"]["candidate"]["delivery_source"] == "library_sync"
        assert request["source"]["candidate"]["drm_type"] == "KDRM"
        path = Path(request["destination"])
        path.write_bytes(epub_bytes())
        return {"ok": True, "artifact": {"path": str(path), "format": "epub"}}

    try:
        offer = selected(session, account, "book-1")
        assert offer.delivery == "available"
        assert offer.delivery_offer_id.startswith("kobo.runtime:")
        assert calls == [("book-1", "descriptor"), ("book-1", "delegated")]
        delivered = deliver(session, account, "book-1", offer.delivery_offer_id,
                            tmp_path, runtime)
        assert delivered.path.read_bytes() == epub_bytes()
        assert len(runtime_calls) == 1
        assert calls == [
            ("book-1", "descriptor"), ("book-1", "delegated"),
            ("book-1", "descriptor"), ("book-1", "delegated"),
        ]
    finally:
        session.close()


def test_plain_offer_not_rejected_by_unrelated_kdrm_advertisement():
    """No delegated POST if the exact qualified offer is plain, not runtime."""
    session, vault, _broker, account, _ = setup()
    calls = ready(session, vault)

    def mixed(product, **options):
        calls.append((product, options.get("mode")))
        if options.get("mode") != "descriptor":
            pytest.fail("Plain offer must never ask Vault for delegated mode")
        unpinned = kdrm_candidate(product)
        unpinned["kobo"] = {"source": "content_access_book"}
        plain = descriptor(product=product)["acquisition"]["candidates"][0]
        result = descriptor(product=product, candidate=unpinned)
        result["acquisition"]["candidates"].append(plain)
        return result

    vault.acquisition = mixed
    calls.clear()
    try:
        offer = selected(session, account, "book-1")
        assert offer.delivery == "available"
        assert offer.delivery_offer_id.startswith("kobo.descriptor:")
        assert calls == [("book-1", "descriptor")]
    finally:
        session.close()


def test_multiple_pinned_library_sync_kdrm_routes_remain_ambiguous():
    """Two eligible library_sync candidates must not pick an arbitrary URL."""
    session, vault, _broker, account, _ = setup()
    calls = ready(session, vault)
    original = vault.acquisition

    def ambiguous(product, **options):
        response = original(product, **options)
        if options.get("mode") == "descriptor":
            second = kdrm_candidate(product)
            second["url"] = second["url"].replace(".kepub", "-copy.kepub")
            response["acquisition"]["candidates"].append(second)
        return response

    vault.acquisition = ambiguous
    try:
        offer = selected(session, account, "book-1")
        assert offer.delivery == "unknown"
        assert offer.delivery_offer_id == ""
        assert calls == [("book-1", "descriptor")]
    finally:
        session.close()


@pytest.mark.parametrize(("raw_type", "accepted"), [
    ("KDRM", True),
    ("KoboDRM", True),
    ("kobo_drm", True),
    ("Kobo-Drm", True),
    ("Kobo DRM", True),
    ("KoboDRMPreview", False),
    ("AdobeDrm", False),
    ("unknown", False),
    ("KoboDRMSAMPLE", False),
])
def test_kvault_kobo_drm_raw_aliases_preserve_only_exact_pinned_route(
    raw_type, accepted, tmp_path, monkeypatch,
):
    """Worker normalizes KoboDRM but preserves the upstream raw DRMType.

    A positive scope, eligible family/protection and pinned library_sync
    candidate are still mandatory; only the spelling of an attested KDRM
    may vary. No unpinned/foreign DRM is upgraded.
    """
    session, vault, _broker, account, _ = setup()
    calls = ready(session, vault)
    original = vault.acquisition
    replay_params = []

    def upstream(product, **options):
        answer = original(product, **options)
        if options.get("mode") == "descriptor":
            candidate = answer["acquisition"]["candidates"][0]
            candidate["drm"]["raw_type"] = raw_type
            # KVault includes this legacy duplicate of the same raw_type.
            candidate["drm_type"] = raw_type
        elif options.get("mode") == "delegated":
            replay_params.append(options)
        return answer

    vault.acquisition = upstream
    monkeypatch.setattr(
        HTTPX_TRANSPORT, "attested_https_download",
        lambda **_kwargs: pytest.fail("never GET protected KDRM as plaintext"),
    )
    runtime_calls = []

    def runtime(request):
        runtime_calls.append(request)
        assert request["source"]["candidate"]["drm_type"] == "KDRM"
        assert request["source"]["candidate"]["delivery_source"] == "library_sync"
        out = Path(request["destination"])
        out.write_bytes(epub_bytes())
        return {"ok": True, "artifact": {"path": str(out), "format": "epub"}}

    try:
        row = selected(session, account, "book-1")
        assert (row.delivery == "available") is accepted
        assert bool(row.delivery_offer_id) is accepted
        if accepted:
            assert row.delivery_offer_id.startswith("kobo.runtime:")
            received = deliver(session, account, "book-1",
                               row.delivery_offer_id, tmp_path, runtime)
            assert received.path.read_bytes() == epub_bytes()
            assert len(runtime_calls) == 1
            assert len(replay_params) == 2
            assert all(p["include_acsm"] is False for p in replay_params)
            assert calls == [
                ("book-1", "descriptor"), ("book-1", "delegated"),
                ("book-1", "descriptor"), ("book-1", "delegated"),
            ]
        else:
            assert row.delivery == "unknown"
            assert calls == [("book-1", "descriptor")]
            assert replay_params == []
            assert runtime_calls == []
    finally:
        session.close()


@pytest.mark.parametrize("adobe_capability", [False, True])
def test_kdrm_explicit_acsm_opt_out_reaches_vault_even_if_adobe_runtime_missing(
    adobe_capability,
):
    session, vault, _broker, account, _ = setup()
    ready(session, vault)
    session.supports_acquisition_acsm = adobe_capability
    calls = []
    original = vault.acquisition

    def capture(product, **options):
        calls.append(dict(options))
        return original(product, **options)

    vault.acquisition = capture
    try:
        offered = selected(session, account, "book-1")
        assert offered.delivery == "available"
        assert [x["mode"] for x in calls] == ["descriptor", "delegated"]
        assert calls[1]["include_acsm"] is False
        # A descriptor is still allowed to request ACSM if an Adobe runtime
        # is installed, but may not force ACSM when no runtime is installed.
        assert calls[0]["include_acsm"] is adobe_capability
    finally:
        session.close()


@pytest.mark.parametrize("drm_first", [True, False])
def test_independent_drm_entrypoint_cannot_shadow_provider_kdrm_helper(
    monkeypatch, drm_first,
):
    """The separately installed DRM plugin owns the old top-level name.

    Simulate its real import cache entry in both activation orders. The
    provider's selected reader and candidate staging must use its OWN helper.
    All account/descriptor responses are synthetic; no external Kobo I/O.
    """
    import importlib
    import sys
    from types import ModuleType

    root = Path(__file__).resolve().parents[1]
    if not drm_first:
        importlib.import_module("cifereca_kobo_provider_kdrm")

    drm_entrypoint = ModuleType("cifereca_kobo_kdrm")
    drm_entrypoint.__file__ = "/synthetic/drm-kobo-kdrm/cifereca_kobo_kdrm.py"
    drm_entrypoint.PLUGIN = object()
    monkeypatch.setitem(sys.modules, "cifereca_kobo_kdrm", drm_entrypoint)

    helper = importlib.import_module("cifereca_kobo_provider_kdrm")
    assert Path(helper.__file__).resolve() == root / "cifereca_kobo_provider_kdrm.py"
    assert callable(helper.scoped_kdrm_source)
    assert not (root / "cifereca_kobo_kdrm.py").exists()

    session, vault, broker, account, posts = setup()
    try:
        offer = session.rights_delivery_offer_snapshot(
            account, "book-1", explicit_user_action=True,
        )
        assert offer.rights[0].provider_attested is True
        assert offer.rights[0].delivery == "available"
        assert len(posts) == 1
    finally:
        session.close()

    from cifereca_kobo_selected_candidate import scoped_kdrm_source
    assert scoped_kdrm_source is helper.scoped_kdrm_source
    assert sys.modules["cifereca_kobo_kdrm"] is drm_entrypoint
