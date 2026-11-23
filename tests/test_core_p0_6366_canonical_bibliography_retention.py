"""#6366: canonical C keeps exact public bibliography after provider TTL purge.

The source record is an independently verified public Search observation; its
edition coordinates are never joined by title, ISBN, URL, N3 or current user.
"""
from __future__ import annotations

import json

import pytest

from cifereca.storage import Storage
from cifereca.core.notice_c_cover_index import (
    canonical_cover_document_ref, persist_consolidated_cover_source,
    read_consolidated_notice_cover, _db_key,
)
from cifereca.core.notice_cover_repository import (
    _opaque_key, read_notice_cover,
)
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from tests.test_core_p0_6366_public_c_sqlite_source_search import (
    persist, verified_public,
)


def _make_c(db, row):
    source = persist(db, row)
    canonical = persist_consolidated_cover_source(
        db, canonical_edition_id=source.observation.key.edition_id,
        source=source, relation_proof_id="core.exact.observed.v1:proof",
    )
    assert canonical.sources[0].public_bibliography is not None
    assert canonical.sources[0].public_bibliography.title == row.title
    assert canonical.sources[0].source_ref == source.document.source_key
    return source, canonical


def _drop_short_source(db, source):
    with db._lock, db._connect() as conn:
        conn.execute("DELETE FROM metadata WHERE key=?", (
            _opaque_key(source.document),
        ))


def test_public_title_description_cover_survive_source_expiry_reboot_offline(
    tmp_path,
):
    path = tmp_path / "c.sqlite"
    db = Storage(path)
    row = verified_public(
        record_id="exact-record", title="Le Loup rouge", author="Une auteure",
        description="Un roman de forêt",
    )
    source, c = _make_c(db, row)
    try:
        assert search_public_notice_c(db, "Loup", now=100).hits[0].document == c.document
        _drop_short_source(db, source)
        assert read_notice_cover(db, source.document) is None
        remaining = read_consolidated_notice_cover(db, c.document)
        assert remaining is not None and remaining.candidates
        assert remaining.sources[0].public_bibliography.title == row.title
        page = search_public_notice_c(db, "loup", now=100)
        assert page.status == "partial"
        assert len(page.hits) == 1
        hit = page.hits[0]
        assert hit.document == c.document
        assert (hit.title, hit.author, hit.description) == (
            row.title, row.author, row.description,
        )
        # A manually dropped short-lived source cannot certify current freshness.
        # Its separately persisted canonical cover remains available.
        assert hit.cover_state == "available" and hit.stale
        known = merge_public_notice_c_known_rows(
            [], page, identity_id="bob",
        )
        assert known[0]["_cover_document_ref"] == c.document
        assert known[0]["identity_id"] == "bob"
        assert known[0]["thumbnail_url"] is None
        assert not known[0].get("download_url")
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
    reopened = Storage(path)
    try:
        stale = search_public_notice_c(reopened, "roman", now=100 + 90000)
        assert len(stale.hits) == 1
        assert stale.hits[0].document == c.document
        assert stale.hits[0].stale is True
        assert read_consolidated_notice_cover(
            reopened, c.document,
        ).candidates[0].url == row.thumbnail_url
    finally:
        reopened.close()


def test_same_title_and_cover_two_editions_never_merge_after_purge(tmp_path):
    db = Storage(tmp_path / "editions.sqlite")
    try:
        first, ca = _make_c(db, verified_public(
            record_id="A", title="La forêt", author="Autrice",
        ))
        second, cb = _make_c(db, verified_public(
            record_id="B", title="La forêt", author="Autrice",
        ))
        assert ca.document != cb.document
        _drop_short_source(db, first)
        _drop_short_source(db, second)
        page = search_public_notice_c(db, "forêt", now=100)
        assert len(page.hits) == 2
        assert {r.document for r in page.hits} == {ca.document, cb.document}
        assert {r.record_id for r in page.hits} == {"A", "B"}
        assert db.entitlements() == []
    finally:
        db.close()


def test_tampered_canonical_bibliography_never_rebinds_other_edition(tmp_path):
    db = Storage(tmp_path / "tamper.sqlite")
    try:
        source, c = _make_c(db, verified_public(
            record_id="safe", title="Ceci est public",
        ))
        _drop_short_source(db, source)
        with db._lock, db._connect() as conn:
            key = _db_key(c.document)
            value = json.loads(conn.execute(
                "SELECT value FROM metadata WHERE key=?", (key,),
            ).fetchone()[0])
            bib = next(iter(value["sources"].values()))["public_bibliography"]
            bib["record_id"] = "foreign"
            conn.execute(
                "UPDATE metadata SET value=? WHERE key=?",
                (json.dumps(value), key),
            )
        with pytest.raises(ValueError, match="corrupt_canonical_cover"):
            read_consolidated_notice_cover(db, c.document)
        assert search_public_notice_c(db, "public", now=100).hits == ()
        assert db.entitlements() == []
    finally:
        db.close()


def test_cover_only_canonical_source_never_invents_bibliography(tmp_path):
    db = Storage(tmp_path / "cover-only.sqlite")
    try:
        from tests.test_core_p0_6366_real_search_notice_c_ingestion import (
            public_row, mirror,
        )
        row = public_row(record_id="cover-only", title="Notice sans bibliographie")
        assert mirror(db, [row]) == 1
        source_ref = row._core_notice_cover_document_ref
        source = read_notice_cover(db, source_ref)
        assert source is not None and source.observation.parser_version == "1"
        c = persist_consolidated_cover_source(
            db, canonical_edition_id=source.observation.key.edition_id,
            source=source, relation_proof_id="core.cover.only.v1:test",
        )
        assert c.sources[0].public_bibliography is None
        _drop_short_source(db, source)
        assert search_public_notice_c(db, "Notice sans", now=100).hits == ()
        assert read_consolidated_notice_cover(db, c.document).candidates
    finally:
        db.close()


def test_durable_search_is_not_starved_by_transient_source_budget(tmp_path):
    db = Storage(tmp_path / "independent-c-search.sqlite")
    try:
        source, expected = _make_c(db, verified_public(
            record_id="durable-target", title="Éléphant rare", author="Autrice",
        ))
        _drop_short_source(db, source)
        for n in range(12):
            persist(db, verified_public(
                record_id=f"transient-{n}", title=f"Un autre livre {n}",
            ))
            old, _ = _make_c(db, verified_public(
                record_id=f"durable-{n}", title=f"Document indépendant {n}",
            ))
            _drop_short_source(db, old)
        with db._lock, db._connect() as sql:
            sql.execute("INSERT INTO metadata(key,value) VALUES(?,?)",
                        ("notice.c.cover.canonical.v1:corrupt", "{"))
        page = search_public_notice_c(db, "elephant", field="title",
                                     now=100, max_scan=2)
        assert page.status == "budget_exceeded" and page.has_more
        assert page.scanned_sources == 2
        assert page.known_matches == 1
        assert page.hits[0].document == expected.document
        assert page.hits[0].title == "Éléphant rare"
        assert search_public_notice_c(db, "elephant", field="author",
                                     now=100, max_scan=2).hits == ()
        assert search_public_notice_c(db, "elephant", provider_ids=("foreign",),
                                     now=100, max_scan=2).hits == ()
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_unscanned_current_source_vetoes_obsolete_c_text(tmp_path):
    db = Storage(tmp_path / "unscanned-current.sqlite")
    try:
        rows = [verified_public(record_id=f"candidate-{n}",
                                title="Ancien titre unique") for n in range(8)]
        sources = [persist(db, row) for row in rows]
        ordered = sorted(zip(sources, rows), key=lambda pair: _opaque_key(pair[0].document))
        source, row = ordered[-1]
        from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
        expected = persist_exact_public_search_cover(db, source)
        current = persist(db, verified_public(record_id=row.record_id,
                                             title="Titre corrigé"), at=101)
        assert current.epoch > source.epoch
        # None of the early transient rows match this title; the retained
        # old canonical record must not be revived by the independent C scan.
        for old, _ in ordered[:-1]:
            assert old.document != current.document
        result = search_public_notice_c(db, "unique", max_scan=1, now=101)
        assert result.status == "budget_exceeded"
        assert all(hit.document != expected.document and hit.record_id != row.record_id
                   for hit in result.hits)
        assert db.entitlements() == []
    finally:
        db.close()


def test_canonical_text_prefilter_cannot_hide_competing_group(tmp_path):
    from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
    db = Storage(tmp_path / "prefilter-conflict.sqlite")
    try:
        original = persist(db, verified_public(
            source_provider="seller-a", record_id="shared-record",
            title="Ancien nom",
        ), at=100)
        b = persist(db, verified_public(
            source_provider="seller-b", record_id="related-b",
            title="Autre nom B",
        ), at=100)
        first_group = "edition:prefilter-old-group"
        for member in (original, b):
            persist_consolidated_cover_source(
                db, canonical_edition_id=first_group, source=member,
                relation_proof_id="strong:fixture:old-group",
            )
        current = persist(db, verified_public(
            source_provider="seller-a", record_id="shared-record",
            title="Éléphant rare",
        ), at=101)
        own = persist_exact_public_search_cover(db, current)
        c = persist(db, verified_public(
            source_provider="seller-c", record_id="related-c",
            title="Autre nom C",
        ), at=100)
        for member in (current, c):
            persist_consolidated_cover_source(
                db, canonical_edition_id="edition:prefilter-new-group",
                source=member, relation_proof_id="strong:fixture:new-group",
            )
        for member in (current, b, c):
            _drop_short_source(db, member)
        # The old group does not match the text anywhere. Nevertheless its
        # conflicting strong claim must be considered: exact self C wins.
        result = search_public_notice_c(db, "elephant", now=101, max_scan=1)
        assert len(result.hits) == 1
        assert result.hits[0].document == own.document
        assert result.hits[0].title == "Éléphant rare"
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
