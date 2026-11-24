"""#2385: the six C modes share one read model but timeline pages are global.

Fixtures are admitted public documentary sources; no provider request, loan,
entitlement, or profile-level catalogue mirror is used.
"""
from __future__ import annotations

import pytest

from cifereca.core.notice_c_catalogue_query import search_public_catalogue_view
from cifereca.core.notice_c_cover_index import (
    persist_consolidated_cover_source, persist_exact_public_search_cover,
)
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import (
    persist, verified_public,
)


def test_timeline_pages_follow_global_recency_across_restart_and_source_eviction(tmp_path):
    filename = tmp_path / "c-timeline.sqlite"
    db = Storage(filename)
    saved = []
    try:
        # Insert the alphabetically first row first, but observe it oldest.
        for name, observed_at in (("alpha", 100), ("beta", 200), ("gamma", 300)):
            source = persist(db, verified_public(
                source_provider="seller-" + name, record_id="record-" + name,
                title="Histoire " + name,
            ), at=observed_at)
            saved.append(source)
            persist_exact_public_search_cover(db, source)

        def timeline(storage):
            pages = [
                search_public_catalogue_view(
                    storage, "histoire", mode="timeline", page=n,
                    page_size=1, now=400,
                ) for n in range(4)
            ]
            assert all(page.known_documents == 3 and page.known_matches == 3
                       for page in pages)
            assert [page.has_more for page in pages] == [True, True, False, False]
            assert [page.rows[0].record_id for page in pages[:3]] == [
                "record-gamma", "record-beta", "record-alpha",
            ]
            assert not pages[3].rows
            assert [page.rows[0].last_seen_at for page in pages[:3]] == [
                300, 200, 100,
            ]

        timeline(db)
        for source in saved:
            assert evict_notice_cover(
                db, source.document, expected_epoch=source.epoch,
            )
    finally:
        db.close()

    # The retained C witness still determines the same ordering after reboot.
    reopened = Storage(filename)
    try:
        timeline(reopened)
        assert reopened.entitlements() == [] and reopened.acquisitions() == []
    finally:
        reopened.close()


def test_timeline_uses_freshest_attested_source_of_one_c_before_paging(tmp_path):
    db = Storage(tmp_path / "c-timeline-group.sqlite")
    try:
        first = persist(db, verified_public(
            source_provider="bnf", record_id="source-alpha",
            title="Histoire première source",
        ), at=100)
        latest = persist(db, verified_public(
            source_provider="numilog-catalog", record_id="source-gamma",
            title="Histoire seconde source",
        ), at=300)
        target = first.observation.key.edition_id
        persist_consolidated_cover_source(
            db, canonical_edition_id=target, source=first,
            relation_proof_id="strong:fixture:bnf",
        )
        joined = persist_consolidated_cover_source(
            db, canonical_edition_id=target, source=latest,
            relation_proof_id="strong:fixture:numilog",
        )
        middle = persist(db, verified_public(
            source_provider="seller-beta", record_id="source-beta",
            title="Histoire indépendante",
        ), at=200)
        persist_exact_public_search_cover(db, middle)

        first_page = search_public_catalogue_view(
            db, "histoire", mode="timeline", page=0, page_size=1, now=400,
        )
        second_page = search_public_catalogue_view(
            db, "histoire", mode="timeline", page=1, page_size=1, now=400,
        )
        assert first_page.known_matches == second_page.known_matches == 3
        assert first_page.known_documents == second_page.known_documents == 2
        assert first_page.rows[0].document == joined.document
        assert first_page.rows[0].record_id == "source-gamma"
        assert first_page.rows[0].last_seen_at == 300
        assert first_page.rows[0].source_count == 2
        assert second_page.rows[0].record_id == "source-beta"
        assert second_page.rows[0].last_seen_at == 200
        assert first_page.has_more and not second_page.has_more

        details = search_public_notice_c(
            db, "histoire", group_by_document=True, sort_by_recency=True,
            page=0, page_size=1, now=400,
        )
        assert {source.provider_id for source in details.matching_sources} == {
            "bnf", "numilog-catalog",
        }
        with pytest.raises(ValueError, match="invalid_notice_c_search_request"):
            search_public_notice_c(
                db, "histoire", group_by_document=True, sort_by_recency=1,
            )
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
