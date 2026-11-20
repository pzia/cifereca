"""#3724 Gallica exact anonymous OPDS occurrence, synthetic HTTP only."""
from types import SimpleNamespace
import httpx
import pytest

from cifereca_gallica import GallicaSession
from cifereca.core.search_orchestration import _public_rows_from_response, _mirror_remote_results
from cifereca.core.notice_cover_repository import read_notice_cover, evict_notice_cover
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.storage import Storage

ARK = "ark:/12148/bpt6k5493831p"
LINK = "https://gallica.bnf.fr/" + ARK
ENTRY = f"""<entry><id>{LINK}</id><title>Notice exacte Gallica</title>
<author><name>Auteur témoin</name></author>
<dcterms:language>fr</dcterms:language><dcterms:issued>1880</dcterms:issued>
<link rel="alternate" type="text/html" href="{LINK}"/>
<link rel="http://opds-spec.org/acquisition/open-access" href="{LINK}.epub"
type="application/epub+zip"/>
<link rel="http://opds-spec.org/image/thumbnail" href="{LINK}/thumbnail?token=secret"/>
<summary>Résumé</summary><dcterms:rights>Déclaration</dcterms:rights></entry>"""

def session(entries=ENTRY, *, credential="__public__", credentials=None, options=None):
    calls = []
    payload = ('<feed xmlns="http://www.w3.org/2005/Atom" '
               'xmlns:dcterms="http://purl.org/dc/terms/">' + entries + '</feed>').encode()
    obj = GallicaSession(credential_id=credential, label="Gallica",
                         credentials=credentials or {}, options={"include_audio": False, **(options or {})},
                         config={}, diagnostics=None)
    obj.client.close()
    obj.client = httpx.Client(transport=httpx.MockTransport(
        lambda req: calls.append(req) or httpx.Response(200, content=payload)))
    obj.connect()
    return obj, calls

def test_exact_opds_parser_to_durable_c(tmp_path):
    obj, calls = session()
    filename = tmp_path / "state.sqlite"
    db = Storage(filename)
    try:
        assert obj.connected and obj.accounts == []
        response = obj.search_with_response_facets("Notice exacte Gallica", "catalog", field="title",
                                                    content_types=("epub",))
        assert response.partial is False and response.catalogue_complete is False
        raw, = response.results
        assert response.public_notices[0].result is raw
        assert raw.source_collection == "public_catalog"
        assert raw.downloadable is raw.owned is False
        assert raw.download_url is raw.thumbnail_url is raw.description is None
        assert raw.record_id == ARK and raw.friendly_url == LINK
        assert "rights" not in raw.metadata
        row, = _public_rows_from_response(response, obj)
        assert _mirror_remote_results(SimpleNamespace(storage=db), [row],
                                      identity_id="alice", require_public_cover_witness=True) == 1
        source = read_notice_cover(db, row._core_notice_cover_document_ref)
        canonical = row._core_notice_canonical_cover_document_ref
        c = read_consolidated_notice_cover(db, canonical)
        facts = c.sources[0].public_bibliography
        assert facts.title == raw.title and facts.published_at == "1880"
        assert facts.language_observation.languages == ("fr",)
        assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
        offer, = obj.search("Notice exacte Gallica", "digital", field="title")
        assert offer.downloadable is True and offer.download_url == LINK + ".epub"
        assert len(calls) == 2
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        obj.close()
        db.close()
    db = Storage(filename)
    try:
        hit, = search_public_notice_c(db, "Notice exacte Gallica", group_by_document=True).hits
        assert hit.document == canonical and hit.record_id == ARK
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()

@pytest.mark.parametrize("entries", [
    ENTRY + ENTRY,
    ENTRY.replace(f"<id>{LINK}</id>", ""),
    ENTRY.replace("<title>Notice exacte Gallica</title>", ""),
    ENTRY.replace(LINK + ".epub", "https://foreign.invalid/" + ARK + ".epub"),
    ENTRY.replace(LINK + ".epub", LINK + "2.epub"),
])
def test_nonqualified_occurrences_are_partial(entries):
    obj, calls = session(entries)
    try:
        response = obj.search_with_response_facets("Notice exacte Gallica", "catalog", field="title")
        assert response.results == [] and response.public_notices == ()
        assert response.partial is True and len(calls) == 1
    finally:
        obj.close()

@pytest.mark.parametrize("kwargs", [
    {"credential": "private"},
    {"credentials": {"secret": "fixture"}},
    {"options": {"epub_search_url": "https://foreign.invalid/opds"}},
])
def test_unqualified_session_cannot_publish_public_occurrence(kwargs):
    obj, calls = session(**kwargs)
    try:
        response = obj.search_with_response_facets("Notice exacte Gallica", "catalog", field="title")
        assert response.partial is True and response.results == [] and calls == []
    finally:
        obj.close()

def test_audio_remains_explicitly_unqualified_without_new_sru_get():
    obj, calls = session(options={"include_audio": True})
    try:
        response = obj.search_with_response_facets("Notice exacte Gallica", "catalog", field="title")
        assert len(response.results) == 1 and response.partial is True
        assert len(calls) == 1
        response = obj.search_with_response_facets("Notice exacte Gallica", "catalog",
                                                   field="title", content_types=("mp3",))
        assert not response.results and response.partial is True and len(calls) == 1
    finally:
        obj.close()


@pytest.mark.parametrize("with_alternate", [True, False])
def test_exact_ark_web_choice_survives_source_eviction_and_restart(tmp_path, with_alternate):
    from contextlib import closing
    from cifereca.core.notice_c_web_choices import (
        read_public_web_choices, resolve_public_web_choice,
    )
    from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows

    entries = ENTRY if with_alternate else ENTRY.replace(
        f'<link rel="alternate" type="text/html" href="{LINK}"/>', "",
    )
    obj, calls = session(entries)
    filename = tmp_path / "gallica-web.sqlite"
    try:
        with closing(Storage(filename)) as db:
            response = obj.search_with_response_facets("Notice exacte Gallica", "catalog",
                                                      field="title", content_types=("epub",))
            row, = _public_rows_from_response(response, obj)
            assert _mirror_remote_results(SimpleNamespace(storage=db), [row],
                                          identity_id="alice", require_public_cover_witness=True) == 1
            canonical = row._core_notice_canonical_cover_document_ref
            source = read_notice_cover(db, row._core_notice_cover_document_ref)
            c = read_consolidated_notice_cover(db, canonical)
            ref, = c.catalogue_web_references
            assert (ref.provider_id, ref.record_id, ref.url) == (obj.provider_id, ARK, LINK)
            assert ref.evidence == obj.provider_id + ".public_search_record_identifier"
            assert ref.access_mode == "public"
            menu = read_public_web_choices(db, canonical)
            choice, = menu.choices
            assert (choice.record_id, choice.provider_id) == (ARK, obj.provider_id)
            assert resolve_public_web_choice(db, canonical, expected_epoch=menu.epoch,
                                             choice_id=choice.choice_id) == LINK
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
            assert db.entitlements() == db.acquisitions() == []
        with closing(Storage(filename)) as db:
            known, = merge_public_notice_c_known_rows(
                [], search_public_notice_c(db, "Notice exacte Gallica", group_by_document=True),
                identity_id="bob",
            )
            assert known["_cover_document_ref"] == canonical
            assert read_public_web_choices(db, canonical) == menu
            assert resolve_public_web_choice(db, canonical, expected_epoch=menu.epoch,
                                             choice_id=choice.choice_id) == LINK
            assert resolve_public_web_choice(db, canonical, expected_epoch=menu.epoch + 1,
                                             choice_id=choice.choice_id) is None
            assert db.entitlements() == db.acquisitions() == []
        assert len(calls) == 1
    finally:
        obj.close()


def test_conflicting_ark_alternate_cannot_supply_a_web_choice(tmp_path):
    from contextlib import closing

    entries = ENTRY.replace("</entry>",
        '<link rel="alternate" type="text/html" href="https://gallica.bnf.fr/ark:/12148/bpt6k1234"/></entry>')
    obj, calls = session(entries)
    try:
        with closing(Storage(tmp_path / "refused.sqlite")) as db:
            response = obj.search_with_response_facets("Notice exacte Gallica", "catalog", field="title")
            assert response.partial is True and response.results == []
            rows = _public_rows_from_response(response, obj)
            assert rows == []
            assert _mirror_remote_results(SimpleNamespace(storage=db), rows,
                                          identity_id="alice", require_public_cover_witness=True) == 0
            assert not search_public_notice_c(db, "Notice exacte Gallica").hits
            assert db.entitlements() == db.acquisitions() == []
        assert len(calls) == 1
    finally:
        obj.close()
