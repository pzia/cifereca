"""Retained series/language proof participates in both C cursor paths."""
from contextlib import closing

from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.gui.query_model import QueryState
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public


def test_series_and_language_search_use_only_exact_source_proofs_after_restart(tmp_path):
    path = tmp_path / "retained-series.sqlite"
    with closing(Storage(path)) as db:
        declared = verified_public(record_id="declared", title="Un volume")
        common = {"source": declared.source_provider + ".fixture",
                  "scope": declared.scenario, "freshness": "search_response"}
        declared.metadata = {
            "series": ["Cycle Orion"], "languages": ["ja"],
            "bibliography_evidence": {
                "series": {**common, "source_field": "Search.series",
                           "confidence": "linked_search_relation"},
                "languages": {**common, "raw_tags": ["jpn"],
                              "source_field": "dc.language",
                              "confidence": "explicit_record_field"},
            },
        }
        other = verified_public(record_id="unproved", title="Autre volume",
                                metadata={"series": ["Cycle Orion"], "languages": ["ja"]})
        first, second = (persist(db, row) for row in (declared, other))
        canonical = persist_exact_public_search_cover(db, first)
        persist_exact_public_search_cover(db, second)

        def check(storage):
            for field, query in (("series", "cycle orion"), ("all", "orion"),
                                 ("language", "ja"), ("all", "orion ja")):
                page = search_public_notice_c(
                    storage, query, field=field, group_by_document=True, now=100,
                )
                hit, = page.hits
                assert hit.document == canonical.document and page.status == "partial"
                row, = merge_public_notice_c_known_rows([], page, identity_id="alice")
                assert QueryState(text=query, field=field).filter([row]) == [row]
            assert not search_public_notice_c(storage, "orion", field="title", now=100).hits
            assert not search_public_notice_c(storage, "ja", field="series", now=100).hits
            assert not search_public_notice_c(storage, "fr", field="language", now=100).hits
            assert storage.entitlements() == [] and storage.acquisitions() == []

        check(db)
        for source in (first, second):
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
    with closing(Storage(path)) as db:
        check(db)
