from __future__ import annotations

from pathlib import Path
import sys

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cifereca.plugin_api import ProviderError, ProviderCapabilityError
from cifereca_gallica import GallicaSession


ROOT_FEED = b'''<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Gallica</title>
  <link rel="search" type="application/opensearchdescription+xml" href="/opds-search.xml" />
</feed>'''

OPEN_SEARCH = b'''<?xml version="1.0" encoding="utf-8"?>
<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
  <Url type="application/atom+xml;profile=opds-catalog" template="https://gallica.bnf.fr/opds/search?q={searchTerms}&amp;startPage={startPage?}" />
</OpenSearchDescription>'''

RESULTS = '''<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dcterms="http://purl.org/dc/terms/">
  <title>Recherche</title>
  <entry>
    <id>https://gallica.bnf.fr/ark:/12148/bpt6k5493831p</id>
    <title>Les CaractÃ¨res</title>
    <author><name>Jean de La BruyÃ¨re</name></author>
    <summary>Edition numÃ©rique Gallica</summary>
    <dcterms:issued>1880</dcterms:issued>
    <link rel="alternate" type="text/html" href="https://gallica.bnf.fr/ark:/12148/bpt6k5493831p" />
    <link rel="http://opds-spec.org/image/thumbnail" type="image/jpeg" href="/ark:/12148/bpt6k5493831p.thumbnail" />
    <link rel="http://opds-spec.org/acquisition/open-access" type="application/epub+zip" href="https://gallica.bnf.fr/ark:/12148/bpt6k5493831p.epub" />
  </entry>
</feed>'''

SRU_AUDIO = b'''<?xml version="1.0" encoding="UTF-8"?>
<srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/" xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <srw:numberOfRecords>1</srw:numberOfRecords>
  <srw:records><srw:record><srw:recordData><oai_dc:dc>
    <dc:identifier>https://gallica.bnf.fr/ark:/12148/bpt6k1279113</dc:identifier>
    <dc:title>Archives de la parole</dc:title>
    <dc:creator>Ferdinand Brunot</dc:creator>
    <dc:date>1911</dc:date>
    <dc:publisher>Universit&#233; de Paris</dc:publisher>
    <dc:subject>Linguistique</dc:subject>
    <dc:subject>Archives sonores</dc:subject>
    <dc:language>fre</dc:language>
    <dc:type>sound</dc:type><dc:type>document sonore</dc:type>
    <dc:format>audio</dc:format>
  </oai_dc:dc></srw:recordData></srw:record></srw:records>
</srw:searchRetrieveResponse>'''

OAI_AUDIO = b'''<?xml version="1.0" encoding="UTF-8"?>
<results><typedoc>sonore</typedoc><sounds>
  <page num="0"><media><title>Introduction</title><file>https://gallica.bnf.fr/ark:/12148/bpt6k1279113/f1.audio</file></media></page>
  <page num="1"><media><title>Discours</title><file>https://gallica.bnf.fr/ark:/12148/bpt6k1279113/f2.audio</file></media></page>
</sounds></results>'''


class Diagnostics:
    def add(self, *_args, **_kwargs):
        return None


class FailingDiagnostics:
    def add(self, *_args, **_kwargs):
        raise RuntimeError("diagnostics unavailable")


class MemoryTransport:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.sources: list[dict] = []

    def fetch(self, source, destination):
        self.sources.append(dict(source))
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.payload)
        return path


def _session(tmp_path: Path, handler, *, transport=None, include_audio=False, diagnostics=None) -> GallicaSession:
    session = GallicaSession(
        credential_id="gallica-public",
        label="Gallica",
        credentials={},
        options={
            "opds_url": "https://gallica.bnf.fr/opds",
            "include_audio": include_audio,
        },
        config={"request_timeout_seconds": 25},
        diagnostics=diagnostics or Diagnostics(),
        transport=transport,
    )
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    session.connect()
    return session


def test_opds_search_discovers_opensearch_and_normalizes_results(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://gallica.bnf.fr/opds":
            return httpx.Response(200, content=ROOT_FEED, headers={"content-type": "application/atom+xml"})
        if url == "https://gallica.bnf.fr/opds-search.xml":
            return httpx.Response(200, content=OPEN_SEARCH, headers={"content-type": "application/opensearchdescription+xml"})
        if url.startswith("https://gallica.bnf.fr/opds/search?"):
            assert "q=bruyere" in url
            assert "startPage=1" in url
            return httpx.Response(200, content=RESULTS, headers={"content-type": "application/atom+xml"})
        raise AssertionError(url)

    session = _session(tmp_path, handler)
    try:
        rows = session.search("bruyere", "digital")
        assert len(rows) == 1
        item = rows[0]
        assert item.source_provider == "gallica"
        assert item.record_id == "ark:/12148/bpt6k5493831p"
        assert item.format == "EPUB"
        assert item.medium == "digital"
        assert item.download_url and item.download_url.endswith(".epub")
        assert item.thumbnail_url == "https://gallica.bnf.fr/ark:/12148/bpt6k5493831p.thumbnail"
    finally:
        session.close()


def test_search_success_is_not_failed_by_diagnostics_sink(tmp_path, monkeypatch):
    session = _session(
        tmp_path,
        lambda request: httpx.Response(404),
        diagnostics=FailingDiagnostics(),
    )
    item = session._parse_feed(RESULTS, "https://gallica.bnf.fr/opds/search")[0]
    monkeypatch.setattr(session, "_search_ebooks", lambda query, page, field: [item])

    try:
        rows = session.search("bruyere", "digital")

        assert [row.id for row in rows] == [item.id]
        assert session.last_search_warnings == []
    finally:
        session.close()


def test_partial_search_keeps_valid_branch_when_diagnostics_sink_fails(tmp_path, monkeypatch):
    session = _session(
        tmp_path,
        lambda request: httpx.Response(404),
        include_audio=True,
        diagnostics=FailingDiagnostics(),
    )
    item = session._parse_feed(RESULTS, "https://gallica.bnf.fr/opds/search")[0]
    monkeypatch.setattr(session, "_search_ebooks", lambda query, page, field: [item])

    def fail_audio(query, page, field):
        raise RuntimeError("audio unavailable")

    monkeypatch.setattr(session, "_search_audio", fail_audio)

    try:
        rows = session.search("bruyere", "digital")

        assert [row.id for row in rows] == [item.id]
        assert session.last_search_warnings == [
            "partial_results : Gallica : recherche audio temporairement indisponible"
        ]
    finally:
        session.close()


def test_successful_ebook_download_is_not_failed_by_diagnostics_sink(tmp_path):
    transport = MemoryTransport(b"PK\x03\x04" + b"epub" * 32)
    session = _session(
        tmp_path,
        lambda request: httpx.Response(404),
        transport=transport,
        diagnostics=FailingDiagnostics(),
    )
    item = session._parse_feed(RESULTS, "https://gallica.bnf.fr/opds/search")[0]

    try:
        path, url = session.download_direct(item, tmp_path / "downloads")

        assert path.is_file()
        assert path.suffix == ".epub"
        assert url.endswith(".epub")
        assert len(transport.sources) == 1
    finally:
        session.close()


def test_physical_scope_is_explicitly_unsupported_and_epub_download_uses_validating_transport(tmp_path):
    epub = b"PK\x03\x04" + b"fake-epub-test" * 20
    transport = MemoryTransport(epub)
    session = _session(tmp_path, lambda request: httpx.Response(404), transport=transport)
    try:
        with pytest.raises(ProviderCapabilityError, match="unsupported_scope"):
            session.search("anything", "physical")
        item = session._parse_feed(RESULTS, "https://gallica.bnf.fr/opds/search")[0]
        path, url = session.download_direct(item, tmp_path / "downloads")
        assert path.suffix == ".epub"
        assert path.read_bytes() == epub
        assert url.endswith("bpt6k5493831p.epub")
        assert transport.sources[0]["expected_format"] == "epub"
        assert "application/epub+zip" in transport.sources[0]["allowed_content_types"]
        detail = session.holdings(item)
        assert detail.availability_label == "Téléchargement libre"
        assert detail.holdings == []
    finally:
        session.close()


def test_sru_audio_normalizes_and_downloads_all_tracks_with_playlist(tmp_path):
    audio1 = b"ID3" + b"a" * 64
    audio2 = b"ID3" + b"b" * 64

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.url.path.endswith("/services/OAIRecord"):
            return httpx.Response(200, content=OAI_AUDIO, headers={"content-type": "application/xml"})
        if url.endswith("/f1.audio"):
            return httpx.Response(200, content=audio1, headers={"content-type": "audio/mpeg"})
        if url.endswith("/f2.audio"):
            return httpx.Response(200, content=audio2, headers={"content-type": "audio/mpeg"})
        raise AssertionError(url)

    session = _session(tmp_path, handler, include_audio=True)
    try:
        rows = session._parse_sru_audio(SRU_AUDIO)
        assert len(rows) == 1
        item = rows[0]
        assert item.content_type == "audiobook"
        assert item.format is None
        assert item.record_id == "ark:/12148/bpt6k1279113"
        assert item.publisher == "Université de Paris"
        assert item.published_at == "1911"
        assert item.metadata["languages"] == ["fr"]
        assert item.metadata["subjects"] == ["Linguistique", "Archives sonores"]
        assert item.metadata["identifiers"] == ["https://gallica.bnf.fr/ark:/12148/bpt6k1279113"]
        assert item.metadata["bibliographic_types"] == ["sound", "document sonore"]
        assert item.metadata["bibliographic_formats"] == ["audio"]
        assert item.metadata["metadata_evidence"] == "gallica.sru.dublin_core"

        playlist, source_url = session.download_direct(item, tmp_path / "audio")
        assert item.format == "MP3"
        assert playlist.suffix == ".m3u8"
        assert playlist.is_file()
        assert source_url.endswith("bpt6k1279113")
        track_files = sorted(playlist.parent.glob("*.mp3"))
        assert len(track_files) == 2
        assert track_files[0].read_bytes() == audio1
        assert track_files[1].read_bytes() == audio2
    finally:
        session.close()


def test_opds_audio_acquisition_gets_audiobook_content_type(tmp_path):
    feed = b'''<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <id>https://gallica.bnf.fr/ark:/12148/bpt6kaudio</id><title>Un livre audio</title>
      <link rel="http://opds-spec.org/acquisition/open-access" type="audio/mpeg" href="https://gallica.bnf.fr/ark:/12148/bpt6kaudio/f1.mp3" />
    </entry></feed>'''
    session = _session(tmp_path, lambda request: httpx.Response(404))
    try:
        item = session._parse_feed(feed, "https://gallica.bnf.fr/opds/")[0]
        assert item.content_type == "audiobook"
        assert item.format == "MP3"
        assert item.type == "Livre audio"
    finally:
        session.close()


def test_sru_audio_recovers_valid_records_from_partially_malformed_xml(tmp_path):
    malformed = '''<?xml version="1.0" encoding="UTF-8"?>
    <srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/" xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" xmlns:dc="http://purl.org/dc/elements/1.1/">
      <srw:records>
        <srw:record><srw:recordData><oai_dc:dc>
          <dc:identifier>https://gallica.bnf.fr/ark:/12148/bpt6kgood</dc:identifier>
          <dc:title>Document sonore valide</dc:title>
          <dc:type>sound</dc:type><dc:format>audio</dc:format>
        </oai_dc:dc></srw:recordData></srw:record>
        <srw:record><srw:recordData><oai_dc:dc>
          <dc:identifier>https://gallica.bnf.fr/ark:/12148/bpt6kbad</dc:identifier>
          <dc:title>Notice cassÃ©e</dc:creator>
          <dc:type>sound</dc:type>
        </oai_dc:dc></srw:recordData></srw:record>
      </srw:records>
    </srw:searchRetrieveResponse>'''.encode("utf-8")

    session = _session(tmp_path, lambda request: httpx.Response(404))
    try:
        rows = session._parse_sru_audio(malformed)
        assert len(rows) == 1
        assert rows[0].record_id == "ark:/12148/bpt6kgood"
        assert rows[0].content_type == "audiobook"
    finally:
        session.close()


def test_audio_xml_failure_with_empty_healthy_opds_warns_without_parser_details(tmp_path):
    empty_feed = b'''<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"><title>Vide</title></feed>'''
    broken_sru = b'''<?xml version="1.0"?><srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/">
      <srw:records><srw:record><srw:recordData><bad><tag></bad></srw:recordData></srw:record></srw:records>
    </srw:searchRetrieveResponse>'''

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://gallica.bnf.fr/opds":
            return httpx.Response(200, content=ROOT_FEED, headers={"content-type": "application/atom+xml"})
        if url == "https://gallica.bnf.fr/opds-search.xml":
            return httpx.Response(200, content=OPEN_SEARCH, headers={"content-type": "application/opensearchdescription+xml"})
        if url.startswith("https://gallica.bnf.fr/opds/search?"):
            return httpx.Response(200, content=empty_feed, headers={"content-type": "application/atom+xml"})
        if request.url.path.endswith("/services/engine/search/sru"):
            return httpx.Response(200, content=broken_sru, headers={"content-type": "application/xml"})
        raise AssertionError(url)

    session = _session(tmp_path, handler, include_audio=True)
    try:
        # The OPDS branch is healthy but empty: a broken SRU audio branch
        # must report partial coverage, not turn a real zero into a global
        # failure. Its parse details must still remain private.
        assert session.search("audio-test", "digital") == []
        assert len(session.last_search_warnings) == 1
        message = session.last_search_warnings[0]
        assert message.startswith("partial_results : Gallica : ")
        assert "recherche audio temporairement indisponible" in message
        assert "mismatched tag" not in message
        assert "XML invalide" not in message
    finally:
        session.close()


def test_audio_download_success_is_not_failed_by_diagnostics_sink(tmp_path):
    audio1 = b"ID3" + b"a" * 64
    audio2 = b"ID3" + b"b" * 64

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.url.path.endswith("/services/OAIRecord"):
            return httpx.Response(200, content=OAI_AUDIO, headers={"content-type": "application/xml"})
        if url.endswith("/f1.audio"):
            return httpx.Response(200, content=audio1, headers={"content-type": "audio/mpeg"})
        if url.endswith("/f2.audio"):
            return httpx.Response(200, content=audio2, headers={"content-type": "audio/mpeg"})
        raise AssertionError(url)

    session = _session(
        tmp_path,
        handler,
        include_audio=True,
        diagnostics=FailingDiagnostics(),
    )
    try:
        item = session._parse_sru_audio(SRU_AUDIO)[0]
        playlist, source_url = session.download_direct(item, tmp_path / "audio")

        assert playlist.is_file()
        assert playlist.suffix == ".m3u8"
        assert source_url.endswith("bpt6k1279113")
        assert len(list(playlist.parent.glob("*.mp3"))) == 2
    finally:
        session.close()
