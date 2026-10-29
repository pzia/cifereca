"""#1844/#771: Numilog ACSM redirect is tested at its real HTTP client boundary.

Fictional HTTPS responses only, via httpx.MockTransport.  No account,
merchant order, fulfillment endpoint, ADEPT installation or internet is used.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
import zipfile

from cifereca.plugin_api import Account, AccountRef, ProviderCapabilityError, ProviderError
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
    session._accounts = [Account(provider_id="numilog", credential_id=session.credential_id,
                                 unique_id=identity_broker.account_id, identity_id="fictional-reader")]
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
        assert request.headers.get("referer") == (
            "https://www.numilog.com/Pages/Compte/MesLivres.aspx"
            if url == READ_BOOK else "https://www.numilog.com/"
        )
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


@pytest.mark.parametrize("destination", (
    "https://entrepot2r.numilog.com.evil.example/fulfillment/evil.acsm",
    "http://entrepot2r.numilog.com/fulfillment/evil.acsm",
    "https://entrepot2r.numilog.com:8443/fulfillment/evil.acsm",
))
def test_second_redirect_from_allowed_signed_acsm_is_rejected_before_network(
    tmp_path, destination,
):
    """Allowed first hop does not authorize any subsequent destination."""
    seen = []

    def handler(request):
        url = str(request.url)
        seen.append(url)
        if url == READ_BOOK:
            return httpx.Response(302, headers={"location": SIGNED})
        if url == SIGNED:
            return httpx.Response(302, headers={"location": destination})
        raise AssertionError("Unsafe third HTTP request was actually sent")

    session, diag, acquisition = setup(tmp_path, handler)
    try:
        with pytest.raises(ProviderError, match="destination réseau inattendue"):
            session._download(READ_BOOK, tmp_path / "target.epub")
        assert seen == [READ_BOOK, SIGNED]
        assert acquisition.calls == []
        assert "SYNTHETIC_NOT_A_CREDENTIAL" not in repr(diag.events)
        _assert_clean(tmp_path)
    finally:
        session.close()


def test_stream_body_limit_is_enforced_even_if_content_length_is_falsely_small(tmp_path):
    """The bytes actually read override the server's declared size."""
    seen = []

    def handler(request):
        url = str(request.url)
        seen.append(url)
        if url == READ_BOOK:
            return httpx.Response(302, headers={"location": SIGNED})
        assert url == SIGNED
        return httpx.Response(
            200, content=b"x" * 1500, headers={
                "content-type": "application/octet-stream",
                "content-length": "8",
            },
        )

    session, diag, acquisition = setup(tmp_path, handler)
    session.maximum = 1024
    try:
        with pytest.raises(ProviderError, match="trop volumineux"):
            session._download(READ_BOOK, tmp_path / "target.epub")
        assert seen == [READ_BOOK, SIGNED]
        assert acquisition.calls == []
        _assert_clean(tmp_path)
    finally:
        session.close()



def test_owned_readbook_uses_fixed_library_referrer_only_on_first_hop(tmp_path):
    """The referrer never includes a private lid, signature or redirect target."""
    login = "https://www.numilog.com/Login.Aspx?auth=PRIVATE_REFERRER_MARKER"
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers.get("referer")))
        if str(request.url) == READ_BOOK:
            return httpx.Response(302, headers={"location": login})
        if str(request.url) == login:
            return httpx.Response(
                200, headers={"content-type": "text/html"},
                text='<form><input type="password" /></form>',
            )
        raise AssertionError("unexpected network request")

    session, diag, acquisition = setup(tmp_path, handler)
    try:
        with pytest.raises(ProviderError, match="formulaire de connexion"):
            session._download(READ_BOOK, tmp_path / "file.epub", source="readbook")
        assert seen == [
            (READ_BOOK, session.library_url),
            (login, session.base_url),
        ]
        assert acquisition.calls == []
        assert not (tmp_path / "file.epub").exists()
        # No queried referrer, URL, purchase ID or private landing page
        # should ever be serialized to diagnostics or error text.
        exported = repr(diag.events)
        assert "PRIVATE_REFERRER_MARKER" not in exported
        assert "fictional" not in exported
        assert "?lid=" not in exported
    finally:
        session.close()


def test_other_allowed_readbook_origin_cannot_claim_private_library_referrer(tmp_path):
    foreign_readbook = (
        "https://services.numilog.com/Pages/Compte/readBook.ashx"
        "?lid=PRIVATE_REFERRER_MARKER"
    )
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers.get("referer")))
        return httpx.Response(403)

    session, diag, acquisition = setup(tmp_path, handler)
    try:
        with pytest.raises(ProviderError):
            session._download(foreign_readbook, tmp_path / "file.epub")
        assert seen == [(foreign_readbook, session.base_url)]
        assert acquisition.calls == []
        assert "PRIVATE_REFERRER_MARKER" not in repr(diag.events)
    finally:
        session.close()


@pytest.mark.parametrize("broker_status,media_format", (
    ("complete", "epub"),
    ("unchanged", "epub"),
    ("complete", "pdf"),
    ("unchanged", "pdf"),
))
def test_acsm_broker_returns_nested_identity_artifact_without_flattening_or_index_claim(
    tmp_path, broker_status, media_format,
):
    """A provider's ACSM success is NOT a durable Core purchase index.

    The real Core broker puts fulfilled artifacts inside an identity/provider
    acsm-<source_sha>/ directory and indexes them under an ACSM digest
    before Numilog's distinct purchase-ID checkpoint runs. A provider must
    relay the EXACT returned file, neither flattening/moving it nor claiming
    it was attached to an owned right. All inputs are fictional.
    """
    visited = []

    def handler(request):
        url = str(request.url)
        visited.append(url)
        if url == READ_BOOK:
            return httpx.Response(302, headers={"location": SIGNED})
        assert url == SIGNED
        return httpx.Response(
            200, content=ACSM,
            headers={"content-type": "application/vnd.adobe.adept+xml"},
        )

    session, diag, broker = setup(tmp_path, handler)
    nested = (
        tmp_path / "identity" / "numilog" / "acsm-FICTIONAL_SOURCE_SHA"
        / ("Livre-fictif." + media_format)
    )
    nested.parent.mkdir(parents=True)
    if media_format == "pdf":
        payload = b"%PDF-1.4\n% Fictional, not a real purchase\n"
        nested.write_bytes(payload)
    else:
        with zipfile.ZipFile(nested, "w") as archive:
            archive.writestr(
                "mimetype", "application/epub+zip",
                compress_type=zipfile.ZIP_STORED,
            )
            archive.writestr(
                "META-INF/container.xml",
                '<?xml version="1.0"?><container version="1.0"/>',
            )
        payload = nested.read_bytes()
    assert nested.is_file()

    calls = []

    def import_acsm(path, **kwargs):
        assert Path(path).is_file() and Path(path).read_bytes() == ACSM
        assert kwargs["identity_id"] == "fictional-reader"
        assert kwargs["provider_id"] == "numilog"
        calls.append((Path(path), kwargs))
        # Simulate the *public* Core broker response. Existing artifact
        # provenance is intentionally out of this provider's authority.
        return {"status": broker_status, "path": str(nested)}

    broker.import_acsm = import_acsm
    try:
        output, source = session._download(
            READ_BOOK, tmp_path / "should-not-flatten.epub",
            title="PRIVATE_BOOK_TITLE",
        )
        assert output == nested and output.read_bytes() == payload
        assert source == session.library_url
        assert not (tmp_path / "should-not-flatten.epub").exists()
        assert visited == [READ_BOOK, SIGNED] and len(calls) == 1
        # The Numilog trace proves the broker returned an existing local
        # file, but cannot attest Core's later purchase-ID index/checkpoint.
        events = [
            kw["detail"] for args, kw in diag.events
            if args[:2] == ("NUMILOG", "acquisition_delivery")
        ]
        assert events[-1]["phase"] == "completed"
        assert events[-1]["code"] == "acsm_complete"
        # The terminal success is the diagnostics.add status argument,
        # NOT a claim that Core has completed the purchase-specific index.
        assert diag.events[-1][0][:3] == ("NUMILOG", "acquisition_delivery", "ok")
        assert not any(event.get("phase") == "core_index_complete" for event in events)
        for sensitive in (
            "FICTIONAL_SOURCE_SHA", "PRIVATE_BOOK_TITLE",
            "SYNTHETIC_NOT_A_CREDENTIAL", "auth=",
            "orderid=", "resid=", "source_acsm_sha256",
        ):
            assert sensitive not in repr(diag.events)
        assert list(tmp_path.rglob("*.acsm")) == []
        _assert_clean(tmp_path)
    finally:
        session.close()
