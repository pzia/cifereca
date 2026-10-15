"""#1844 / #771: exercise Numilog's real HTTP redirect and ACSM broker boundary.

Entirely synthetic signed URL; httpx.MockTransport, no network, credentials,
real orders, ADEPT server, or external filesystem side effects.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from cifereca.plugin_api import AccountRef, ProviderError
from cifereca_numilog import NumilogSession

READ_BOOK = "https://www.numilog.com/Pages/Compte/readBook.ashx?lid=7&m=download"
SIGNED = (
    "https://entrepot2r.numilog.com/fulfillment/URLLink.acsm"
    "?action=enterorder&ordersource=Numilog&orderid=FAKE-ORDER"
    "&resid=urn%3Auuid%3AFICTIONAL&rights=%24cpy%230%23ever"
    "&dateval=1111111111&auth=SYNTHETIC_SIGNATURE"
)
ACSM = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<fulfillmentToken xmlns="http://ns.adobe.com/adept">'
    b'<resourceItemInfo/></fulfillmentToken>'
)


class Diagnostics:
    def __init__(self):
        self.events = []

    def add(self, *args, **kwargs):
        self.events.append((args, kwargs))


class Acquisition:
    def __init__(self, root, status="complete"):
        self.root = root
        self.status = status
        self.calls = []

    def import_acsm(self, path, **kwargs):
        source = Path(path)
        assert source.is_file() and source.read_bytes() == ACSM
        assert kwargs["identity_id"] == "reader-1"
        assert kwargs["provider_id"] == "numilog"
        self.calls.append((source.name, dict(kwargs)))
        if self.status == "attention_required":
            return {"status": "attention_required", "code": "adept_attention_required"}
        if self.status == "failed":
            return {"status": "failed"}
        result = self.root / "fulfilled.epub"
        result.write_bytes(b"SIMULATED-BROKER-FULFILLMENT")
        return {"status": self.status, "path": str(result)}


class Identities:
    def __init__(self, scope, *, ambiguous=False):
        account = AccountRef(
            account_id="numilog:" + scope,
            identity_id="reader-1",
            provider_id="numilog",
            label="Compte fictif",
        )
        # Two different identities claiming the same account are ambiguous;
        # duplicated identical bindings do not change the set of owners.
        second = AccountRef(
            account_id="numilog:" + scope,
            identity_id="reader-2",
            provider_id="numilog",
            label="Compte fictif (autre identité)",
        )
        self.rows = (account, second) if ambiguous else (account,)

    def accounts(self, identity_id=None):
        return self.rows


def fixture(tmp_path, handler, *, broker_status="complete", ambiguous=False, maximum=10240):
    credentials = {"email": "fictional@example.test", "password": "fake"}
    credential_id = "fake-credential"
    scope = hashlib.sha256(credential_id.encode()).hexdigest()[:20]
    sink = Diagnostics()
    broker = Acquisition(tmp_path, status=broker_status)
    session = NumilogSession(
        credential_id=credential_id, label="Fiction",
        credentials=credentials, config={},
        options={"max_download_bytes": maximum},
        diagnostics=sink, acquisition_broker=broker,
        identity_broker=Identities(scope, ambiguous=ambiguous),
    )
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(handler),
                                follow_redirects=False, trust_env=False)
    target = tmp_path / "staged" / "output.epub"
    target.parent.mkdir(parents=True)
    return session, broker, sink, target


def assert_no_staging(directory):
    assert not list(directory.glob(".numilog-*.part"))
    assert not list(directory.glob(".numilog-*.acsm"))


@pytest.mark.parametrize("route", ["redirect", "html", "javascript"])
def test_signed_delivery_reaches_one_broker_on_exact_account_without_query_mutation(
    tmp_path, route
):
    seen = []

    def handler(request):
        address = str(request.url)
        seen.append(address)
        if address == READ_BOOK:
            if route == "redirect":
                return httpx.Response(302, headers={"location": SIGNED})
            if route == "html":
                return httpx.Response(200, headers={"content-type": "text/html"},
                                      text=f'<a href="{SIGNED}">Télécharger</a>')
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text=f'<script>window.location = "{SIGNED}";</script>')
        if request.url.host == "entrepot2r.numilog.com":
            assert request.url.scheme == "https" and request.url.port in (None, 443)
            assert request.url.path == "/fulfillment/URLLink.acsm"
            assert request.url.query.decode("ascii") == urlsplit(SIGNED).query
            return httpx.Response(200, headers={
                "content-type": "application/vnd.adobe.adept+xml",
            }, content=ACSM)
        raise AssertionError("Unexpected synthetic HTTP host/path")

    session, broker, diagnostics, target = fixture(tmp_path, handler)
    try:
        produced, source = session._download(READ_BOOK, target, title="Livre fictif")
        assert produced.is_file()
        assert source == session.library_url
        assert seen == [READ_BOOK, SIGNED]
        assert len(broker.calls) == 1
        assert broker.calls[0][1]["title"] == "Livre fictif"
        assert "SYNTHETIC_SIGNATURE" not in repr(diagnostics.events)
        assert "FAKE-ORDER" not in repr(diagnostics.events)
        assert "SYNTHETIC_SIGNATURE" not in source
        assert_no_staging(target.parent)
    finally:
        session.close()


@pytest.mark.parametrize("bad_destination", (
    SIGNED.replace("https://", "http://", 1),
    SIGNED.replace("entrepot2r.numilog.com", "entrepot2r.numilog.com.evil.invalid"),
    SIGNED.replace("entrepot2r.numilog.com", "entrepot2r.numilog.com:8443"),
    SIGNED.replace("https://", "https://user:pass@", 1),
    SIGNED + "#SECRET_FRAGMENT",
    "https://evil.invalid/download.epub",
))
def test_redirect_to_untrusted_origin_never_dispatches_a_get(
    tmp_path, bad_destination
):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        assert str(request.url) == READ_BOOK
        return httpx.Response(302, headers={"location": bad_destination})

    session, broker, diagnostics, target = fixture(tmp_path, handler)
    try:
        with pytest.raises(ProviderError):
            session._download(READ_BOOK, target)
        assert seen == [READ_BOOK]
        assert not broker.calls
        assert_no_staging(target.parent)
        assert "SECRET_FRAGMENT" not in repr(diagnostics.events)
    finally:
        session.close()


@pytest.mark.parametrize("fault", [
    "403", "429", "loop", "html_missing", "html_ambiguous",
    "wrong_mime", "wrong_xml", "oversize",
])
def test_negative_delivery_is_closed_and_never_calls_adept(tmp_path, fault):
    seen = []

    def handler(request):
        address = str(request.url)
        seen.append(address)
        if address == READ_BOOK:
            return httpx.Response(302, headers={"location": SIGNED})
        assert address == SIGNED
        if fault in {"403", "429"}:
            return httpx.Response(int(fault), text="PRIVATE_PROVIDER_ERROR")
        if fault == "loop":
            return httpx.Response(302, headers={"location": SIGNED})
        if fault == "html_missing":
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text="<html><body>No download link</body></html>")
        if fault == "html_ambiguous":
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text=f'<a href="{SIGNED}">A</a>'
                                       f'<a href="{SIGNED}&duplicate=1">B</a>')
        if fault == "wrong_mime":
            return httpx.Response(200, headers={"content-type": "image/png"},
                                  content=b"NOT AN ACSM")
        if fault == "wrong_xml":
            return httpx.Response(200, headers={"content-type": "application/xml"},
                                  content=b"<not-a-fulfillment-token/>")
        if fault == "oversize":
            return httpx.Response(200, headers={
                "content-type": "application/vnd.adobe.adept+xml",
                "content-length": "2048",
            }, content=b"X" * 2048)
        raise AssertionError("Uncovered fault")

    session, broker, diagnostics, target = fixture(tmp_path, handler, maximum=1024)
    try:
        with pytest.raises(ProviderError):
            session._download(READ_BOOK, target)
        assert seen == [READ_BOOK, SIGNED]
        assert not broker.calls
        assert_no_staging(target.parent)
        assert "PRIVATE_PROVIDER_ERROR" not in repr(diagnostics.events)
        assert "SYNTHETIC_SIGNATURE" not in repr(diagnostics.events)
    finally:
        session.close()


@pytest.mark.parametrize("state,ambiguous", [
    ("attention_required", False),
    ("failed", False),
    ("complete", True),
])
def test_broker_attention_failure_or_ambiguous_identity_is_not_success(
    tmp_path, state, ambiguous
):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if str(request.url) == READ_BOOK:
            return httpx.Response(302, headers={"location": SIGNED})
        assert str(request.url) == SIGNED
        return httpx.Response(200, headers={
            "content-type": "application/vnd.adobe.adept+xml",
        }, content=ACSM)

    session, broker, diagnostics, target = fixture(
        tmp_path, handler, broker_status=state, ambiguous=ambiguous
    )
    try:
        with pytest.raises(ProviderError):
            session._download(READ_BOOK, target)
        assert seen == [READ_BOOK, SIGNED]
        assert len(broker.calls) == (0 if ambiguous else 1)
        assert_no_staging(target.parent)
        assert "SYNTHETIC_SIGNATURE" not in repr(diagnostics.events)
    finally:
        session.close()
