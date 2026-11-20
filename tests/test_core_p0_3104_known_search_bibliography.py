"""Retained C bibliography appears in actual Search detail after restart."""
from contextlib import closing
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.gui.search_known_public_c_bibliography import (
    known_public_detail_metadata, known_public_alternative_section,
)
from cifereca.storage import Storage
from tests.test_core_p0_6366_multisource_c_search import shared
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public
from tests.test_core_p0_6366_c_first_gtk_list_detail import _detail_selected
from tests.test_search_results_view import _host


def test_retained_primary_and_alternative_facts_render_separately(tmp_path):
    path = tmp_path / "bibliography.sqlite"
    with closing(Storage(path)) as db:
        a = persist(db, verified_public(
            source_provider="bnf", record_id="edition-a", title="Deux sources",
            author="Auteur A", metadata={"subjects": ["Astronomie"], "genre": "Roman"},
        ))
        b = persist(db, verified_public(
            source_provider="numilog-catalog", record_id="edition-b", title="Deux sources",
            author="Auteur B", metadata={"subjects": ["Cuisine"], "genre": "Essai"},
        ))
        group = shared(db, a, b)
        for source in (a, b):
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
    with closing(Storage(path)) as db:
        page = search_public_notice_c(db, "deux sources", group_by_document=True)
        item, = merge_public_notice_c_known_rows([], page, identity_id="alice")
        assert item["_cover_document_ref"] == group.document
        primary, alternative = (
            ("Astronomie", "Cuisine") if item["provider_id"] == "bnf"
            else ("Cuisine", "Astronomie")
        )
        ui = _host()
        ui._identity_context_generation = 2
        ui._search_generation = 3
        ui.service._account_context_epoch = 5
        ui.service._source_context_epoch = 7
        ui._populate_local_search([item])
        row, = ui.search_page.show_rows.call_args.args[0]
        panel = NS(show_presentation=Mock(), empty=Mock())
        host = NS(
            _closed=False, _search_detail_request_generation=0,
            _cancel_cached_book_details=Mock(),
            _search_local_objects={item["key"]: item}, _selected_search_id=None,
            service=ui.service, _search_mode="local",
            _identity_context_generation=2, _search_generation=3,
            search_page=NS(detail=panel, selection=NS(get_selected_item=lambda: row)),
        )
        _detail_selected()(host, row)
        panel.empty.assert_not_called()
        presentation = panel.show_presentation.call_args.args[0]
        assert presentation.cover_document_ref == group.document
        fields = [field for section in presentation.sections if section.title == "Détail"
                  for field in section.fields]
        assert any(field.label == "Sujet" and field.value == primary for field in fields)
        assert not any(field.value == alternative for field in fields)
        separate, = [section for section in presentation.sections
                     if section.id == "known-public-alternatives"]
        field, = separate.fields
        assert "Sujets : " + alternative in field.value
        assert field.kind == "text"
        assert not presentation.actions and not presentation.links
        assert db.entitlements() == [] and db.acquisitions() == []


def test_only_native_safe_bibliography_and_explicit_limits_reach_detail():
    data = {
        "subjects": ["Astronomie", {"value": "unsafe"}],
        "rights": "owned", "license": "private",
        "notice_c_source_alternatives": [
            {"provider_id": "bad://provider", "title": "Unknown"},
            {"provider_id": "seller-b", "title": "Second",
             "subjects": [f"Sujet {i}" for i in range(10)],
             "description": "x" * 700, "record_id": "OPAQUE",
             "download_url": "https://private.invalid/"},
        ],
    }
    assert known_public_detail_metadata(data) == {"subjects": ["Astronomie"]}
    section = known_public_alternative_section(data)
    field, = section.fields
    assert field.label == "Source 3 · seller-b"
    assert "Sujet 7" in field.value and "Sujet 8" not in field.value
    assert field.value.endswith("x" * 512 + "…")
    assert "OPAQUE" not in field.value and "https://" not in field.value
    data["notice_c_source_alternatives"] *= 16
    assert known_public_alternative_section(data) is None


def test_primary_field_provenance_survives_eviction_and_restart(tmp_path):
    from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
    from cifereca.gui.detail_model import book_detail_presentation
    from cifereca.gui.search_known_public_c_bibliography import with_known_public_field_provenance
    from cifereca.gui.detail_field_provenance import detail_field_provenance

    path = tmp_path / "typed-facts.sqlite"
    with closing(Storage(path)) as db:
        row = verified_public(record_id="primary", title="Un volume")
        common = {"source": row.source_provider + ".fixture",
                  "scope": row.scenario, "freshness": "search_response"}
        row.metadata = {
            "series": ["Cycle Orion"], "languages": ["ja"],
            "bibliography_evidence": {
                "series": {**common, "source_field": "Search.series",
                           "confidence": "linked_search_relation"},
                "languages": {**common, "raw_tags": ["ja"],
                              "source_field": "dc.language",
                              "confidence": "explicit_record_field"},
            },
        }
        src = persist(db, row)
        persist_exact_public_search_cover(db, src)
        assert evict_notice_cover(db, src.document, expected_epoch=src.epoch)
    with closing(Storage(path)) as db:
        page = search_public_notice_c(db, "Un volume", group_by_document=True, now=100)
        item, = merge_public_notice_c_known_rows([], page, identity_id="alice")
        meta = item["metadata"]
        def present(candidate):
            p = book_detail_presentation(title=item["title"],
                metadata=known_public_detail_metadata(candidate["metadata"]))
            return with_known_public_field_provenance(p, candidate)

        fields = [f for section in present(item).sections for f in section.fields]
        series, = [f for f in fields if f.label == "Série"]
        language, = [f for f in fields if f.label == "Langue"]
        assert series.value == "Cycle Orion" and language.value == "ja"
        assert series.provenance.source_id == language.provenance.source_id == "primary"
        assert series.provenance.provider_id == item["provider_id"]
        assert "Search.series" in detail_field_provenance(series)
        assert "dc.language" in detail_field_provenance(language)
        assert series.provenance.observed_at == language.provenance.observed_at
        assert not present(item).actions
        for forged in ({k: v for k, v in item.items() if k != "_core_notice_c_primary_facts"},
                       {**item, "item_id": "other"},
                       {**item, "identity_id": "bob"},
                       {**item, "metadata": {**meta, "series": ["Other"], "languages": ["fr"]}}):
            assert not any(f.provenance for sec in present(forged).sections for f in sec.fields)
        assert db.entitlements() == [] and db.acquisitions() == []
