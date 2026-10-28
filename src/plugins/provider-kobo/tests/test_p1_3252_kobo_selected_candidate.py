"""#3252 Kobo real session selected delivery with synthetic Vault and HTTPX.

No live account, internet, signed credentials, DRM service or Core publication.
The content GET uses the public-client construction interface under test.
"""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import zipfile

import httpx
import pytest

from cifereca.plugin_api import HTTPX_TRANSPORT, ProviderCapabilityError, ProviderError
from tests.test_p1_3252_kobo_selected_delivery_offer import (
    descriptor, setup, SIGNED,
)
from tests.test_p1_3252_rights_observation_snapshot import OwnerBroker


def epub_bytes():
    out = BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", (
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles>'
            '</container>'
        ))
        archive.writestr("OPS/book.opf", "<package/>")
    return out.getvalue()


ACSM = (
    b'<?xml version="1.0"?><fulfillmentToken '
    b'xmlns="http://ns.adobe.com/adept"><resourceItemInfo>'
    b'<type>application/epub+zip</type></resourceItemInfo></fulfillmentToken>'
)


def fake_public_client(monkeypatch, handler, built):
    def public_client(*, producer, timeout):
        built.append((producer, timeout))
        return httpx.Client(
            transport=httpx.MockTransport(handler),
            trust_env=False, follow_redirects=False,
        )
    monkeypatch.setattr(HTTPX_TRANSPORT, "public_client", public_client)


def admitted(session, account):
    return session.rights_selected_delivery_offer_snapshot(
        account, edition_id="book-1", right_id="book-1",
        kind="purchase", branch="personal_manifest",
        explicit_user_action=True,
    ).rights[0].delivery_offer_id


def execute(session, account, offer, tmp_path, **changed):
    opts = dict(
        edition_id="book-1", right_id="book-1",
        kind="purchase", branch="personal_manifest",
        delivery_offer_id=offer, target_dir=tmp_path,
        explicit_user_action=True,
    )
    opts.update(changed)
    return session.rights_selected_delivery_candidate(account, **opts)


def test_exact_kobo_epub_get_only_after_fresh_descriptor(tmp_path, monkeypatch):
    session, vault, broker, account, posts = setup()
    requests, clients = [], []
    payload = epub_bytes()
    def handler(request):
        requests.append((request.method, str(request.url), dict(request.headers)))
        return httpx.Response(
            200, content=payload, headers={"content-type": "application/epub+zip"},
        )
    fake_public_client(monkeypatch, handler, clients)
    try:
        offer = admitted(session, account)
        assert len(posts) == 1
        path, ref = execute(session, account, offer, tmp_path)
        assert path.parent == tmp_path and path.is_file()
        assert path.read_bytes() == payload
        assert path.suffix == ".epub" and ref == ""
        assert [x[0:2] for x in requests] == [("GET", SIGNED)]
        assert not requests[0][2].get("authorization")
        assert not requests[0][2].get("cookie")
        assert len(posts) == 2 and vault.calls == 2
        assert clients == [("provider.kobo.delivery", 30)]
    finally:
        session.close()


def test_rotated_signed_url_revalidates_opaque_offer(tmp_path, monkeypatch):
    session, vault, broker, account, posts = setup()
    fresh = SIGNED.replace("SYNTHETIC_PRIVATE_SIGNATURE_DO_NOT_EXPORT", "ROTATED")
    seen, clients = [], []
    fake_public_client(monkeypatch, lambda request: (
        seen.append(str(request.url)) or httpx.Response(
            200, content=epub_bytes(),
        )
    ), clients)
    try:
        offer = admitted(session, account)
        def rotated(product_id, **kw):
            posts.append((product_id, kw))
            item = descriptor()
            item["acquisition"]["candidates"][0]["url"] = fresh
            return item
        vault.acquisition = rotated
        candidate, source = execute(session, account, offer, tmp_path)
        assert candidate.exists() and source == ""
        assert seen == [fresh]
    finally:
        session.close()


def test_selected_kobo_acsm_staged_without_adept(tmp_path, monkeypatch):
    session, vault, broker, account, posts = setup()
    def adobe(product_id, **kw):
        posts.append((product_id, kw))
        return descriptor(candidate={
            "kind": "acsm", "handling": "adobe_adept",
            "transport": "https", "method": "GET", "url": SIGNED,
            "media_format": "acsm",
            "drm": {"raw_type": "AdobeDrm"},
        })
    vault.acquisition = adobe
    calls, clients = [], []
    fake_public_client(monkeypatch, lambda request: (
        calls.append(str(request.url)) or httpx.Response(
            200, content=ACSM, headers={
                "content-type": "application/vnd.adobe.adept+xml",
            },
        )
    ), clients)
    try:
        offer = admitted(session, account)
        received, ref = execute(session, account, offer, tmp_path)
        assert received.suffix == ".acsm"
        assert received.read_bytes() == ACSM
        assert ref == "" and calls == [SIGNED]
    finally:
        session.close()


@pytest.mark.parametrize("wrong", [
    {"explicit_user_action": False},
    {"edition_id": "book-2"},
    {"right_id": "book-2"},
    {"kind": "loan"},
    {"branch": "bnf_html"},
    {"delivery_offer_id": "kobo.descriptor:" + "0" * 64},
])
def test_wrong_selection_or_offer_refused_without_content_get(
    tmp_path, monkeypatch, wrong,
):
    session, vault, broker, account, posts = setup()
    clients = []
    fake_public_client(monkeypatch, lambda request: pytest.fail(
        "unexpected HTTP GET",
    ), clients)
    try:
        offer = admitted(session, account)
        with pytest.raises((ProviderCapabilityError, ProviderError)):
            execute(session, account, offer, tmp_path, **wrong)
        assert clients == [] and not list(tmp_path.iterdir())
    finally:
        session.close()


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"location": "https://other.invalid/epub"}),
    httpx.Response(403, content=b"forbidden"),
    httpx.Response(200, content=b"<html>login</html>",
                   headers={"content-type": "text/html"}),
    httpx.Response(200, content=b"not-a-book"),
    httpx.Response(200, content=b"oversize",
                   headers={"content-length": str(129 * 1024 * 1024)}),
])
def test_failed_get_never_leaves_any_candidate(
    tmp_path, monkeypatch, response,
):
    session, vault, broker, account, posts = setup()
    seen, clients = [], []
    fake_public_client(monkeypatch, lambda request: (
        seen.append(str(request.url)) or response
    ), clients)
    try:
        offer = admitted(session, account)
        with pytest.raises(ProviderCapabilityError):
            execute(session, account, offer, tmp_path)
        assert seen == [SIGNED]
        assert not list(tmp_path.iterdir())
    finally:
        session.close()


def test_owner_rebound_during_get_drops_staged_bytes(tmp_path, monkeypatch):
    session, vault, broker, account, posts = setup()
    seen, clients = [], []
    def handler(request):
        seen.append(str(request.url))
        broker.identities = ("identity-B",)
        return httpx.Response(200, content=epub_bytes())
    fake_public_client(monkeypatch, handler, clients)
    try:
        offer = admitted(session, account)
        with pytest.raises((ProviderCapabilityError, ProviderError)):
            execute(session, account, offer, tmp_path)
        assert seen == [SIGNED] and not list(tmp_path.iterdir())
    finally:
        session.close()


def test_broker_aba_after_fresh_descriptor_prevents_any_get(
    tmp_path, monkeypatch,
):
    session, vault, broker, account, posts = setup()
    clients = []
    fake_public_client(monkeypatch, lambda request: pytest.fail(
        "no delivery after broker instance replacement",
    ), clients)
    try:
        offer = admitted(session, account)
        def rebound(product_id, **kw):
            posts.append((product_id, kw))
            session.identity_broker = OwnerBroker()
            return descriptor()
        vault.acquisition = rebound
        with pytest.raises((ProviderCapabilityError, ProviderError)):
            execute(session, account, offer, tmp_path)
        assert clients == [] and not list(tmp_path.iterdir())
    finally:
        session.close()
