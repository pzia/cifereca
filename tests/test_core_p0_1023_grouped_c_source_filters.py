"""Grouped C filters require one independent source to satisfy every predicate."""
from contextlib import closing
from dataclasses import replace

from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_c_loaded_filter import matching_source_label
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.gui.query_model import QueryState
from cifereca.storage import Storage
from tests.test_core_p0_6366_multisource_c_search import shared
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public


def make(db, provider, record, title, subject, lang, fmt):
    row = verified_public(source_provider=provider, record_id=record,
                          title=title, format=fmt)
    row.metadata = {
        "subjects": [subject], "languages": [lang],
        "bibliography_evidence": {"languages": {
            "raw_tags": [lang], "source": provider + ".fixture",
            "source_field": "dc.language", "scope": row.scenario,
            "confidence": "explicit_record_field", "freshness": "search_response",
        }},
    }
    return persist(db, row)


def test_filter_contributions_after_real_sqlite_restart(tmp_path):
    path = tmp_path / "source-filters.sqlite"
    with closing(Storage(path)) as db:
        a = make(db, "bnf", "a", "Deux sources A", "Astronomie", "fr", "EPUB")
        b = make(db, "numilog-catalog", "b", "Deux sources B", "Cuisine", "en", "PDF")
        group = shared(db, a, b)
        for source in (a, b):
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
    with closing(Storage(path)) as db:
        page = search_public_notice_c(db, "deux sources", group_by_document=True)
        row, = merge_public_notice_c_known_rows([], page, identity_id="alice")
        assert row["_cover_document_ref"] == group.document
        assert row["provider_id"] == "bnf"
        state = QueryState(text="cuisine", field="subject", providers=("numilog-catalog",),
                           types=("pdf",), languages=("en",), scope="digital")
        selected, = state.filter([row])
        assert selected["provider_id"] == "bnf"
        assert selected["metadata"] is row["metadata"]
        assert selected["_cover_document_ref"] == row["_cover_document_ref"]
        assert selected["_notice_c_matched_providers"] == ("numilog-catalog",)
        assert "numilog-catalog" in matching_source_label(selected)
        assert "_notice_c_matched_providers" not in row
        from cifereca.core.notice_c_loaded_filter import matching_source_reason
        all_fields = replace(state, field="all")
        explained, = all_fields.filter([row])
        assert matching_source_reason(explained, "cuisine") == "Correspondance : Sujet"
        assert matching_source_reason(explained, "astronomie") == ""
        assert matching_source_reason(explained, "cuisine", field="subject") == ""
        assert matching_source_reason(row, "cuisine") == ""  # unfiltered group
        stale_reason = dict(explained, metadata={**explained["metadata"],
                                               "notice_c_epoch": group.epoch + 1})
        assert matching_source_reason(stale_reason, "cuisine") == ""
        for bad in (
            replace(state, providers=("bnf",)),
            replace(state, types=("epub",)),
            replace(state, languages=("fr",)),
            replace(state, scope="physical"),
            replace(state, text="astronomie cuisine", field="all", providers=(),
                    types=(), languages=()),
        ):
            assert bad.filter([row]) == []
        both, = QueryState(text="deux sources").filter([row])
        assert set(both["_notice_c_matched_providers"]) == {"bnf", "numilog-catalog"}
        stale = dict(row, metadata={**row["metadata"], "notice_c_epoch": group.epoch + 1})
        assert state.filter([stale]) == []
        fake = {key: value for key, value in row.items()
                if key != "_core_notice_c_filter_sources"}
        assert state.filter([fake]) == []

        # Python bool is an int subclass: epoch=True must never be
        # accepted as a sealed generation 1 for the grouped source filter.
        # Recreate a genuine Core source packet at epoch 1 so this test is
        # independent from the initial SQLite canonical revision number.
        from cifereca.core.notice_c_loaded_filter import _sources, attach_filter_sources
        epoch_one_hits = tuple(replace(hit, epoch=1) for hit in page.matching_sources)
        # Re-stamping only the group must fail: its original primary receipt
        # still refers to the prior hit object, not the replacement.
        forged = dict(row, metadata={**row["metadata"], "notice_c_epoch": 1})
        attach_filter_sources(forged, epoch_one_hits)
        assert _sources(forged) == ()

        # Exercise the native projector so both the primary receipt and the
        # group packet are issued from the same exact epoch-one hit objects.
        strict_page = replace(
            page, hits=tuple(replace(hit, epoch=1) for hit in page.hits),
            matching_sources=epoch_one_hits,
        )
        strict, = merge_public_notice_c_known_rows([], strict_page, identity_id="alice")
        assert strict["metadata"]["notice_c_epoch"] == 1
        assert _sources(strict) == epoch_one_hits
        coerced = dict(strict, metadata={**strict["metadata"], "notice_c_epoch": True})
        assert _sources(coerced) == ()
        assert state.filter([coerced]) == []
        negative = dict(strict, metadata={**strict["metadata"], "notice_c_epoch": False})
        assert _sources(negative) == ()
        assert db.entitlements() == [] and db.acquisitions() == []


def test_grouped_search_recommendations_full_filter_matrix_after_restart(tmp_path):
    """Receive #4761 for the same exact C, not a second recommendation index."""
    from cifereca.core.notice_c_loaded_filter import matches_known_public_c_filters
    from cifereca.core.notice_c_recommendation_known import (
        known_public_notice_candidates, known_public_c_binding_facts,
    )

    filename = tmp_path / "parity.sqlite"
    with closing(Storage(filename)) as db:
        a = make(db, "bnf", "a", "Deux sources A", "Astronomie", "fr", "EPUB")
        b = make(db, "numilog-catalog", "b", "Deux sources B", "Cuisine", "en", "PDF")
        canonical = shared(db, a, b)
        for source in (a, b):
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
    with closing(Storage(filename)) as db:
        candidate, = known_public_notice_candidates(db, "deux sources")
        assert known_public_c_binding_facts(candidate) == (canonical.document, canonical.epoch)
        for identity in ("alice", "bob", "carol"):
            page = search_public_notice_c(db, "deux sources", group_by_document=True)
            row, = merge_public_notice_c_known_rows([], page, identity_id=identity)
            base = QueryState(text="cuisine", field="subject", providers=("numilog-catalog",),
                              types=("pdf",), languages=("en",), scope="digital")
            for state, expected in (
                (base, True),
                (replace(base, providers=("bnf",)), False),
                (replace(base, types=("epub",)), False),
                (replace(base, languages=("fr",)), False),
                (replace(base, scope="physical"), False),
                (replace(base, text="", providers=(), types=(), languages=(), scope="all"), True),
                (replace(base, text="astronomie", providers=("bnf",),
                         types=("epub",), languages=("fr",)), True),
                (replace(base, text="astronomie cuisine", field="all",
                         providers=(), types=(), languages=()), False),
            ):
                search_rows = state.filter([row])
                assert bool(search_rows) is expected
                assert matches_known_public_c_filters(
                    candidate, query=state.text, field=state.field, scope=state.scope,
                    providers=state.providers, content_types=state.types,
                    languages=state.languages,
                ) is expected
                if expected:
                    assert search_rows[0]["_cover_document_ref"] == canonical.document
                    assert search_rows[0]["provider_id"] == row["provider_id"]
                assert known_public_c_binding_facts(candidate) == (canonical.document, canonical.epoch)
            assert "_notice_c_matched_providers" not in row
        assert db.entitlements() == db.acquisitions() == db.placements() == []
