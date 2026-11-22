"""#1023/#4761: grouped C source filters reject rewritten typed provenance."""
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


@pytest.mark.parametrize("source_index", [0, 1])
@pytest.mark.parametrize("alteration", ["valid_subject", "foreign"])
def test_grouped_source_mutated_evidence_never_mints_a_subject(
    tmp_path, source_index, alteration,
):
    with closing(Storage(tmp_path / "source-evidence.sqlite")) as db:
        a = make(db, "bnf", "a", "Deux sources A", "Astronomie", "fr", "EPUB")
        b = make(db, "numilog-catalog", "b", "Deux sources B", "Cuisine", "en", "PDF")
        shared(db, a, b)
        page = search_public_notice_c(db, "Deux sources", group_by_document=True)
        row, = merge_public_notice_c_known_rows([], page, identity_id="alice")
        hits = _sources(row)
        assert hits and len(hits) == 2
        primary = known_public_primary_facts(row)
        assert primary is hits[0]
        selected = hits[source_index]
        assert selected.classification is not None

        if alteration == "foreign":
            class Hostile:
                def __eq__(self, _other):
                    raise AssertionError("foreign evidence equality")
            replacement = Hostile()
        else:
            replacement = (("subjects", ("Forged catalogue subject",)),)
        object.__setattr__(selected.classification, "entries", replacement)
        assert _sources(row) == ()
        assert known_public_primary_facts(row) is (
            None if source_index == 0 else primary
        )
        assert QueryState(
            text="Forged catalogue subject", field="subject",
        ).filter([row]) == []
        assert db.entitlements() == db.acquisitions() == db.placements() == []
