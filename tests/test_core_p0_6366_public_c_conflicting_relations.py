"""P0 #6366: contradictory same-edition C claims do not become truth by size.

Both the full-coordinate reader and C-first Search must prefer the source's
independent canonical C over conflicting multi-provider groups; with no
independent C the exact source-to-canonical route remains unknown.
"""
from __future__ import annotations

from cifereca.core.notice_c_cover_index import (
    persist_consolidated_cover_source, persist_exact_public_search_cover,
)
from cifereca.core.notice_c_exact_source_lookup import exact_public_source_c
from cifereca.storage import Storage
from tests.test_core_p0_6366_exact_public_c_source_lookup import make, find


def group(db, name, *sources):
    result = None
    for n, source in enumerate(sources):
        result = persist_consolidated_cover_source(
            db, canonical_edition_id="edition:" + name,
            source=source, relation_proof_id=f"strong:fixture:{name}:{n}",
        )
    return result


def test_unequal_conflicting_foreign_groups_keep_independent_source(tmp_path):
    db = Storage(tmp_path / "conflicting-group-sizes.sqlite")
    try:
        a = make(db, "bnf", "same-edition-a")
        b = make(db, "numilog-catalog", "same-edition-b")
        c = make(db, "paris", "same-edition-c")
        d = make(db, "catalog-4", "same-edition-d")
        own = persist_exact_public_search_cover(db, a)
        short = group(db, "conflict-short", a, b)
        long = group(db, "conflict-long", a, c, d)
        assert len(short.sources) == 2
        assert len(long.sources) == 3
        assert find(db, a).document == own.document
        assert find(db, b).document == short.document
        assert find(db, c).document == long.document
        assert find(db, d).document == long.document
        assert not db.entitlements()
    finally:
        db.close()


def test_unequal_conflicts_without_source_self_are_ambiguous(tmp_path):
    db = Storage(tmp_path / "conflicts-no-self.sqlite")
    try:
        a = make(db, "bnf", "record-a")
        b = make(db, "numilog-catalog", "record-b")
        c = make(db, "paris", "record-c")
        d = make(db, "catalog-4", "record-d")
        group(db, "conflict-short", a, b)
        group(db, "conflict-long", a, c, d)
        assert find(db, a) is None
        assert find(db, b) is not None
        assert find(db, c) is not None
        assert not db.acquisitions()
    finally:
        db.close()


def test_foreign_single_source_record_is_not_an_edition_proof(tmp_path):
    db = Storage(tmp_path / "foreign-singleton.sqlite")
    try:
        a = make(db, "bnf", "record-a")
        group(db, "some-other-canonical-edition", a)
        assert find(db, a) is None
        own = persist_exact_public_search_cover(db, a)
        assert find(db, a).document == own.document
    finally:
        db.close()


def test_unique_multisource_relation_still_supersedes_independent_c(tmp_path):
    db = Storage(tmp_path / "unique-relation.sqlite")
    try:
        a = make(db, "bnf", "record-a")
        b = make(db, "numilog-catalog", "record-b")
        own = persist_exact_public_search_cover(db, a)
        joined = group(db, "uniquely-attested", a, b)
        assert find(db, a).document == joined.document
        assert own.document != joined.document
    finally:
        db.close()
