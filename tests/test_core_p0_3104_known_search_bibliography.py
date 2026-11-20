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
