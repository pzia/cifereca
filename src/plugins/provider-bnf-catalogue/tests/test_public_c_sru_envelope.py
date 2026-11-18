"""#6366/#6397: the actual BnF SRU parser and ownerless Core Search, no live GET."""
from pathlib import Path

import httpx
import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.services import LibraryService
from cifereca.storage import Storage
from cifereca.core.notice_cover_repository import read_notice_cover, evict_notice_cover
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.core.notice_c_web_choices import read_public_web_choices, resolve_public_web_choice
import cifereca_bnf_catalogue as provider

ARK = "ark:/12148/cb100000004d"
URL = "https://catalogue.bnf.fr/" + ARK
BODY = f"""<srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/"
  xmlns:dc="http://purl.org/dc/elements/1.1/">
 <srw:numberOfRecords>1</srw:numberOfRecords>
 <srw:records><srw:record><srw:recordData><dc:dc>
 <dc:identifier>{URL}</dc:identifier><dc:title>Les loups</dc:title>
 <dc:creator>Auteur</dc:creator><dc:format>1 vol. ; 20 cm</dc:format>
 <dc:language>fre</dc:language><dc:language>eng</dc:language>
 </dc:dc></srw:recordData></srw:record></srw:records>
 </srw:searchRetrieveResponse>""".encode()


@pytest.mark.parametrize("custom", [False, True])
def test_bnf_sru_one_page_public_c_web_reference(tmp_path, monkeypatch, custom):
    requests = []
    def serve(request):
        requests.append(request)
        assert request.method == "GET"
        return httpx.Response(200, content=BODY, request=request)
    monkeypatch.setattr(provider.HTTPX_TRANSPORT, "client",
        lambda **kw: httpx.Client(transport=httpx.MockTransport(serve), trust_env=False))
    cfg = Config(tmp_path / "config.json")
    cfg.set("library.directory", str(tmp_path / "library"))
    cfg.set("identities", [{"id": "alice", "label": "Alice"}, {"id": "bob", "label": "Bob"}])
    cfg.set("providers.accounts", [])
    db = Storage(tmp_path / "bnf.sqlite")
    service = LibraryService(cfg, db, Diagnostics())
    session = provider.BnfCatalogueSession(
        credential_id="__public__", label="BnF", credentials={},
        options={"sru_url": "https://other.example.org/sru"} if custom else {},
        diagnostics=None,
    )
    session.connect()
    service._sessions = {session.provider_id: session}
    try:
        rows = service.search("Les loups", provider_id=session.provider_id, force=True)
        assert len(requests) == 1
        if custom:
            assert rows == []
            assert service.search_public_notice_c("loups").hits == ()
            return
        row, = rows
        assert row.friendly_url == URL and row.thumbnail_url is None
        assert row.source_collection == "public_catalog"
        assert row.metadata["public_url_evidence"] == "bnf-catalogue.public_search_record_identifier"
        assert row.owned is False and row.downloadable is False
        doc = row._core_notice_cover_document_ref
        canonical = row._core_notice_canonical_cover_document_ref
        source = read_notice_cover(db, doc)
        assert source.observation.status == "partial" and source.attribute.state == "pending"
        c = read_consolidated_notice_cover(db, canonical)
        language = c.sources[0].public_bibliography.language_observation
        assert language.raw_tags == ("fre", "eng")
        assert language.languages == ("fr", "en")
        assert language.source == "bnf-catalogue.sru"
        assert language.source_field == "dc.language"
        ref, = c.catalogue_web_references
        assert ref.url == URL and ref.record_id == ARK and ref.provider_id == session.provider_id
        assert evict_notice_cover(db, doc, expected_epoch=source.epoch)
        service.select_identity("bob")
        known = service.search_known_catalog("loups", identity_id="bob")
        public, = [item for item in known if item.get("source") == "notice_c_public"]
        assert public["_cover_document_ref"] == canonical
        assert public["metadata"]["languages"] == ["fr", "en"]
        assert public["metadata"]["bibliography_evidence"]["languages"]["raw_tags"] == ["fre", "eng"]
        reopened = Storage(tmp_path / "bnf.sqlite")
        try:
            # Exact canonical source survives transient-source eviction.
            retained = read_consolidated_notice_cover(reopened, canonical)
            assert retained.sources[0].public_bibliography.language_observation == language
        finally:
            reopened.close()
        menu = read_public_web_choices(db, canonical)
        choice, = menu.choices
        assert resolve_public_web_choice(db, canonical, expected_epoch=menu.epoch,
                                         choice_id=choice.choice_id) == URL
        assert len(requests) == 1
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        service.close()
        session.close()


@pytest.mark.parametrize("destination", [
    "https://foreign.example.org/api/SRU",
    "https://catalogue.bnf.fr/other",
    "http://catalogue.bnf.fr/api/SRU",
    "https://catalogue.bnf.fr/api/SRU?query=other",
])
def test_bnf_canonical_sru_redirect_never_parses_or_follows(tmp_path, monkeypatch, destination):
    from cifereca.plugin_api import ProviderError
    requests = []

    def serve(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(302, headers={"location": destination}, request=request)
        # A syntactically valid SRU record cannot confer canonical origin.
        return httpx.Response(200, content=BODY, request=request)

    monkeypatch.setattr(provider.HTTPX_TRANSPORT, "client",
        lambda **kw: httpx.Client(transport=httpx.MockTransport(serve),
                                  trust_env=False, follow_redirects=True))
    session = provider.BnfCatalogueSession(
        credential_id="__public__", label="BnF", credentials={},
        options={}, diagnostics=None,
    )
    session.connect()
    try:
        with pytest.raises(ProviderError, match="unverified_sru_navigation"):
            session.search_with_response_facets("Les loups", "all")
        assert len(requests) == 1
        assert requests[0].url.host == "catalogue.bnf.fr"
        assert requests[0].url.path == "/api/SRU"
    finally:
        session.close()


@pytest.mark.parametrize("total,count,next_position,scope,field,partial,complete", [
    (0, 0, None, "all", "title", False, True),
    (1, 1, None, "all", "title", False, True),
    (3, 2, None, "all", "title", False, False),
    (3, 1, 2, "all", "title", False, False),
    (3, 1, None, "all", "title", True, None),
    (3, 0, None, "all", "title", True, None),
    (0, 1, None, "all", "title", True, None),
    (1, 1, 2, "all", "title", True, None),
    (3, 1, 3, "all", "title", True, None),
    (1, 1, None, "digital", "title", False, False),
    (1, 1, None, "all", "series", False, False),
])
def test_sru_coverage_same_page_not_execution_failure(
    monkeypatch, total, count, next_position, scope, field, partial, complete,
):
    from xml.etree import ElementTree as ET
    root = ET.fromstring(BODY)
    ns = "{http://www.loc.gov/zing/srw/}"
    dc = "{http://purl.org/dc/elements/1.1/}"
    root.find(ns + "numberOfRecords").text = str(total)
    records = root.find(ns + "records")
    template = records[0]
    records.remove(template)
    for index in range(count):
        record = ET.fromstring(ET.tostring(template))
        record.find(".//" + dc + "identifier").text = (
            "https://catalogue.bnf.fr/ark:/12148/cb10000000" + str(index + 1) + "d"
        )
        records.append(record)
    if next_position is not None:
        ET.SubElement(root, ns + "nextRecordPosition").text = str(next_position)
    body = ET.tostring(root)
    calls = []
    def serve(request):
        calls.append(request)
        return httpx.Response(200, content=body, request=request)
    monkeypatch.setattr(provider.HTTPX_TRANSPORT, "client",
        lambda **kw: httpx.Client(transport=httpx.MockTransport(serve), trust_env=False))
    session = provider.BnfCatalogueSession(
        credential_id="__public__", label="BnF", credentials={},
        options={"page_size": 2}, diagnostics=None,
    )
    session.connect()
    try:
        response = session.search_with_response_facets("Les loups", scope, field=field)
        assert response.partial is partial
        assert response.catalogue_complete is complete
        expected = 0 if scope == "digital" or field == "series" else count
        assert len(response.results) == expected
        assert len(response.public_notices) == expected
        assert len(calls) == 1
        assert bool(session.last_search_warnings) is partial
        assert all(not row.owned and not row.downloadable for row in response.results)
    finally:
        session.close()


@pytest.mark.parametrize("defect", ["duplicate", "unattested", "missing_data", "nested"])
def test_sru_record_loss_is_not_a_clean_catalogue_window(monkeypatch, defect):
    from xml.etree import ElementTree as ET
    root = ET.fromstring(BODY)
    ns = "{http://www.loc.gov/zing/srw/}"
    dc = "{http://purl.org/dc/elements/1.1/}"
    root.find(ns + "numberOfRecords").text = "2"
    records = root.find(ns + "records")
    other = ET.fromstring(ET.tostring(records[0]))
    if defect == "unattested":
        other.find(".//" + dc + "identifier").text = "https://example.org/record"
    elif defect == "missing_data":
        other.remove(other.find(ns + "recordData"))
    elif defect == "nested":
        data = other.find(ns + "recordData")
        other.remove(data)
        ET.SubElement(other, ns + "extra").append(data)
    records.append(other)
    calls = []
    def serve(request):
        calls.append(request)
        return httpx.Response(200, content=ET.tostring(root), request=request)
    monkeypatch.setattr(provider.HTTPX_TRANSPORT, "client",
        lambda **kw: httpx.Client(transport=httpx.MockTransport(serve), trust_env=False))
    session = provider.BnfCatalogueSession(
        credential_id="__public__", label="BnF", credentials={},
        options={}, diagnostics=None,
    )
    session.connect()
    try:
        page = session.search_with_response_facets("Les loups")
        assert page.partial is True and page.catalogue_complete is None
        assert len(page.results) == len(page.public_notices) == 1
        assert len(calls) == 1
    finally:
        session.close()


def test_sru_skipped_query_does_not_mint_coverage(monkeypatch):
    def fail(request):
        raise AssertionError("short query must not perform GET")
    monkeypatch.setattr(provider.HTTPX_TRANSPORT, "client",
        lambda **kw: httpx.Client(transport=httpx.MockTransport(fail), trust_env=False))
    session = provider.BnfCatalogueSession(
        credential_id="__public__", label="BnF", credentials={},
        options={}, diagnostics=None,
    )
    session.connect()
    try:
        short = session.search_with_response_facets("x")
        assert short.partial is True and short.catalogue_complete is None
        clear = session.search_with_response_facets("")
        assert clear.partial is False and clear.catalogue_complete is None
    finally:
        session.close()


@pytest.mark.parametrize("raw, expected", [
    (b"<dc:language>fra</dc:language>", True),
    (b"", False),
    (b"<dc:language>fra</dc:language><dc:language>not a tag</dc:language>", False),
])
def test_language_proof_only_for_entire_explicit_record_declaration(monkeypatch, raw, expected):
    from cifereca.core.search_orchestration import _public_rows_from_response
    from cifereca.core.public_search_projection import declared_public_language_observation

    body = BODY.replace(b"<dc:language>fre</dc:language><dc:language>eng</dc:language>", raw)
    monkeypatch.setattr(provider.HTTPX_TRANSPORT, "client",
        lambda **kw: httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=body, request=request)), trust_env=False))
    session = provider.BnfCatalogueSession(
        credential_id="__public__", label="BnF", credentials={}, options={}, diagnostics=None,
    )
    session.connect()
    try:
        response = session.search_with_response_facets("Les loups")
        row, = _public_rows_from_response(response, session)
        proof = declared_public_language_observation(
            row.metadata, provider_id=row.source_provider, scenario=row.scenario,
        )
        assert (proof is not None) is expected
        if proof is not None:
            assert proof.raw_tags == ("fra",) and proof.languages == ("fr",)
            # A mismatched normalized value, provider or scope cannot certify
            # a different edition/source's language.
            assert declared_public_language_observation(
                {**row.metadata, "languages": ["en"]},
                provider_id=row.source_provider, scenario=row.scenario,
            ) is None
            assert declared_public_language_observation(
                row.metadata, provider_id="other", scenario=row.scenario,
            ) is None
            assert declared_public_language_observation(
                row.metadata, provider_id=row.source_provider, scenario="OTHER",
            ) is None
    finally:
        session.close()
