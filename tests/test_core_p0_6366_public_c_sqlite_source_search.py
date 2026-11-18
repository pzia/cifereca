"""P0 #6366: public C source bibliography is searchable after cache expiry.

This first stage is a bounded, read-only, independent C source cursor,
not an account- or ISBN-joined catalogue and never a complete query answer.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import (
    persist_notice_cover, read_notice_cover,
)
from cifereca.core.notice_search_cover_producer import (
    admit_public_search_cover, admit_public_search_catalogue,
    _CORE_PUBLIC_C_WITNESS,
)
from cifereca.storage import Storage
from tests.test_core_p0_6366_real_search_notice_c_ingestion import (
    public_row, URL,
)
from tests.test_core_p1_2385_notice_cover_persistence import (
    PRIVATE, notice,
)


def verified_public(**kwargs):
    row = public_row(**kwargs)
    # This flag is assigned by the Core same-call anonymous projector; only
    # test fixtures attach it directly to model a previously verified result.
    row._core_notice_c_public_bibliography = _CORE_PUBLIC_C_WITNESS
    return row


def persist(db, row, at=100):
    obs = admit_public_search_catalogue(
        row, identity_id="alice", observed_at=at,
        public_cover_url=row.thumbnail_url,
    )
    assert obs is not None
    return persist_notice_cover(
        db, obs, field_path=("search_result", "thumbnail_url"),
        cover_inventory_complete=True,
    )


def test_c_search_reads_two_revisions_without_a_mirror_or_current_profile(tmp_path):
    path = tmp_path / "source.sqlite"
    db = Storage(path)
    try:
        first = verified_public(revision_id="rev-1", title="Les loups des bois")
        second = verified_public(revision_id="rev-2", title="Le Loup du Nord")
        other = verified_public(record_id="another-record", title="Les chiens")
        saved = [persist(db, row) for row in (first, second, other)]
        assert len({s.document for s in saved}) == 3
        # None of the source observations requires entries in the *separate*
        # legacy search or account-specific catalogue mirror.
        assert db.catalog_entries() == []
        page = search_public_notice_c(db, "loup", now=100)
        assert page.status == "partial"
        assert page.known_matches == 2
        assert len(page.hits) == 2
        assert {x.source_revision for x in page.hits} == {"rev-1", "rev-2"}
        assert {x.document for x in page.hits} == {
            saved[0].document, saved[1].document,
        }
        assert all(x.stale is False and x.cover_state == "available"
                   for x in page.hits)
        assert search_public_notice_c(
            db, "loup", field="publisher", now=100,
        ).hits == ()
        assert search_public_notice_c(
            db, "loup", provider_ids=("foreign",), now=100,
        ).hits == ()
        assert len(search_public_notice_c(
            db, "loup", page_size=1, page=1, now=100,
        ).hits) == 1
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()

    reopened = Storage(path)
    try:
        stale = search_public_notice_c(
            reopened, "loup", now=100 + 25 * 3600,
        )
        assert stale.status == "partial"
        assert all(x.stale is True for x in stale.hits)
        assert {x.document for x in stale.hits} == {
            saved[0].document, saved[1].document,
        }
        assert all(read_notice_cover(reopened, x.document)
                   for x in stale.hits)
    finally:
        reopened.close()


def test_only_same_call_admitted_safe_public_text_enters_search_index(tmp_path):
    db = Storage(tmp_path / "privacy.sqlite")
    try:
        no_marker = public_row(title="Un catalogue sans preuve d'enveloppe")
        legacy = persist(db, no_marker)
        assert legacy.observation.parser_version == "1"
        assert search_public_notice_c(db, "catalogue", now=100).hits == ()

        private = notice(key=PRIVATE)
        persist_notice_cover(
            db, private, field_path=("media", "cover"),
            cover_inventory_complete=True,
        )
        assert search_public_notice_c(db, "livre", now=100).hits == ()
        assert search_public_notice_c(db, "catalogue", now=100).scanned_sources == 1

        bad = verified_public(
            record_id="unsafe-source",
            title="https://private.example.invalid?token=secret",
        )
        rejected = persist(db, bad)
        assert rejected.observation.parser_version == "1"
        assert search_public_notice_c(db, "private", now=100).hits == ()

        private_account = verified_public(
            record_id="account-row", source_account_id="borrower",
        )
        assert admit_public_search_catalogue(
            private_account, identity_id="alice",
            observed_at=100, public_cover_url=URL,
        ) is None
        signed = verified_public(
            record_id="signed-image",
            thumbnail_url=URL + "?token=secret",
        )
        assert admit_public_search_catalogue(
            signed, identity_id="alice",
            observed_at=100, public_cover_url=signed.thumbnail_url,
        ) is None
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_budget_exceeded_never_converts_partial_index_to_attested_zero(tmp_path):
    db = Storage(tmp_path / "large.sqlite")
    try:
        for index in range(5):
            persist(db, verified_public(
                record_id=f"record-{index}", title=f"Catalogue {index}",
            ))
        result = search_public_notice_c(
            db, "nomatch", max_scan=2, now=100,
        )
        assert result.status == "budget_exceeded"
        assert result.known_matches == 0
        assert result.has_more is True
        assert result.scanned_sources == 2
    finally:
        db.close()


@pytest.mark.parametrize("invalid", [
    {"query": "x"}, {"query": "a" * 161},
    {"query": "book", "max_scan": 30001},
    {"query": "book", "page_size": 1000},
    {"query": "book", "provider_ids": ("bnf", "")},
    {"query": "book", "field": "rights"},
])
def test_search_c_rejects_invalid_queries(tmp_path, invalid):
    db = Storage(tmp_path / "bad.sqlite")
    try:
        with pytest.raises(ValueError, match="invalid_notice_c_search_request"):
            search_public_notice_c(db, **invalid)
    finally:
        db.close()


def test_legacy_replay_cannot_downgrade_same_call_c_bibliography(tmp_path):
    db = Storage(tmp_path / "downgrade.sqlite")
    try:
        live = verified_public(title="La forêt des loups")
        rich = persist(db, live, at=100)
        assert rich.observation.parser_version == "2"
        assert rich.epoch == 1

        # Cached SearchResult loses the nonserialized Core same-call stamp.
        cached = replace(live)
        plain = admit_public_search_cover(
            cached, identity_id="bob",
            observed_at=101, public_cover_url=cached.thumbnail_url,
        )
        assert plain is not None and plain.parser_version == "1"
        replay = persist_notice_cover(
            db, plain, field_path=("search_result", "thumbnail_url"),
            cover_inventory_complete=True,
        )
        assert replay == rich
        assert read_notice_cover(db, rich.document) == rich
        c = search_public_notice_c(db, "forêt", now=102)
        assert len(c.hits) == 1
        assert c.hits[0].document == rich.document
        assert c.hits[0].epoch == 1
        assert c.hits[0].title == live.title
    finally:
        db.close()


def test_publication_date_retained_after_source_eviction_and_reopen(tmp_path):
    from cifereca.core.notice_c_cover_index import (
        persist_exact_public_search_cover, read_consolidated_notice_cover, _db_key,
    )
    from cifereca.core.notice_cover_repository import evict_notice_cover
    from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
    import json

    path = tmp_path / "dates.sqlite"
    db = Storage(path)
    try:
        dated = persist(db, verified_public(record_id="dated", title="Les loups",
                                            published_at="2026-10-07"), at=100)
        undated = persist(db, verified_public(record_id="unknown-date", title="Les loups",
                                              published_at=None), at=100)
        c = persist_exact_public_search_cover(db, dated)
        old = persist_exact_public_search_cover(db, undated)
        assert c.sources[0].public_bibliography.published_at == "2026-10-07"
        assert old.sources[0].public_bibliography.published_at is None
        assert persist_exact_public_search_cover(db, undated) == old
        with db._lock, db._connect() as sql:
            raw = json.loads(sql.execute("SELECT value FROM metadata WHERE key=?",
                                          (_db_key(old.document),)).fetchone()[0])
            assert "published_at" not in next(iter(raw["sources"].values()))["public_bibliography"]
        assert evict_notice_cover(db, dated.document, expected_epoch=dated.epoch)
        assert evict_notice_cover(db, undated.document, expected_epoch=undated.epoch)
    finally:
        db.close()
    db = Storage(path)
    try:
        page = search_public_notice_c(db, "loups", now=101, group_by_document=True)
        assert {hit.record_id: hit.published_at for hit in page.hits} == {
            "dated": "2026-10-07", "unknown-date": None,
        }
        rows = merge_public_notice_c_known_rows([], page, identity_id="bob")
        assert {row["item_id"]: row["published_at"] for row in rows} == {
            "dated": "2026-10-07", "unknown-date": None,
        }
        assert db.entitlements() == [] and db.acquisitions() == []
        # Corrupt/unsafe descriptive values are refused by the same strict
        # canonical decoder, not reflected into the public Search DTO.
        for unsafe in ("https://private.example.org/?token=secret",
                       r"C:\private\book.epub", "  ~\\private\\book.epub"):
            with db._lock, db._connect() as sql:
                key = _db_key(c.document)
                raw = json.loads(sql.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()[0])
                next(iter(raw["sources"].values()))["public_bibliography"]["published_at"] = unsafe
                sql.execute("UPDATE metadata SET value=? WHERE key=?", (json.dumps(raw), key))
            with pytest.raises(ValueError, match="corrupt_canonical_cover"):
                read_consolidated_notice_cover(db, c.document)
    finally:
        db.close()


def test_location_in_scalar_never_promotes_cover_only_observation(tmp_path):
    db = Storage(tmp_path / "location.sqlite")
    try:
        row = verified_public(title="Safe title", description=r"C:\private\book.epub")
        observed = persist(db, row)
        assert observed.observation.parser_version == "1"
        assert search_public_notice_c(db, "Safe title", now=100).hits == ()
        assert "private" not in repr(observed.observation.fields_copy())
    finally:
        db.close()


def test_series_relation_survives_source_eviction_and_reopen(tmp_path):
    from types import SimpleNamespace
    from cifereca.core.search_orchestration import _public_rows_from_response
    from cifereca.core.notice_c_cover_index import (
        persist_exact_public_search_cover, read_consolidated_notice_cover, _db_key,
    )
    from cifereca.core.notice_cover_repository import evict_notice_cover
    from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
    from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
    import json

    proof = {
        "source": "bnf.catalogue.search", "source_field": "Search.RecordLink",
        "scope": "search", "confidence": "linked_search_relation",
        "freshness": "search_response",
    }
    metadata = {"series": ["Cycle A"], "bibliography_evidence": {"series": proof}}
    first = public_row(record_id="series-a", metadata=metadata)
    second = public_row(record_id="series-b", metadata={"series": ["Cycle B"]})
    session = SimpleNamespace(provider_id="bnf", credential_id="__public__",
                              credentials={}, _accounts=[])
    response = ProviderSearchResponse([first, second], public_notices=(
        PublicSearchNotice(first), PublicSearchNotice(second),
    ))
    rows = _public_rows_from_response(response, session)
    assert len(rows) == 2
    assert rows[0].metadata["bibliography_evidence"]["series"] == proof
    assert rows[0].metadata["bibliography_evidence"]["series"] is not proof
    path = tmp_path / "series.sqlite"
    db = Storage(path)
    try:
        for row in rows:
            source = persist(db, row)
            canonical = persist_exact_public_search_cover(db, source)
            if row.record_id == "series-a":
                document = canonical.document
                relation = canonical.sources[0].public_bibliography.series_relation
                assert relation.label == "Cycle A"
                assert relation.source_field == proof["source_field"]
            else:
                assert canonical.sources[0].public_bibliography.series_relation is None
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
    finally:
        db.close()
    db = Storage(path)
    try:
        page = search_public_notice_c(db, "titre", now=101, group_by_document=True)
        projected = merge_public_notice_c_known_rows([], page, identity_id="bob")
        by_id = {row["item_id"]: row["metadata"] for row in projected}
        assert by_id["series-a"]["series"] == ["Cycle A"]
        assert by_id["series-a"]["bibliography_evidence"] == {"series": proof}
        assert "series" not in by_id["series-b"]
        assert db.entitlements() == [] and db.acquisitions() == []
        with db._lock, db._connect() as sql:
            key = _db_key(document)
            raw = json.loads(sql.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()[0])
            next(iter(raw["sources"].values()))["public_bibliography"]["series_relation"]["source"] = "foreign.catalogue.search"
            sql.execute("UPDATE metadata SET value=? WHERE key=?", (json.dumps(raw), key))
        with pytest.raises(ValueError, match="corrupt_canonical_cover"):
            read_consolidated_notice_cover(db, document)
    finally:
        db.close()
