"""A bounded receipt refusal cannot poison another canonical C on the page."""
from contextlib import closing
from dataclasses import replace

from cifereca.core import notice_c_recommendation_known as known
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.storage import Storage
from tests.test_core_p0_6366_multisource_c_search import sources, shared
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public


def test_over_budget_group_does_not_abort_independent_document(tmp_path, monkeypatch):
    with closing(Storage(tmp_path / "bounded.sqlite")) as db:
        first, second = sources(db)
        canonical = shared(db, first, second)
        third = persist(db, verified_public(
            record_id="independent", title="Deux sources indépendantes",
        ), at=100)
        independent = persist_exact_public_search_cover(db, third)
        page = search_public_notice_c(db, "deux sources", group_by_document=True)
        primary = next(hit for hit in page.hits if hit.document == canonical.document)
        good = next(hit for hit in page.hits if hit.document == independent.document)
        # Inject pressure at the typed cursor boundary. Actual snapshot
        # check, real document coordinates, no mocked receipt validator.
        oversized = replace(primary, description="x" * 8193)
        pressured = replace(page, hits=(oversized, good),
                            matching_sources=(oversized, good))
        monkeypatch.setattr(known, "search_public_notice_c", lambda *a, **k: pressured)
        result, = known.known_public_notice_candidates(db, "deux sources")
        assert known.known_public_c_binding_facts(result) == (
            independent.document, independent.epoch,
        )
        assert result.owned is result.downloadable is None
        assert db.entitlements() == [] and db.acquisitions() == []
        monkeypatch.setattr(known, "search_public_notice_c", lambda *a, **k: page)
        assert len(known.known_public_notice_candidates(db, "deux sources")) == 2
