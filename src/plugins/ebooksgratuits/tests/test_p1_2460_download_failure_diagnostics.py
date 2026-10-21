"""P1 #2460: per-book download failures remain typed, private and non-retried.

All evidence is synthetic. No live OPDS, personal data or acquisitions.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca.plugin_api import Cancelled, Conflict, TemporaryUnavailable, UnsupportedOperation
from cifereca.plugin_api import ProviderError
from cifereca_ebooksgratuits import EbooksSession, _opds_items, _xml_root


HOST = "https://opds2.ebooksgratuits.org"
FEED = HOST + "/index.php/feed/search?q=book"
DOWNLOAD = HOST + "/index.php/fetch/42.epub"
ENTRY = (
    '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
    '<id>urn:synthetic:42</id><title>Livre public</title>'
    '<link rel="http://opds-spec.org/acquisition/open-access" '
    'type="application/epub+zip" href="/index.php/fetch/42.epub"/>'
    '</entry></feed>'
)


class FakeTransport:
    def __init__(self, error=None):
        self.error = error
        self.calls = []
    def fetch(self, source, target):
        self.calls.append((dict(source), target))
        if self.error is not None:
            raise self.error
        target.write_bytes(b"SYNTHETIC EPUB broker handoff")
        return target


def session(error):
    broker = FakeTransport(error)
    s = EbooksSession(
        credential_id="public", label="Public", credentials={},
        options={}, config={}, diagnostics=None, transport=broker,
    )
    s.connect()
    rows = _opds_items(s, _xml_root(ENTRY.encode()), FEED)
    assert len(rows) == 1
    return s, broker, rows[0]


@pytest.mark.parametrize("error,expected", [
    (TemporaryUnavailable("transport.http: auth_required"), "download_auth_required"),
    (TemporaryUnavailable("transport.http: access_denied"), "download_access_denied"),
    (TemporaryUnavailable("transport.http: not_found"), "download_not_found"),
    (TemporaryUnavailable("transport.http: rate_limited"), "download_rate_limited"),
    (TemporaryUnavailable("transport.http: service_unavailable"), "download_service_unavailable"),
    (TemporaryUnavailable("transport.http: erreur réseau (ReadTimeout)"), "download_service_unavailable"),
    (TemporaryUnavailable("transport.http: trop de redirections"), "download_redirect_limit"),
    (UnsupportedOperation("transport.http: redirection hors origine refusée"), "download_redirect_denied"),
    (UnsupportedOperation("transport.http: redirection HTTPS vers HTTP refusée"), "download_redirect_denied"),
    (UnsupportedOperation("transport.http: réponse HTML refusée"), "download_invalid_content"),
    (UnsupportedOperation("transport.http: content-type refusé (application/xml)"), "download_invalid_content"),
    (UnsupportedOperation("transport.http: artefact invalide (epub_structure_corrupt)"), "download_invalid_epub"),
    (UnsupportedOperation("transport.http: artefact protégé refusé"), "download_protected"),
    (UnsupportedOperation("transport.http: fichier trop volumineux"), "download_too_large"),
    (UnsupportedOperation("transport.http: réponse vide"), "download_empty_response"),
    (Conflict("transport.http: private_file_path.epub"), "download_destination_conflict"),
])
@pytest.mark.parametrize("cached", [False, True])
def test_failed_download_is_exactly_classified_without_retry_or_partial_file(
    tmp_path, cached, error, expected,
):
    s, transport, fresh = session(error)
    try:
        row = deepcopy(fresh) if cached else fresh
        with pytest.raises(ProviderError) as exc:
            s.download_direct(row, tmp_path)
        assert str(exc.value) == "Ebooks libres et gratuits : " + expected
        assert len(transport.calls) == 1
        assert transport.calls[0][0]["url"] == DOWNLOAD
        assert not list(tmp_path.rglob("*.epub"))
        assert not list(tmp_path.rglob("*.part"))
    finally:
        s.close()


def test_transport_error_never_exposes_raw_response_token_or_url(tmp_path):
    marker = "UNIQUE_PRIVATE_MARKER_2460"
    s, transport, row = session(UnsupportedOperation(
        "transport.http: content-type refusé (text/html?token=" + marker + ")",
    ))
    try:
        with pytest.raises(ProviderError) as exc:
            s.download_direct(row, tmp_path)
        assert marker not in str(exc.value)
        assert marker not in repr(exc.value)
        assert str(exc.value).endswith("download_invalid_content")
        assert len(transport.calls) == 1
    finally:
        s.close()


def test_unrecognized_error_does_not_leak_or_claim_http_status(tmp_path):
    marker = "SECRET_USER_PATH_2460"
    for error in (RuntimeError(marker), UnsupportedOperation(marker),
                  TemporaryUnavailable(marker)):
        s, transport, row = session(error)
        try:
            with pytest.raises(ProviderError) as exc:
                s.download_direct(row, tmp_path)
            assert marker not in str(exc.value)
            assert "download_" in str(exc.value)
            assert len(transport.calls) == 1
        finally:
            s.close()


def test_cancelled_download_keeps_control_flow_and_does_not_retry(tmp_path):
    s, transport, row = session(Cancelled("synthetic cancelled"))
    try:
        with pytest.raises(Cancelled):
            s.download_direct(row, tmp_path)
        assert len(transport.calls) == 1
        assert not list(tmp_path.rglob("*.epub"))
    finally:
        s.close()


def test_successful_epub_broker_handoff_is_not_affected_by_failure_mapping(tmp_path):
    s, transport, row = session(None)
    try:
        location, evidence = s.download_direct(deepcopy(row), tmp_path)
        assert location.read_bytes() == b"SYNTHETIC EPUB broker handoff"
        assert evidence == DOWNLOAD
        assert len(transport.calls) == 1
    finally:
        s.close()
