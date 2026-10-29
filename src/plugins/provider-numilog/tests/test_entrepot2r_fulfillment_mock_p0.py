"""#1844: an entirely synthetic Numilog ACSM delivery across HTTP hops.

The URL resembles the observed origin but contains no real order/auth data.
MockTransport never opens a network connection, and the broker is fake.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest

from cifereca.plugin_api import Account, AccountRef, ProviderCapabilityError, ProviderError
from cifereca_numilog import NumilogSession
from tests.test_entrepot2r_acsm_origin_p0 import SIGNED, Diagnostics


ACSM = (
    b'<?xml version="1.0"?>'
    b'<fulfillmentToken xmlns="http://ns.adobe.com/adept">'
    b'<resourceItemInfo/></fulfillmentToken>'
)
START = "https://www.numilog.com/Pages/Compte/readBook.ashx?lid=12&m=download"


class Acquisition:
    def __init__(self, directory, *, status="complete"):
        self.directory = Path(directory)
        self.status = status
        self.calls = []
        self.staging = []

    def import_acsm(self, path, **options):
        staged = Path(path)
        assert staged.is_file()
        assert staged.suffix == ".acsm"
        assert staged.read_bytes() == ACSM
        self.calls.append(options)
        self.staging.append(staged)
        if self.status == "attention_required":
            return {"status": self.status, "code": "adept_profile_required"}
        if self.status == "failed":
            return {"status": self.status}
        final = self.directory / "synthetic-fulfilled.epub"
        final.write_bytes(b"epub-produced-by-fake-broker")
        return {"status": "complete", "path": str(final)}


def make_session(handler, output_dir, *, status="complete", identities=("reader",)):
    broker = Acquisition(output_dir, status=status)
    session = NumilogSession(
        credential_id="synthetic", label="Synthetic reader",
        credentials={"email": "reader@example.test", "password": "not-a-real-password"},
        options={}, config={}, diagnostics=Diagnostics(),
        acquisition_broker=broker,
    )
    account = "numilog:" + hashlib.sha256(b"synthetic").hexdigest()[:20]
    session.identity_broker = SimpleNamespace(accounts=lambda identity_id=None, **_kwargs: tuple(
        AccountRef(
            account_id=account, identity_id=identity,
            provider_id="numilog", label="Fixture",
        )
        for identity in identities
    ))
    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False, trust_env=False,
    )
    session._accounts = [Account(provider_id="numilog", credential_id=session.credential_id,
                                 unique_id=account, identity_id="reader")]
    return session, broker


@pytest.mark.parametrize("via_html", (False, True))
def test_realistic_fictional_fulfillment_keeps_query_and_calls_broker_once(
    tmp_path, via_html,
):
    seen = []
    def handler(request):
        seen.append((request.method, str(request.url)))
        if request.url.path.endswith("/readBook.ashx"):
            if via_html:
                return httpx.Response(
                    200, headers={"content-type": "text/html"},
                    text=f'<script>window.location = "{SIGNED}";</script>',
                )
            return httpx.Response(302, headers={"location": SIGNED})
        if str(request.url) == SIGNED:
            return httpx.Response(
                200, headers={"content-type": "application/vnd.adobe.adept+xml"},
                content=ACSM,
            )
        pytest.fail("unexpected synthetic HTTP destination")

    session, broker = make_session(handler, tmp_path)
    try:
        result, source = session._download(
            START, tmp_path / "proposed.epub", title="Synthetic book"
        )
        assert source == session.library_url  # never leak the signed URL
        assert result.is_file() and result.name == "synthetic-fulfilled.epub"
        assert seen == [("GET", START), ("GET", SIGNED)]
        assert urlsplit(seen[1][1]).query == urlsplit(SIGNED).query
        assert broker.calls == [{
            "identity_id": "reader",
            "provider_id": "numilog",
            "title": "Synthetic book",
        }]
        assert len(broker.staging) == 1
        assert not broker.staging[0].exists()
        assert not list(tmp_path.glob(".numilog-*.part"))
        assert not list(tmp_path.glob(".numilog-*.acsm"))
    finally:
        session.close()


@pytest.mark.parametrize("status", ("attention_required", "failed"))
def test_broker_failure_remains_unconfirmed_and_cleans_staging(tmp_path, status):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(
            200, headers={"content-type": "application/octet-stream"},
            content=ACSM,
        )
    session, broker = make_session(handler, tmp_path, status=status)
    try:
        with pytest.raises((ProviderError, ProviderCapabilityError)) as caught:
            session._download(SIGNED, tmp_path / "out.epub", title="Synthetic")
        assert len(calls) == 1 and calls[0] == SIGNED
        assert len(broker.calls) == 1
        assert not list(tmp_path.glob(".numilog-*"))
        assert "SYNTHETIC_SIGNATURE" not in str(caught.value)
        assert "FAKE-ORDER" not in str(caught.value)
    finally:
        session.close()


@pytest.mark.parametrize("status", (403, 429))
def test_delivery_http_denial_cannot_invoke_broker_or_leak_signature(tmp_path, status):
    seen = []
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(status, text="SYNTHETIC_SIGNATURE PRIVATE")
    session, broker = make_session(handler, tmp_path)
    try:
        with pytest.raises(ProviderError) as caught:
            session._download(SIGNED, tmp_path / "out.epub")
        assert seen == [SIGNED] and broker.calls == []
        assert "SYNTHETIC_SIGNATURE" not in str(caught.value)
        assert not list(tmp_path.glob(".numilog-*"))
    finally:
        session.close()


@pytest.mark.parametrize("content_type,body", (
    ("application/xml", b"<book>not an ACSM</book>"),
    ("text/html", b"<html><body>No delivery link</body></html>"),
    ("application/pdf", b"%PDF-1.4\nfictional"),
))
def test_wrong_content_does_not_get_imported_or_linger(
    tmp_path, content_type, body,
):
    session, broker = make_session(
        lambda _request: httpx.Response(
            200, headers={"content-type": content_type}, content=body
        ),
        tmp_path,
    )
    try:
        with pytest.raises((ProviderError, ProviderCapabilityError)):
            session._download(SIGNED, tmp_path / "out.epub")
        assert broker.calls == []
        assert not list(tmp_path.glob(".numilog-*"))
    finally:
        session.close()


def test_ambiguous_account_owner_never_invokes_broker(tmp_path):
    session, broker = make_session(
        lambda _request: httpx.Response(
            200, headers={"content-type": "application/xml"}, content=ACSM
        ),
        tmp_path,
        identities=("reader-1", "reader-2"),
    )
    try:
        with pytest.raises(ProviderCapabilityError, match="identité propriétaire"):
            session._download(SIGNED, tmp_path / "out.epub")
        assert broker.calls == []
        assert not list(tmp_path.glob(".numilog-*"))
    finally:
        session.close()


@pytest.mark.parametrize("unsafe", (
    "https://entrepot2r.numilog.com.evil.example/fulfillment/unsafe",
    "http://entrepot2r.numilog.com/fulfillment/unsafe",
    "https://entrepot2r.numilog.com:8443/fulfillment/unsafe",
    "https://user:secret@entrepot2r.numilog.com/fulfillment/unsafe",
))
def test_second_hop_outside_trusted_origin_is_stopped_before_get(tmp_path, unsafe):
    seen = []
    def handler(request):
        seen.append(str(request.url))
        if str(request.url) == START:
            return httpx.Response(302, headers={"location": SIGNED})
        if str(request.url) == SIGNED:
            return httpx.Response(302, headers={"location": unsafe})
        pytest.fail("never fetch an untrusted redirect destination")

    session, broker = make_session(handler, tmp_path)
    try:
        with pytest.raises(ProviderError, match="destination réseau inattendue") as caught:
            session._download(START, tmp_path / "out.epub")
        assert seen == [START, SIGNED]
        assert broker.calls == []
        assert "SYNTHETIC_SIGNATURE" not in str(caught.value)
        assert not list(tmp_path.glob(".numilog-*"))
    finally:
        session.close()


def test_fulfillment_redirect_loop_stops_before_repeated_get(tmp_path):
    seen = []
    def handler(request):
        url = str(request.url)
        seen.append(url)
        return httpx.Response(
            302, headers={"location": SIGNED if url == START else START}
        )
    session, broker = make_session(handler, tmp_path)
    try:
        with pytest.raises(ProviderError, match="boucle de téléchargement"):
            session._download(START, tmp_path / "out.epub")
        assert seen == [START, SIGNED]
        assert broker.calls == []
        assert not list(tmp_path.glob(".numilog-*"))
    finally:
        session.close()


def test_fulfillment_redirect_budget_is_bounded(tmp_path):
    seen = []
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(
            302,
            headers={"location": "https://www.numilog.com/steps/" + str(len(seen))},
        )
    session, broker = make_session(handler, tmp_path)
    try:
        with pytest.raises(ProviderError, match="trop de redirections"):
            session._download(START, tmp_path / "out.epub")
        assert len(seen) == 12
        assert broker.calls == []
    finally:
        session.close()


@pytest.mark.parametrize("header_present", (False, True))
def test_oversized_acsm_response_fails_without_broker_or_staging(
    tmp_path, header_present,
):
    seen = []
    oversized = b"x" * 1025
    def handler(request):
        seen.append(str(request.url))
        headers = {"content-type": "application/vnd.adobe.adept+xml"}
        if header_present:
            headers["content-length"] = str(len(oversized))
        # Passing content= automatically adds Content-Length in httpx;
        # a real streaming response without it must exercise the byte loop.
        return httpx.Response(
            200, headers=headers, stream=httpx.ByteStream(oversized)
        )
    session, broker = make_session(handler, tmp_path)
    session.maximum = 1024
    try:
        with pytest.raises(ProviderError, match="trop volumineux") as caught:
            session._download(SIGNED, tmp_path / "out.epub")
        assert seen == [SIGNED]
        assert broker.calls == []
        assert "SYNTHETIC_SIGNATURE" not in str(caught.value)
        assert not list(tmp_path.glob(".numilog-*"))
    finally:
        session.close()
