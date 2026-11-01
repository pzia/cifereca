"""#3867 — Kobo exact selected GET may follow unrelated public HTTPS hosts.

Only synthetic HTTPX responses, synthetic Kobo Vault descriptor and EPUB
bytes. The Core public-client interface is mocked, not its TCP pinning.
"""
from __future__ import annotations

import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_p1_3252_kobo_selected_candidate import (
    ACSM, admitted, execute, epub_bytes, fake_public_client,
)
from tests.test_p1_3252_kobo_selected_delivery_offer import (
    SIGNED, descriptor, setup,
)

PARTNER = "https://new-book-distributor.org/secure/fetch?key=SYNTHETIC%2FKEEP"
SECOND = "https://some-publisher.fr/delivery/reader.epub"


@pytest.mark.parametrize("first_status", [302, 307, 308])
@pytest.mark.parametrize("two_hops", [False, True])
def test_exact_kobo_offer_follows_public_supplier_chain_without_leaking_vault(
    tmp_path, monkeypatch, first_status, two_hops,
):
    session, vault, broker, account, posts = setup()
    requests = []
    payload = epub_bytes()

    def handler(req):
        requests.append(req)
        url = str(req.url)
        if url == SIGNED:
            return httpx.Response(
                first_status, headers={"location": PARTNER, "set-cookie": "fixture=SECRET"},
            )
        if url == PARTNER:
            if two_hops:
                return httpx.Response(
                    302, headers={"location": SECOND, "set-cookie": "fixture2=SECRET"},
                )
            return httpx.Response(
                200, content=payload, headers={"content-type": "application/epub+zip"},
            )
        if url == SECOND and two_hops:
            return httpx.Response(
                200, content=payload, headers={"content-type": "application/epub+zip"},
            )
        pytest.fail("unexpected or replayed supplier GET")

    constructors = []
    fake_public_client(monkeypatch, handler, constructors)
    try:
        offer = admitted(session, account)
        staged, hint = execute(session, account, offer, tmp_path)
        assert staged.parent == tmp_path
        assert staged.read_bytes() == payload and hint == ""
        assert [str(r.url) for r in requests] == (
            [SIGNED, PARTNER, SECOND] if two_hops else [SIGNED, PARTNER]
        )
        assert constructors == [("provider.kobo.delivery", 30)]
        assert len(posts) == 2  # one selected offer, one fresh Vault revalidation
        assert all(req.method == "GET" for req in requests)
        assert all(
            not req.headers.get("cookie") and not req.headers.get("authorization")
            and not req.headers.get("referer") and not req.headers.get("origin")
            for req in requests
        )
    finally:
        session.close()


def test_exact_acsm_route_stages_after_public_redirect_without_adept(
    tmp_path, monkeypatch,
):
    session, vault, broker, account, posts = setup()

    def adobe(product_id, **kwargs):
        posts.append((product_id, kwargs))
        return descriptor(candidate={
            "kind": "acsm", "handling": "adobe_adept", "transport": "https",
            "method": "GET", "url": SIGNED, "media_format": "acsm",
            "drm": {"raw_type": "AdobeDrm"},
        })

    vault.acquisition = adobe
    requests = []

    def handler(req):
        requests.append(str(req.url))
        if str(req.url) == SIGNED:
            return httpx.Response(302, headers={"location": PARTNER})
        assert str(req.url) == PARTNER
        return httpx.Response(
            200, content=ACSM,
            headers={"content-type": "application/vnd.adobe.adept+xml"},
        )

    fake_public_client(monkeypatch, handler, [])
    try:
        offer = admitted(session, account)
        path, hint = execute(session, account, offer, tmp_path)
        assert path.suffix == ".acsm" and path.read_bytes() == ACSM
        assert requests == [SIGNED, PARTNER] and hint == ""
    finally:
        session.close()


@pytest.mark.parametrize("target", [
    "http://new-book-distributor.org/file",
    "https://127.0.0.1/local",
    "https://user:secret@new-book-distributor.org/file",
    "https://new-book-distributor.org/file#part",
    "https://new-book-distributor.org\\unsafe",
])
def test_untrusted_first_redirect_is_refused_before_second_get(
    tmp_path, monkeypatch, target,
):
    session, vault, broker, account, posts = setup()
    requested = []

    def handler(request):
        requested.append(str(request.url))
        assert str(request.url) == SIGNED
        return httpx.Response(302, headers={"location": target})

    fake_public_client(monkeypatch, handler, [])
    try:
        offer = admitted(session, account)
        with pytest.raises(ProviderCapabilityError):
            execute(session, account, offer, tmp_path)
        assert requested == [SIGNED]
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


def test_replayed_redirect_loop_is_refused_without_third_get(tmp_path, monkeypatch):
    session, vault, broker, account, posts = setup()
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(
            302, headers={"location": PARTNER if str(request.url) == SIGNED else SIGNED},
        )

    fake_public_client(monkeypatch, handler, [])
    try:
        offer = admitted(session, account)
        with pytest.raises(ProviderCapabilityError, match="kobo_candidate_redirect_loop"):
            execute(session, account, offer, tmp_path)
        assert requests == [SIGNED, PARTNER]
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


def test_owner_change_on_302_refuses_next_supplier_get(tmp_path, monkeypatch):
    session, vault, broker, account, posts = setup()
    requested = []

    def handler(request):
        requested.append(str(request.url))
        assert str(request.url) == SIGNED
        broker.identities = ("identity-B",)
        return httpx.Response(302, headers={"location": PARTNER})

    fake_public_client(monkeypatch, handler, [])
    try:
        offer = admitted(session, account)
        with pytest.raises(ProviderCapabilityError, match="kobo_candidate_owner_changed"):
            execute(session, account, offer, tmp_path)
        assert requested == [SIGNED]
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


def test_public_302_exceeding_max_hops_does_not_publish(
    tmp_path, monkeypatch,
):
    session, vault, broker, account, posts = setup()
    requests = []
    def handler(request):
        requests.append(str(request.url))
        assert len(requests) <= 9
        return httpx.Response(
            302, headers={"location": f"https://new-book-distributor.org/hop/{len(requests)}"},
        )
    fake_public_client(monkeypatch, handler, [])
    try:
        offer = admitted(session, account)
        with pytest.raises(ProviderCapabilityError, match="kobo_candidate_redirect_limit"):
            execute(session, account, offer, tmp_path)
        assert len(requests) == 9
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()
