"""#4761: a mutated native C hit cannot invent primary bibliographic facts.

Real SQLite admission; all provider rows synthetic. No HTTP, GTK or rights.
"""
from contextlib import closing

from cifereca.core.notice_c_recommendation_known import (
    known_public_notice_candidates, known_public_c_binding_facts,
    known_public_c_primary_hit,
)
from cifereca.core.notice_c_loaded_filter import matches_known_public_c_filters
from cifereca.storage import Storage
from tests.test_core_p0_1023_grouped_c_source_filters import make, shared


def test_replaced_primary_classification_fails_without_changing_dto(tmp_path):
    with closing(Storage(tmp_path / "primary-facts.sqlite")) as db:
        a = make(db, "bnf", "a", "Deux sources A", "Astronomie", "fr", "EPUB")
        b = make(db, "numilog-catalog", "b", "Deux sources B", "Cuisine", "en", "PDF")
        canonical = shared(db, a, b)
        candidate, = known_public_notice_candidates(db, "Deux sources")
        assert known_public_c_binding_facts(candidate) == (canonical.document, canonical.epoch)
        proof = candidate._core_known_public_c_projection
        primary = proof[6]
        alternative, = proof[5]
        assert primary.classification is not None and alternative.classification is not None
        assert known_public_c_primary_hit(candidate) is primary
        assert matches_known_public_c_filters(candidate, query="Astronomie", field="subject",
                scope="all", providers=("bnf",), content_types=(), languages=()) is True
        # Keep the same exact DocumentRef, epoch, record and DTO; change only
        # a native, independently valid subject proof held by the packet.
        object.__setattr__(primary, "classification", alternative.classification)
        assert known_public_c_binding_facts(candidate) == (canonical.document, canonical.epoch)
        assert known_public_c_primary_hit(candidate) is None
        assert matches_known_public_c_filters(candidate, query="Cuisine", field="subject",
                scope="all", providers=("bnf",), content_types=(), languages=()) is False
        assert db.entitlements() == [] and db.acquisitions() == []


def test_mutated_status_flag_cannot_rewrite_original_c_receipt(tmp_path):
    with closing(Storage(tmp_path / "status.sqlite")) as db:
        a = make(db, "bnf", "a", "Deux sources A", "Astronomie", "fr", "EPUB")
        shared(db, a, make(db, "numilog-catalog", "b", "Deux sources B", "Cuisine", "en", "PDF"))
        candidate, = known_public_notice_candidates(db, "Deux sources")
        hit = candidate._core_known_public_c_projection[6]
        assert known_public_c_primary_hit(candidate) is hit
        object.__setattr__(hit, "stale", not hit.stale)
        assert known_public_c_primary_hit(candidate) is None
