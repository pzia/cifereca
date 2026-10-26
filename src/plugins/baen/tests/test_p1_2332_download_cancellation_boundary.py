"""#2332/#2333: Baen public download cancellation and broker error boundaries.

Offline only. Tests never contact Baen, create purchases or change rights.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from cifereca.plugin_api import (
    Cancelled, Conflict, ProviderError, TemporaryUnavailable, UnsupportedOperation,
)
from cifereca_baen import BaenSession


PRODUCT = "https://www.baen.com/allbooks/synthetic-product"
DOWNLOAD = "https://www.baen.com/downloads/synthetic.epub"


class FakePage:
    text = '<a href="/downloads/synthetic.epub">Download EPUB</a>'
    status_code = 200

    def raise_for_status(self):
        pass


class FakeClient:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return FakePage()

    def close(self):
        pass


class Broker:
    def __init__(self, error):
        self.error = error
        self.calls = []

    def fetch(self, source, dest):
        self.calls.append((dict(source), Path(dest)))
        raise self.error


def session(error, *, detail_error=None):
    obj = BaenSession(
        credential_id="public", label="", credentials={}, options={},
        config={}, diagnostics=None, transport=Broker(error),
    )
    obj.client.close()
    obj.client = FakeClient(detail_error)
    obj.connect()
    return obj


def item():
    return SimpleNamespace(
        source_provider="baen", friendly_url=PRODUCT,
        title="Livre", record_id="public-record",
        author=None, published_at=None, metadata={},
    )


def test_cancelled_broker_propagates_as_cancellation_without_fallback_or_retry(tmp_path):
    marker = Cancelled("PRIVATE_TASK_ID")
    s = session(marker)
    try:
        with pytest.raises(Cancelled) as caught:
            s.download_direct(item(), tmp_path)
        assert caught.value is marker
        assert len(s.transport.calls) == 1
        assert len(s.client.calls) == 1
        assert not list(tmp_path.glob("*.epub"))
    finally:
        s.close()


@pytest.mark.parametrize("error,public_code", [
    (TemporaryUnavailable("transport.http: auth_required"), "download_auth_required"),
    (TemporaryUnavailable("transport.http: access_denied"), "download_access_denied"),
    (TemporaryUnavailable("transport.http: not_found"), "download_not_found"),
    (TemporaryUnavailable("transport.http: rate_limited"), "download_rate_limited"),
    (TemporaryUnavailable("transport.http: service_unavailable"), "download_service_unavailable"),
    (TemporaryUnavailable("transport.http: erreur réseau (ReadTimeout)"), "download_service_unavailable"),
    (TemporaryUnavailable("transport.http: trop de redirections"), "download_redirect_limit"),
    (UnsupportedOperation("transport.http: réponse HTML refusée"), "download_invalid_content"),
    (UnsupportedOperation("transport.http: content-type refusé (text/html?token=PRIVATE)"), "download_invalid_content"),
    (UnsupportedOperation("transport.http: artefact invalide (epub_structure_corrupt)"), "download_invalid_epub"),
    (UnsupportedOperation("transport.http: artefact protégé refusé"), "download_protected"),
    (UnsupportedOperation("transport.http: fichier trop volumineux"), "download_too_large"),
    (UnsupportedOperation("transport.http: redirection hors origine refusée"), "download_redirect_denied"),
    (UnsupportedOperation("transport.http: réponse vide"), "download_empty_response"),
    (Conflict("transport.http: /PRIVATE/home/book.epub"), "download_destination_conflict"),
])
def test_broker_errors_are_closed_classified_without_private_exception_chains(
    tmp_path, error, public_code,
):
    s = session(error)
    try:
        with pytest.raises(ProviderError) as caught:
            s.download_direct(item(), tmp_path)
        raised = caught.value
        assert str(raised) == "Baen : " + public_code
        assert raised.__cause__ is None
        assert raised.__context__ is None
        assert "PRIVATE" not in repr(raised)
        assert len(s.client.calls) == 1
        assert len(s.transport.calls) == 1
        source, dest = s.transport.calls[0]
        assert source["url"] == DOWNLOAD
        assert source["classify_http_status"] is True
        assert source["expected_format"] == "epub"
        assert dest.parent == tmp_path
        assert not list(tmp_path.glob("*.epub"))
    finally:
        s.close()


@pytest.mark.parametrize("error", [
    RuntimeError("PRIVATE_SIGNED_URL"),
    ProviderError("PRIVATE_ACCOUNT"),
    UnsupportedOperation("PRIVATE_UNTRUSTED_FORMAT"),
])
def test_unrecognized_error_never_exposes_private_detail(tmp_path, error):
    s = session(error)
    try:
        with pytest.raises(ProviderError) as caught:
            s.download_direct(item(), tmp_path)
        assert "PRIVATE" not in str(caught.value)
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert len(s.transport.calls) == 1
    finally:
        s.close()


def test_detail_transport_failure_does_not_preserve_raw_httpx_url_or_chain(tmp_path):
    request = httpx.Request("GET", PRODUCT + "?secret=PRIVATE")
    original = httpx.ReadTimeout("PRIVATE_HTTPX", request=request)
    s = session(None, detail_error=original)
    try:
        with pytest.raises(ProviderError, match="^Baen : fiche inaccessible$") as caught:
            s.download_direct(item(), tmp_path)
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert "PRIVATE" not in repr(caught.value)
        assert len(s.client.calls) == 1
        assert s.transport.calls == []
    finally:
        s.close()
