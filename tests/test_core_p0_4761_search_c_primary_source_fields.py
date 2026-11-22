"""#4761/#2385: a C Search primary cannot change author or medium after projection.

Exact two-source SQLite C, no GTK, provider I/O or new catalogue relation.
"""
from contextlib import closing

import pytest

from cifereca.core.notice_c_known_projection import (
    known_public_primary_facts, merge_public_notice_c_known_rows,
)
from cifereca.core.notice_c_loaded_filter import _sources
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.gui.query_model import QueryState
from cifereca.storage import Storage
from tests.test_core_p0_1023_grouped_c_source_filters import make, shared


@pytest.mark.parametrize("field,value", [
    ("author", "FALSIFIED AUTHOR"),
    ("medium", "physical"),
    ("description", "FALSIFIED DESCRIPTION"),
    ("stale", True),
])
def test_mutated_core_hit_cannot_be_a_primary_source_match(tmp_path, field, value):
    with closing(Storage(tmp_path / "primary-fields.sqlite")) as db:
        a = make(db, "bnf", "a", "Deux sources A", "Astronomie", "fr", "EPUB")
        b = make(db, "numilog-catalog", "b", "Deux sources B", "Cuisine", "en", "PDF")
        shared(db, a, b)
        page = search_public_notice_c(db, "deux sources", group_by_document=True)
        row, = merge_public_notice_c_known_rows([], page, identity_id="alice")
        hit = known_public_primary_facts(row)
        assert hit is not None and _sources(row) is not None
        original = getattr(hit, field)
        changed = value if value != original else not original
        object.__setattr__(hit, field, changed)
        assert known_public_primary_facts(row) is None
        assert _sources(row) == ()
        if field == "author":
            assert QueryState(text="FALSIFIED AUTHOR", field="author").filter([row]) == []
        object.__setattr__(hit, field, original)
        assert known_public_primary_facts(row) is hit
        assert db.entitlements() == [] and db.acquisitions() == []


@pytest.mark.parametrize("name", ["publisher", "description", "notice_c_cover_state", "notice_c_stale"])
def test_mutated_search_display_metadata_cannot_mint_primary_provenance(tmp_path, name):
    with closing(Storage(tmp_path / "primary-meta.sqlite")) as db:
        a = make(db, "bnf", "a", "Deux sources A", "Astronomie", "fr", "EPUB")
        shared(db, a, make(db, "numilog-catalog", "b", "Deux sources B", "Cuisine", "en", "PDF"))
        row, = merge_public_notice_c_known_rows([], search_public_notice_c(
            db, "deux sources", group_by_document=True), identity_id="alice")
        assert known_public_primary_facts(row) is not None
        forged = {**row, "metadata": dict(row["metadata"])}
        forged["metadata"][name] = "FALSIFIED"
        assert known_public_primary_facts(forged) is None
        assert _sources(forged) == ()
