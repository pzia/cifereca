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
        field, = [field for field in separate.fields if field.label.endswith(" · Sujet")]
        assert field.value == alternative
        assert field.kind == "text"
        source = item["metadata"]["notice_c_source_alternatives"][0]
        assert field.provenance.source == "Notice C · observation de source"
        assert field.provenance.provider_id == source["provider_id"]
        assert field.provenance.source_id == source["record_id"]
        # A forged value remains passive rather than acquiring exact provenance.
        from copy import deepcopy
        altered = deepcopy(item["metadata"])
        altered["notice_c_source_alternatives"][0]["subjects"] = ["Fait modifié"]
        forged = {**item, "metadata": altered}
        modified_fields = known_public_alternative_section(altered, item=forged).fields
        forged_field, = [field for field in modified_fields if "Fait modifié" in field.value]
        assert forged_field.provenance is None
        genre, = [field for field in modified_fields if field.label.endswith(" · Genre")]
        assert genre.provenance.provider_id == source["provider_id"]
        assert genre.provenance.source_id == source["record_id"]
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
    assert field.provenance is None
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

        # Exercise the same fields through the actual mapped Search detail,
        # not just the pure presentation decorator.
        ui = _host()
        ui._identity_context_generation = 2
        ui._search_generation = 3
        ui.service._account_context_epoch = 5
        ui.service._source_context_epoch = 7
        ui._populate_local_search([item])
        mapped, = ui.search_page.show_rows.call_args.args[0]
        panel = NS(show_presentation=Mock(), empty=Mock())
        host = NS(
            _closed=False, _search_detail_request_generation=0,
            _cancel_cached_book_details=Mock(),
            _search_local_objects={item["key"]: item}, _selected_search_id=None,
            service=ui.service, _search_mode="local",
            _identity_context_generation=2, _search_generation=3,
            search_page=NS(detail=panel, selection=NS(get_selected_item=lambda: mapped)),
        )
        _detail_selected()(host, mapped)
        panel.empty.assert_not_called()
        visible = panel.show_presentation.call_args.args[0]
        observed = {f.label: f.provenance for section in visible.sections
                    for f in section.fields if f.label in {"Série", "Langue"}}
        assert observed == {"Série": series.provenance, "Langue": language.provenance}
        assert visible.cover_document_ref == item["_cover_document_ref"]
        assert not visible.actions
        for forged in ({k: v for k, v in item.items() if k != "_core_notice_c_primary_facts"},
                       {**item, "item_id": "other"},
                       {**item, "identity_id": "bob"},
                       {**item, "metadata": {**meta, "series": ["Other"], "languages": ["fr"]}}):
            assert not any(f.provenance for sec in present(forged).sections for f in sec.fields)
        # A view row can be modified after its Core receipt was attached.
        # Never call an injected object's equality or decorate that field
        # with trusted provenance; the independent other field still qualifies.
        class HostileEquals:
            def __eq__(self, other):
                raise AssertionError("provider-defined equality executed")

        from cifereca.gui.search_known_public_c_bibliography import (
            with_known_public_field_provenance,
        )
        authentic = book_detail_presentation(
            title=item["title"], metadata=known_public_detail_metadata(meta),
        )
        for field_name, untrusted_value, lost_label, surviving_label in (
            ("series", HostileEquals(), "Série", "Langue"),
            ("languages", [HostileEquals()], "Langue", "Série"),
            ("series", ("Cycle Orion",), "Série", "Langue"),
            ("languages", ("ja",), "Langue", "Série"),
        ):
            modified = {**item, "metadata": {**meta, field_name: untrusted_value}}
            safe = with_known_public_field_provenance(authentic, modified)
            by_label = {f.label: f for sec in safe.sections for f in sec.fields}
            assert by_label[lost_label].provenance is None
            assert by_label[surviving_label].provenance is not None
        assert db.entitlements() == [] and db.acquisitions() == []


def test_alternative_series_and_language_keep_their_own_provenance_after_restart(tmp_path):
    from cifereca.gui.detail_field_provenance import detail_field_provenance

    path = tmp_path / "two-source-provenance.sqlite"
    with closing(Storage(path)) as db:
        sources = []
        for provider, record, series, language in (
            ("bnf", "record-a", "Cycle Orion", "ja"),
            ("numilog-catalog", "record-b", "Cycle Vega", "fr"),
        ):
            row = verified_public(source_provider=provider, record_id=record,
                                  title="Deux séries")
            common = {"source": provider + ".fixture", "scope": row.scenario,
                      "freshness": "search_response"}
            row.metadata = {
                "series": [series], "languages": [language],
                "bibliography_evidence": {
                    "series": {**common, "source_field": "Search.series",
                               "confidence": "linked_search_relation"},
                    "languages": {**common, "raw_tags": [language],
                                  "source_field": "dc.language",
                                  "confidence": "explicit_record_field"},
                },
            }
            sources.append(persist(db, row))
        group = shared(db, *sources)
        for source in sources:
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
    with closing(Storage(path)) as db:
        page = search_public_notice_c(db, "Deux séries", group_by_document=True)
        item, = merge_public_notice_c_known_rows([], page, identity_id="alice")
        assert item["_cover_document_ref"] == group.document
        meta = item["metadata"]
        alternative, = meta["notice_c_source_alternatives"]
        expected_provider = alternative["provider_id"]
        expected_record = alternative["record_id"]

        def section(row):
            return known_public_alternative_section(row["metadata"], item=row)

        fields = section(item).fields
        series, = [f for f in fields if f.label.endswith(" · Série")]
        language, = [f for f in fields if f.label.endswith(" · Langue")]
        assert series.value == alternative["series"][0]
        assert language.value == alternative["languages"][0]
        assert series.value != meta["series"][0] and language.value != meta["languages"][0]
        assert series.provenance.provider_id == language.provenance.provider_id == expected_provider
        assert series.provenance.source_id == language.provenance.source_id == expected_record
        assert series.provenance.observed_at == language.provenance.observed_at
        assert "Search.series" in detail_field_provenance(series)
        assert "dc.language" in detail_field_provenance(language)
        assert all(f.kind == "text" for f in fields)

        ui = _host()
        ui._identity_context_generation = 2
        ui._search_generation = 3
        ui.service._account_context_epoch = 5
        ui.service._source_context_epoch = 7
        ui._populate_local_search([item])
        mapped, = ui.search_page.show_rows.call_args.args[0]
        panel = NS(show_presentation=Mock(), empty=Mock())
        host = NS(_closed=False, _search_detail_request_generation=0,
                  _cancel_cached_book_details=Mock(),
                  _search_local_objects={item["key"]: item}, _selected_search_id=None,
                  service=ui.service, _search_mode="local",
                  _identity_context_generation=2, _search_generation=3,
                  search_page=NS(detail=panel, selection=NS(get_selected_item=lambda: mapped)))
        _detail_selected()(host, mapped)
        panel.empty.assert_not_called()
        presentation = panel.show_presentation.call_args.args[0]
        visible, = [part for part in presentation.sections if part.id == "known-public-alternatives"]
        assert visible == section(item)
        assert not presentation.actions and not presentation.links

        def changed(**changes):
            return {**item, "metadata": {**meta, "notice_c_source_alternatives": [
                {**alternative, **changes},
            ]}}

        for forged in (
            {k: v for k, v in item.items() if k != "_core_notice_c_filter_sources"},
            {k: v for k, v in item.items() if k != "_core_notice_c_primary_facts"},
            {**item, "identity_id": "bob"},
            {**item, "metadata": {**meta, "notice_c_epoch": True}},
            changed(record_id="different"),
            changed(provider_id="another"),
            changed(title="Different title"),
            changed(series=["Different series"], languages=["en"]),
        ):
            assert all(f.provenance is None for f in section(forged).fields)
        # One modified field does not erase independent evidence on the other.
        remaining = [f for f in section(changed(series=["Other"])).fields if f.provenance]
        assert remaining == [language]
        assert all(f.provenance is None for f in known_public_alternative_section(meta).fields)
        assert db.entitlements() == [] and db.acquisitions() == []


def test_plain_primary_catalogue_fields_keep_exact_source_provenance(tmp_path):
    from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
    from cifereca.gui.detail_model import book_detail_presentation
    from cifereca.gui.search_known_public_c_bibliography import with_known_public_field_provenance

    path = tmp_path / "catalogue-provenance.sqlite"
    with closing(Storage(path)) as db:
        source = persist(db, verified_public(record_id="exact-catalogue",
            title="Notice primaire", publisher="Éditeur témoin", published_at="2025-01-02",
        ), at=100)
        persist_exact_public_search_cover(db, source)
        assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
    with closing(Storage(path)) as db:
        item, = merge_public_notice_c_known_rows(
            [], search_public_notice_c(db, "Notice primaire", now=100),
            identity_id="alice",
        )
        def present(row, *, display_override=None):
            values = [("Format", row["media_format"]),
                      ("Éditeur", row["metadata"]["publisher"]),
                      ("Publication", row["published_at"])]
            if display_override is not None:
                values[1] = ("Éditeur", display_override)
            p = book_detail_presentation(title=row["title"], section_rows=(
                ("Catalogue", values),
                ("Bibliothèque", values),
            ))
            return with_known_public_field_provenance(p, row)

        p = present(item)
        catalogue, local = p.sections
        assert {f.label for f in catalogue.fields} == {"Format", "Éditeur", "Publication"}
        for field in catalogue.fields:
            assert field.provenance.source == "Notice C · observation de source"
            assert field.provenance.provider_id == item["provider_id"]
            assert field.provenance.source_id == "exact-catalogue"
            assert field.provenance.observed_at == "1970-01-01T00:01:40+00:00"
        assert all(f.provenance is None for f in local.fields)
        changed = {**item, "metadata": {**item["metadata"], "publisher": "Autre éditeur"}}
        for altered in (present(changed), present(item, display_override="Autre éditeur")):
            fields = {f.label: f.provenance for f in altered.sections[0].fields}
            assert fields["Éditeur"] is None
            assert fields["Format"] is not None and fields["Publication"] is not None
        assert all(f.provenance is None for f in present({**item, "identity_id": "bob"}).sections[0].fields)
        assert not p.actions and not p.links
        assert db.entitlements() == [] and db.acquisitions() == []



def test_primary_c_classification_provenance_does_not_attach_to_local_sections(tmp_path):
    from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
    from cifereca.gui.detail_model import book_detail_presentation
    from cifereca.gui.search_known_public_c_bibliography import (
        with_known_public_field_provenance,
    )

    path = tmp_path / "source-classification.sqlite"
    with closing(Storage(path)) as db:
        source = persist(db, verified_public(
            record_id="classified", title="Notice classée",
            metadata={"subjects": ["Astronomie"], "genre": "Roman",
                      "keywords": ["Espace"], "tags": ["Cosmos"],
                      "themes": ["Galaxies"]},
        ), at=100)
        persist_exact_public_search_cover(db, source)
        assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)

    with closing(Storage(path)) as db:
        item, = merge_public_notice_c_known_rows(
            [], search_public_notice_c(db, "Notice classée", now=100),
            identity_id="alice",
        )
        metadata = item["metadata"]
        presentation = book_detail_presentation(
            title=item["title"],
            metadata=known_public_detail_metadata(metadata),
            section_rows=(("Bibliothèque", (
                ("Sujet", "Astronomie"), ("Genre", "Roman"),
            )),),
        )
        visible = with_known_public_field_provenance(presentation, item)
        by_section = {
            section.title: {f.label: f for f in section.fields}
            for section in visible.sections
        }
        for label in ("Sujet", "Genre", "Thème"):
            field = by_section["Détail"][label]
            assert field.provenance.source == "Notice C · observation de source"
            assert field.provenance.provider_id == item["provider_id"]
            assert field.provenance.source_id == "classified"
            assert field.provenance.observed_at == "1970-01-01T00:01:40+00:00"
        for label in ("Sujet", "Genre"):
            assert by_section["Bibliothèque"][label].provenance is None
        keywords = [f for sec in visible.sections if sec.title == "Détail"
                    for f in sec.fields if f.label == "Mot-clé"]
        assert {field.value for field in keywords} == {"Espace", "Cosmos"}
        assert all(field.provenance.source == "Notice C · observation de source"
                   for field in keywords)

        class Hostile:
            def __eq__(self, other):
                raise AssertionError("foreign classification equality executed")

        for supplied in (["Histoire"], ("Astronomie",), [Hostile()]):
            changed = {**item, "metadata": {**metadata, "subjects": supplied}}
            altered = with_known_public_field_provenance(presentation, changed)
            fields = {f.label: f for sec in altered.sections if sec.title == "Détail"
                      for f in sec.fields}
            assert fields["Sujet"].provenance is None
            assert fields["Genre"].provenance == by_section["Détail"]["Genre"].provenance
        assert db.entitlements() == db.acquisitions() == db.placements() == []
