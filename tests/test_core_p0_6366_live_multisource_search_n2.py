"""#6366: a live supplier Search row adopts exactly its attested multi-source C.

A strong relation is installed using the existing transactional Core writer;
no title/ISBN/image equality is used to infer that edition relation.
"""
from __future__ import annotations

from dataclasses import replace
from threading import Event
from types import SimpleNamespace as NS

from cifereca.core import search_orchestration
from cifereca.core.notice_c_cover_index import (
    canonical_cover_document_ref, persist_consolidated_cover_source,
    read_consolidated_notice_cover,
)
from cifereca.core.notice_c_exact_source_lookup import exact_public_source_c
from cifereca.core.notice_c_known_projection import (
    matching_live_public_canonical_ref, merge_public_notice_c_known_rows,
)
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import read_notice_cover
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.core.notice_cover_repository import is_notice_cover_document
from cifereca.gui.search_document_cover_bridge import (
    exact_remote_search_document, register_exact_search_document,
)
from cifereca.storage import Storage

from tests.test_core_p0_6366_same_call_public_cover_writer import (
    mirror, witnessed,
)
from tests.test_core_p0_6366_real_search_notice_c_ingestion import (
    public_row, OTHER_URL,
)
from tests.test_core_p1_6168_search_remote_document_gateway import host


def _live(provider: str, record: str, *, image: str = OTHER_URL):
    return witnessed(public_row(
        source_provider=provider, record_id=record, thumbnail_url=image,
    ))


def _source(db, row):
    return read_notice_cover(db, row._core_notice_cover_document_ref)


def _strong_join(db, a, b):
    original_a, original_b = _source(db, a), _source(db, b)
    target = original_a.observation.key.edition_id
    persist_consolidated_cover_source(
        db, canonical_edition_id=target, source=original_a,
        relation_proof_id="strong:attested:first",
    )
    return persist_consolidated_cover_source(
        db, canonical_edition_id=target, source=original_b,
        relation_proof_id="strong:attested:second",
    )


def test_producer_live_both_suppliers_and_c_first_share_one_n2_ref(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(search_orchestration.time, "time", lambda: 100)
    db = Storage(tmp_path / "shared.sqlite")
    try:
        a = _live("bnf", "edition-a")
        b = _live("numilog-catalog", "edition-b")
        assert mirror(db, [a, b]) == 2
        self_b = b._core_notice_canonical_cover_document_ref
        shared = _strong_join(db, a, b)
        assert shared.document != self_b
        assert len(shared.sources) == 2

        # The next *real* public Search producer read reuses the attested
        # multi-source C. The GUI never does this SQLite resolution.
        assert mirror(db, [b]) == 1
        assert b._core_notice_canonical_cover_document_ref == shared.document
        assert b._core_notice_canonical_cover_document_epoch == shared.epoch
        assert matching_live_public_canonical_ref(
            b, identity_id="alice",
        ) == shared.document
        window, _, key = host(b)
        live_n2 = exact_remote_search_document(window, b, key)
        assert live_n2 is not None and live_n2.document == shared.document
        assert live_n2.candidates[0].url == OTHER_URL

        known = search_public_notice_c(db, "Même titre", now=100)
        assert len(known.hits) == 2
        assert {hit.document for hit in known.hits} == {shared.document}
        rows = merge_public_notice_c_known_rows(
            [], known, identity_id="alice",
        )
        assert len(rows) == 1
        assert rows[0]["_cover_document_ref"] == live_n2.document
        snapshot = cached_notice_cover_snapshot(NS(storage=db), live_n2.document)
        assert snapshot is not None
        assert len(snapshot.candidates) == 2
        assert {x.url for x in snapshot.candidates} == {
            a.thumbnail_url, b.thumbnail_url,
        }
        # The common N2/N3 read-through is identical for the live list and
        # detail. No second source-specific Search cover is registered.
        loader = CoverLoader(max_workers=2)
        calls, reads = [], []
        monkeypatch.setattr(
            loader, "load",
            lambda url, callback, **kw: (
                calls.append(url) or NS(cancel=lambda: None)
            ),
        )
        monkeypatch.setattr(
            loader, "load_preview",
            lambda url, callback, **kw: (
                calls.append(url) or NS(cancel=lambda: None)
            ),
        )
        def reader(document):
            reads.append(document)
            return cached_notice_cover_snapshot(NS(storage=db), document)
        covers = DocumentCoverService(
            loader, cached_source_reader=reader,
            cached_source_scope=is_notice_cover_document,
        )
        try:
            assert register_exact_search_document(
                window, b, covers, live_n2, shared.document,
            )
            ready = Event()
            initial = covers.get_cover(
                shared.document, "list", lambda _: None,
                on_source_changed=ready.set,
            )
            assert ready.wait(10) and not initial.still_current
            listed = covers.get_cover(
                shared.document, "list", lambda _: None,
            )
            detailed = covers.get_cover(
                shared.document, "detail", lambda _: None,
            )
            assert listed.source_uri == detailed.source_uri == b.thumbnail_url
            assert reads == [shared.document]
            assert calls and set(calls) == {b.thumbnail_url}
        finally:
            covers.close()
            loader.close()
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_forged_route_replay_and_mutated_bibliography_cannot_bind_foreign_c(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(search_orchestration.time, "time", lambda: 100)
    db = Storage(tmp_path / "forged.sqlite")
    try:
        a, b = _live("bnf", "source-a"), _live("numilog-catalog", "source-b")
        assert mirror(db, [a, b]) == 2
        shared = _strong_join(db, a, b)
        assert mirror(db, [b]) == 1
        assert matching_live_public_canonical_ref(
            b, identity_id="bob",
        ) == shared.document

        clone = replace(b)
        assert matching_live_public_canonical_ref(
            clone, identity_id="bob",
        ) is None

        self_b = canonical_cover_document_ref(
            _source(db, b).observation.key.edition_id,
        )
        b._core_notice_canonical_cover_document_ref = (
            canonical_cover_document_ref("edition:unrelated")
        )
        assert matching_live_public_canonical_ref(
            b, identity_id="alice",
        ) is None
        window, _, key = host(b)
        fallback = exact_remote_search_document(window, b, key)
        assert fallback is not None
        assert fallback.document == b._core_notice_cover_document_ref
        assert fallback.document != shared.document

        b._core_notice_canonical_cover_document_ref = shared.document
        b.title = "Édition source modifiée"
        assert matching_live_public_canonical_ref(
            b, identity_id="alice",
        ) is None
        assert self_b != shared.document
    finally:
        db.close()


def test_two_conflicting_multisource_groups_keep_independent_self_c(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(search_orchestration.time, "time", lambda: 100)
    db = Storage(tmp_path / "conflicts.sqlite")
    try:
        b = _live("numilog-catalog", "source-b")
        a = _live("bnf", "source-a")
        c = _live("paris", "source-c")
        assert mirror(db, [b, a, c]) == 3
        self_b = b._core_notice_canonical_cover_document_ref
        joined_a = _strong_join(db, a, b)
        joined_c = _strong_join(db, c, b)
        assert joined_a.document != joined_c.document != self_b
        assert exact_public_source_c(
            db, provider_id="numilog-catalog", record_id="source-b",
            docbase=b.docbase, medium=b.medium,
            source_revision=b.revision_id,
        ).document == self_b
        assert mirror(db, [b]) == 1
        assert b._core_notice_canonical_cover_document_ref == self_b
        assert matching_live_public_canonical_ref(
            b, identity_id="alice",
        ) == self_b
        assert len(read_consolidated_notice_cover(
            db, joined_a.document,
        ).sources) == 2
    finally:
        db.close()
