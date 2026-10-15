"""Offline end-to-end signed Numilog delivery tests (#1844)."""
from __future__ import annotations

from pathlib import Path
import tempfile
from urllib.parse import urlsplit
import zipfile

import httpx
import pytest

from cifereca.plugin_api import AccountRef, ProviderError, ProviderCapabilityError
from cifereca_numilog import NumilogSession

SIGNED = (
    "https://entrepot2r.numilog.com/fulfillment/URLLink.acsm"
    "?action=enterorder&orderid=FICTIONAL-001&rights=FAKE%2FREAD"
    "&resid=FAKE-RESOURCE&auth=FAKE-SIGNATURE%2BUNIQUE"
)
ACSM = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<fulfillmentToken xmlns="http://ns.adobe.com/adept">'
    b'<resourceItemInfo/></fulfillmentToken>'
)


class Diagnostics:
    def __init__(self):
        self.events = []

    def add(self, *args, **kwargs):
        self.events.append((args, kwargs))


class Identities:
    def __init__(self, credential_id):
        import hashlib
        self.account_id = "numilog:" + hashlib.sha256(credential_id.encode()).hexdigest()[:20]

    def accounts(self, identity_id=None):
        return (AccountRef(account_id=self.account_id, identity_id="reader",
                           provider_id="numilog", label="Synthetic"),)


class Broker:
    def __init__(self, root, status="complete"):
        self.root = root
        self.status = status
        self.calls = []

    def import_acsm(self, path, **options):
        p = Path(path)
        assert p.is_file() and p.suffix == ".acsm"
        assert p.read_bytes() == ACSM
        assert options["identity_id"] == "reader"
        assert options["provider_id"] == "numilog"
        self.calls.append(dict(options))
        if self.status != "complete":
            return {"status": self.status, "code": "adept_profile_required"}
        output = self.root / "fulfilled.epub"
        with zipfile.ZipFile(output, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
        return {"status": "complete", "path": str(output)}


def fixture(tmp, handler, status="complete"):
    broker = Broker(tmp, status=status)
    diag = Diagnostics()
    s = NumilogSession(
        credential_id="numilog-fixture", label="Synthetic",
        credentials={"email": "test@example.invalid", "password": "fixture"},
        options={}, config={}, diagnostics=diag,
        identity_broker=Identities("numilog-fixture"), acquisition_broker=broker,
    )
    s.client.close()
    s.client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return s, broker, diag


def test_redirect_to_exact_signed_acsm_origin_and_broker_once(tmp_path):
    requests = []
    def handler(request):
        requests.append(str(request.url))
        if str(request.url) == "https://www.numilog.com/readBook.ashx":
            return httpx.Response(302, headers={"Location": SIGNED})
        if str(request.url) == SIGNED:
            return httpx.Response(200, headers={"Content-Type": "application/vnd.adobe.adept+xml"}, content=ACSM)
        raise AssertionError("unexpected destination")

    session, broker, diag = fixture(tmp_path, handler)
    try:
        produced, source = session._download(
            "https://www.numilog.com/readBook.ashx", tmp_path / "book.epub", title="Synthetic"
        )
        assert produced.is_file()
        assert source == session.library_url
        assert requests == ["https://www.numilog.com/readBook.ashx", SIGNED]
        assert len(broker.calls) == 1
        assert not list(tmp_path.glob(".numilog-*"))
        assert "FAKE-SIGNATURE" not in repr(diag.events)
    finally:
        session.close()


def test_html_intermediate_signed_acsm_origin_and_broker_once(tmp_path):
    requests = []
    def handler(request):
        requests.append(str(request.url))
        if str(request.url) == "https://www.numilog.com/readBook.ashx":
            return httpx.Response(200, headers={"Content-Type": "text/html"},
                                  text=f'<a href="{SIGNED.replace("&", "&amp;")}">Download</a>')
        if str(request.url) == SIGNED:
            return httpx.Response(200, headers={"Content-Type": "application/xml"}, content=ACSM)
        raise AssertionError("unexpected destination")

    session, broker, diag = fixture(tmp_path, handler)
    try:
        produced, source = session._download(
            "https://www.numilog.com/readBook.ashx", tmp_path / "book.epub"
        )
        assert produced.is_file() and source == session.library_url
        assert requests == ["https://www.numilog.com/readBook.ashx", SIGNED]
        assert len(broker.calls) == 1
        assert not list(tmp_path.glob(".numilog-*"))
        assert "FAKE-SIGNATURE" not in repr(diag.events)
    finally:
        session.close()


@pytest.mark.parametrize("bad", (
    "http://entrepot2r.numilog.com/fulfillment/URLLink.acsm",
    "https://entrepot2r.numilog.com:8443/fulfillment/URLLink.acsm",
    "https://entrepot2r.numilog.com.evil.invalid/fulfillment/URLLink.acsm",
    "https://unknown.invalid/other",
))
def test_redirect_denied_before_request(tmp_path, bad):
    requested = []
    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(302, headers={"Location": bad})

    session, broker, diag = fixture(tmp_path, handler)
    try:
        with pytest.raises(ProviderError):
            session._download("https://www.numilog.com/readBook.ashx",
                              tmp_path / "book.epub")
        assert requested == ["https://www.numilog.com/readBook.ashx"]
        assert broker.calls == []
        assert not list(tmp_path.glob(".numilog-*"))
    finally:
        session.close()


@pytest.mark.parametrize("status,content_type,body", (
    (403, "application/xml", b"forbidden"),
    (429, "application/xml", b"rate limit"),
    (200, "text/xml", b"<not-an-acsm/>"),
    (200, "text/html", b"<html><body>no delivery</body></html>"),
))
def test_delivery_failures_do_not_call_broker_or_leave_staging(tmp_path, status, content_type, body):
    requested = []
    def handler(request):
        requested.append(str(request.url))
        if str(request.url) == "https://www.numilog.com/readBook.ashx":
            return httpx.Response(302, headers={"Location": SIGNED})
        return httpx.Response(status, headers={"Content-Type": content_type}, content=body)

    session, broker, diag = fixture(tmp_path, handler)
    try:
        with pytest.raises((ProviderError, ProviderCapabilityError)):
            session._download("https://www.numilog.com/readBook.ashx",
                              tmp_path / "book.epub")
        assert requested == ["https://www.numilog.com/readBook.ashx", SIGNED]
        assert broker.calls == []
        assert not list(tmp_path.glob(".numilog-*"))
        assert "FAKE-SIGNATURE" not in repr(diag.events)
    finally:
        session.close()


def test_broker_attention_required_is_not_false_success(tmp_path):
    def handler(request):
        if str(request.url) == "https://www.numilog.com/readBook.ashx":
            return httpx.Response(302, headers={"Location": SIGNED})
        return httpx.Response(200, headers={"Content-Type": "application/xml"}, content=ACSM)

    session, broker, _ = fixture(tmp_path, handler, status="attention_required")
    try:
        with pytest.raises(ProviderCapabilityError, match="adept_profile_required"):
            session._download("https://www.numilog.com/readBook.ashx",
                              tmp_path / "book.epub")
        assert len(broker.calls) == 1
        assert not list(tmp_path.glob(".numilog-*"))
    finally:
        session.close()
