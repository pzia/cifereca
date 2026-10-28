"""An in-flight delivery cannot be redirected into another profile's ADEPT import."""
import io
import zipfile

import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_numilog_signed_acsm_transport_p0 import setup, ACSM, READ_BOOK, SIGNED, _assert_clean
from tests.test_numilog_owned_js_delivery_p0 import _session, _page, _card, LIBRARY


@pytest.mark.parametrize("rebind_at", ["redirect", "response", "body"])
def test_owner_change_during_acsm_transport_blocks_import_and_further_hops(tmp_path, rebind_at):
    requested = []
    holder = []
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            holder[0].identity_broker.identities = ("another-reader",)
            yield ACSM
    def handler(request):
        requested.append(str(request.url))
        if str(request.url) == READ_BOOK:
            if rebind_at == "redirect":
                holder[0].identity_broker.identities = ("another-reader",)
            return httpx.Response(302, headers={"location": SIGNED})
        assert str(request.url) == SIGNED
        if rebind_at == "response":
            holder[0].identity_broker.identities = ("another-reader",)
        return httpx.Response(200, headers={"content-type": "application/vnd.adobe.adept+xml"},
                              stream=Body() if rebind_at == "body" else httpx.ByteStream(ACSM))
    session, diagnostics, acquisition = setup(tmp_path, handler)
    holder.append(session)
    try:
        with pytest.raises(ProviderCapabilityError, match="rattachement du compte modifié"):
            session._download(READ_BOOK, tmp_path / "result.epub", title="PRIVATE")
        assert requested == ([READ_BOOK] if rebind_at == "redirect" else [READ_BOOK, SIGNED])
        assert not acquisition.calls and not (tmp_path / "result.epub").exists()
        _assert_clean(tmp_path)
        assert "another-reader" not in repr(diagnostics.events)
        assert "PRIVATE" not in repr(diagnostics.events)
    finally:
        session.client.close()


def test_rebinding_during_fresh_owned_library_read_prevents_signed_get(tmp_path):
    session, diagnostics, acquisition, requested = _session(tmp_path, _page(_card(12, 9001, SIGNED)))
    try:
        item = session.library()[0]
        original = session._rows
        def rows(*args, **kwargs):
            result = original(*args, **kwargs)
            session.identity_broker.identities = ("another-reader",)
            return result
        session._rows = rows
        with pytest.raises(ProviderCapabilityError, match="rattachement du compte modifié"):
            session.download_direct(item, tmp_path)
        assert requested == [LIBRARY, LIBRARY]
        assert not acquisition.calls
        _assert_clean(tmp_path)
    finally:
        session.client.close()


def test_rebinding_during_epub_body_never_publishes_or_overwrites_a_file(tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>')
        archive.writestr("content.opf", '<package><metadata/><manifest/><spine/></package>')
    holder = []
    def handler(request):
        holder[0].identity_broker.identities = ("another-reader",)
        return httpx.Response(200, headers={"content-type": "application/epub+zip"}, content=data.getvalue())
    session, _, acquisition = setup(tmp_path, handler)
    holder.append(session)
    target = tmp_path / "kept.epub"
    target.write_bytes(b"existing book must survive")
    try:
        with pytest.raises(ProviderCapabilityError, match="rattachement du compte modifié"):
            session._download(READ_BOOK, target)
        assert target.read_bytes() == b"existing book must survive"
        assert not acquisition.calls
        _assert_clean(tmp_path)
    finally:
        session.client.close()


@pytest.mark.parametrize("broken", ["missing", "ambiguous", "exception"])
def test_initial_owner_becoming_unresolvable_blocks_before_import(tmp_path, broken):
    holder = []
    def handler(request):
        identities = holder[0].identity_broker
        if broken == "exception":
            def fail(*args, **kwargs):
                raise RuntimeError("PRIVATE_BROKER_EXCEPTION")
            identities.accounts = fail
        else:
            identities.identities = () if broken == "missing" else ("first", "second")
        return httpx.Response(200, headers={"content-type": "application/vnd.adobe.adept+xml"}, content=ACSM)
    session, diagnostics, acquisition = setup(tmp_path, handler)
    holder.append(session)
    try:
        with pytest.raises(ProviderCapabilityError, match="rattachement du compte modifié"):
            session._download(READ_BOOK, tmp_path / "result.epub")
        assert not acquisition.calls
        assert "PRIVATE_BROKER_EXCEPTION" not in repr(diagnostics.events)
        _assert_clean(tmp_path)
    finally:
        session.client.close()


def test_rebinding_inside_broker_keeps_original_identity_and_does_not_claim_success(tmp_path):
    session, diagnostics, acquisition = setup(tmp_path, lambda request: httpx.Response(
        200, headers={"content-type": "application/vnd.adobe.adept+xml"}, content=ACSM,
    ))
    original = acquisition.import_acsm
    def import_then_rebind(*args, **kwargs):
        result = original(*args, **kwargs)
        session.identity_broker.identities = ("another-reader",)
        return result
    acquisition.import_acsm = import_then_rebind
    try:
        with pytest.raises(ProviderCapabilityError, match="rattachement du compte modifié"):
            session._download(READ_BOOK, tmp_path / "result.epub")
        assert len(acquisition.calls) == 1
        assert acquisition.calls[0][1]["identity_id"] == "fictional-reader"
        assert (tmp_path / "fiche-fictive.epub").is_file()  # never delete a broker-owned artifact
        assert not any("acsm_complete" in repr(event) for event in diagnostics.events)
        _assert_clean(tmp_path)
    finally:
        session.client.close()


def test_same_owner_new_broker_during_owned_inventory_refuses_signed_delivery(tmp_path):
    """A->A broker replacement is not the original owner attestation."""
    from tests.test_numilog_signed_acsm_transport_p0 import IdentityBroker

    session, _diag, acquisition, requested = _session(
        tmp_path, _page(_card(12, 9001, SIGNED)),
    )
    try:
        item = session.library()[0]
        original = session._rows

        def replace_after_fresh_library(**kwargs):
            rows = original(**kwargs)
            newer = IdentityBroker(("fictional-reader",))
            newer.account_id = "numilog:" + session.scope
            session.identity_broker = newer
            return rows

        session._rows = replace_after_fresh_library
        with pytest.raises(ProviderCapabilityError, match="rattachement du compte modifié"):
            session.download_direct(item, tmp_path)
        assert requested == [LIBRARY, LIBRARY]
        assert acquisition.calls == []
        _assert_clean(tmp_path)
    finally:
        session.client.close()


def test_same_owner_new_broker_during_direct_epub_body_never_returns_artifact(tmp_path):
    """The direct EPUB path must not accept the text of owner A from broker B."""
    from tests.test_numilog_signed_acsm_transport_p0 import IdentityBroker

    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml",
                         '<container><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>')
        archive.writestr("content.opf", '<package><metadata/><manifest/><spine/></package>')
    session_holder = []
    requests = []

    def handler(request):
        requests.append(str(request.url))
        replacement = IdentityBroker(("fictional-reader",))
        replacement.account_id = "numilog:" + session_holder[0].scope
        session_holder[0].identity_broker = replacement
        return httpx.Response(200, headers={"content-type": "application/epub+zip"},
                              content=data.getvalue())

    session, _diag, acquisition = setup(tmp_path, handler)
    session_holder.append(session)
    destination = tmp_path / "new-file.epub"
    try:
        with pytest.raises(ProviderCapabilityError, match="rattachement du compte modifié"):
            session._download(READ_BOOK, destination)
        assert requests == [READ_BOOK]
        assert not destination.exists()
        assert not acquisition.calls
        _assert_clean(tmp_path)
    finally:
        session.client.close()
