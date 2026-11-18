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
        ref, = c.catalogue_web_references
        assert ref.url == URL and ref.record_id == ARK and ref.provider_id == session.provider_id
        assert evict_notice_cover(db, doc, expected_epoch=source.epoch)
        service.select_identity("bob")
        known = service.search_known_catalog("loups", identity_id="bob")
        public, = [item for item in known if item.get("source") == "notice_c_public"]
        assert public["_cover_document_ref"] == canonical
        menu = read_public_web_choices(db, canonical)
        choice, = menu.choices
        assert resolve_public_web_choice(db, canonical, expected_epoch=menu.epoch,
                                         choice_id=choice.choice_id) == URL
        assert len(requests) == 1
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        service.close()
        session.close()
