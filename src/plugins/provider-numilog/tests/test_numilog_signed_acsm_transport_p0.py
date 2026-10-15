"""#1844/#771: Numilog ACSM redirect is tested at its real HTTP client boundary.

Fictional HTTPS responses only, via httpx.MockTransport.  No account,
merchant order, fulfillment endpoint, ADEPT installation or internet is used.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from cifereca.plugin_api import AccountRef, ProviderCapabilityError, ProviderError
from cifereca_numilog import NumilogSession


READ_BOOK = "https://www.numilog.com/Pages/Compte/readBook.ashx?lid=fictional&m=download"
SIGNED = (
    "https://entrepot2r.numilog.com/fulfillment/URLLink.acsm"
    "?action=enterorder&ordersource=Numilog&orderid=FICTIONAL"
    "&resid=urn%3Auuid%3Afake&rights=%24copy%230%23ever"
    "&gbauthdate=01%2f01%2f2000&dateval=946684800&gblver=4"
    "&auth=SYNTHETIC_NOT_A_CREDENTIAL"
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


class IdentityBroker:
    def __init__(self, identities=("fictional-reader",)):
        self.identities = identities
        self.account_id = ""

    def accounts(self, identity_id=None):
        return tuple(
            AccountRef(
                account_id=self.account_id, identity_id=value,
                provider_id="numilog", label="Compte fictif",
            )
            for value in self.identities
        )


class AcquisitionBroker:
    def __init__(self, root: Path, *, status="complete"):
        self.root = root
        self.status = status
        self.calls = []

    def import_acsm(self, path, **kwargs):
        path = Path(path)
        assert path.is_file() and path.suffix == ".acsm"
        assert path.read_bytes() == ACSM
        assert kwargs["identity_id"] == "fictional-reader"
        assert kwargs["provider_id"] == "numilog"
        self.calls.append((str(path), kwargs))
        if self.status == "attention_required":
            return {"status": "attention_required", "code": "adept_login_needed"}
        if self.status != "complete":
            return {"status": self.status}
        target = self.root / "fiche-fictive.epub"
        target.write_bytes(b"fictional ADEPT broker output")
        return {"status": "complete", "path": str(target)}


def setup(tmp_path, handler, *, identities=("fictional-reader",), status="complete"):
    diag = Diagnostics()
    identity_broker = IdentityBroker(identities)
    acquisition = AcquisitionBroker(tmp_path, status=status)
    session = NumilogSession(
        credential_id="fake-credential", label="Numilog fictif",
        credentials={"email": "fictional@example.test", "password": "not-a-real-password"},
        options={}, config={}, diagnostics=diag,
        acquisition_broker=acquisition, identity_broker=identity_broker,
    )
    identity_broker.account_id = "numilog:" + session.scope
    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False,
    )
    return session, diag, acquisition


def _assert_clean(tmp_path):
    assert list(tmp_path.glob(".numilog-*")) == []
    assert list(tmp_path.glob("*.part")) == []
    assert list(tmp_path.glob("*.acsm")) == []


def test_signed_302_reaches_real_acsm_inspection_and_single_account_broker(tmp_path):
    requested = []

    def handler(request):
        url = str(request.url)
        requested.append(url)
        assert request.method == "GET"
        assert request.headers.get("referer") == "https://www.numilog.com/"
        if url == READ_BOOK:
            return httpx.Response(302, headers={"location": SIGNED})
        if url == SIGNED:
            return httpx.Response(
                200, content=ACSM,
                headers={"content-type": "application/vnd.adobe.adept+xml"},
            )
        raise AssertionError("No unlisted URL must be requested")

    session, diag, acquisition = setup(tmp_path, handler)
    try:
        result, source = session._download(
            READ_BOOK, tmp_path / "file.epub", title="Livre fictif",
        )
        assert result.is_file()
        assert source == session.library_url
        assert requested == [READ_BOOK, SIGNED]
        assert urlsplit(requested[1]).query == urlsplit(SIGNED).query
        assert len(acquisition.calls) == 1
        assert acquisition.calls[0][1]["title"] == "Livre fictif"
        assert "auth=" not in source
        assert "SYNTHETIC_NOT_A_CREDENTIAL" not in repr(diag.events)
        _assert_clean(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize("format_html", (
    '<a class="download" href="{url}">Télécharger</a>',
    '<script>window.location = "{url}";</script>',
))
def test_html_delivery_intermediate_preserves_signed_url_and_broker(tmp_path, format_html):
    seen = []

    def handler(request):
        url = str(request.url)
        seen.append(url)
        if url == READ_BOOK:
            return httpx.Response(
                200, text=format_html.format(url=SIGNED),
                headers={"content-type": "text/html; charset=utf-8"},
            )
        if url == SIGNED:
            return httpx.Response(200, content=ACSM, headers={"content-type": "application/xml"})
        raise AssertionError("No unlisted request")

    session, diag, acquisition = setup(tmp_path, handler)
    try:
        result, source = session._download(READ_BOOK, tmp_path / "target.epub")
        assert result.is_file() and source == session.library_url
        assert seen == [READ_BOOK, SIGNED]
        assert len(acquisition.calls) == 1
        assert "SYNTHETIC_NOT_A_CREDENTIAL" not in repr(diag.events)
        _assert_clean(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize("bad", (
    SIGNED.replace("https://", "http://", 1),
    SIGNED.replace("entrepot2r.numilog.com", "entrepot2r.numilog.com.evil.example"),
    SIGNED.replace("entrepot2r.numilog.com", "entrepot2r.numilog.com:8443"),
    SIGNED.replace("https://", "https://user:pass@", 1),
    SIGNED + "#private-fragment",
))
def test_302_to_untrusted_origin_is_denied_before_following_it(tmp_path, bad):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if str(request.url) == READ_BOOK:
            return httpx.Response(302, headers={"location": bad})
        raise AssertionError("Unsafe network destination reached")

    session, diag, acquisition = setup(tmp_path, handler)
    try:
        with pytest.raises(ProviderError, match="destination réseau inattendue"):
            session._download(READ_BOOK, tmp_path / "target.epub")
        assert seen == [READ_BOOK]
        assert acquisition.calls == []
        assert "SYNTHETIC_NOT_A_CREDENTIAL" not in repr(diag.events)
        _assert_clean(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize("failure", ("403", "429", "wrong-xml", "wrong-mime", "oversize"))
def test_acsm_delivery_errors_never_import_or_leave_temporary_files(tmp_path, failure):
    seen = []

    def handler(request):
        url = str(request.url)
        seen.append(url)
        if url == READ_BOOK:
            return httpx.Response(302, headers={"location": SIGNED})
        assert url == SIGNED
        if failure in ("403", "429"):
            return httpx.Response(int(failure))
        if failure == "oversize":
            return httpx.Response(
                200, content=ACSM, headers={
                    "content-type": "application/vnd.adobe.adept+xml",
                    "content-length": str(1024 * 1024 * 1024),
                },
            )
        if failure == "wrong-mime":
            return httpx.Response(200, content=ACSM, headers={"content-type": "image/png"})
        return httpx.Response(200, content=b"<book/>", headers={"content-type": "application/xml"})

    session, diag, acquisition = setup(tmp_path, handler)
    try:
        with pytest.raises((ProviderError, ProviderCapabilityError)):
            session._download(READ_BOOK, tmp_path / "target.epub")
        assert seen == [READ_BOOK, SIGNED]
        assert acquisition.calls == []
        assert "SYNTHETIC_NOT_A_CREDENTIAL" not in repr(diag.events)
        _assert_clean(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize("identities,status", (
    ((), "complete"),
    (("reader-one", "reader-two"), "complete"),
    (("fictional-reader",), "attention_required"),
    (("fictional-reader",), "partial"),
))
def test_acsm_fulfillment_requires_unique_owner_and_confirmed_output(tmp_path, identities, status):
    def handler(request):
        if str(request.url) == READ_BOOK:
            return httpx.Response(302, headers={"location": SIGNED})
        assert str(request.url) == SIGNED
        return httpx.Response(200, content=ACSM, headers={"content-type": "application/xml"})

    session, diag, acquisition = setup(tmp_path, handler, identities=identities, status=status)
    try:
        with pytest.raises((ProviderError, ProviderCapabilityError)):
            session._download(READ_BOOK, tmp_path / "target.epub")
        assert len(acquisition.calls) == (1 if identities == ("fictional-reader",) else 0)
        assert "SYNTHETIC_NOT_A_CREDENTIAL" not in repr(diag.events)
        _assert_clean(tmp_path)
    finally:
        session.close()


def test_loopback_redirect_cannot_replay_the_same_signed_token(tmp_path):
    seen = []

    def handler(request):
        url = str(request.url)
        seen.append(url)
        if url == READ_BOOK:
            return httpx.Response(302, headers={"location": SIGNED})
        assert url == SIGNED
        return httpx.Response(302, headers={"location": READ_BOOK})

    session, diag, acquisition = setup(tmp_path, handler)
    try:
        with pytest.raises(ProviderError, match="boucle de téléchargement"):
            session._download(READ_BOOK, tmp_path / "target.epub")
        assert seen == [READ_BOOK, SIGNED]
        assert acquisition.calls == []
        _assert_clean(tmp_path)
    finally:
        session.close()
