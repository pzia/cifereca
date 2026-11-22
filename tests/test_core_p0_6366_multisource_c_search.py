"""#6366: read a previously attested multi-provider C without losing text.

These tests exercise the real persisted source observations, canonical index,
and Search cursor. The strong edition relation is explicitly supplied by the
fixture to the existing Core writer: this does NOT implement its live producer.
No edition merge from title, ISBN, cover URL or physical N3 bytes.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.core.notice_c_cover_index import (
    persist_consolidated_cover_source, persist_exact_public_search_cover,
    read_consolidated_notice_cover,
)
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import _opaque_key
from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import (
    verified_public, persist,
)

URL_A = "https://covers.example.org/paris-public.jpg"
URL_B = "https://covers.example.org/numilog-public.jpg"


def sources(db):
    first = persist(db, verified_public(
        source_provider="bnf", record_id="bnf-source-1",
        title="Deux sources: BNF", author="Une autrice",
        description="Description spécifique BNF",
        thumbnail_url=URL_A,
    ), at=100)
    second = persist(db, verified_public(
        source_provider="numilog-catalog", record_id="numilog-source-9",
        title="Deux sources: Numilog", author="Une autrice",
        description="Description spécifique Numilog",
        thumbnail_url=URL_B,
    ), at=100)
    assert first.document != second.document
    return first, second


def shared(db, first, second):
    # This explicit fixture relation is NOT inferred by the Search reader.
    # A separate trusted Core producer must establish same-edition authority.
    target = first.observation.key.edition_id
    persist_consolidated_cover_source(
        db, canonical_edition_id=target, source=first,
        relation_proof_id="strong:fixture:bnf",
    )
    return persist_consolidated_cover_source(
        db, canonical_edition_id=target, source=second,
        relation_proof_id="strong:fixture:numilog",
    )


def purge(db, *sources):
    with db._lock, db._connect() as sql:
        for source in sources:
            sql.execute("DELETE FROM metadata WHERE key=?", (
                _opaque_key(source.document),
            ))


def test_multisource_public_c_keeps_separate_source_text_and_one_n2_ref(
    tmp_path,
):
    filename = tmp_path / "two-sources.sqlite"
    db = Storage(filename)
    first, second = sources(db)
    consolidated = shared(db, first, second)
    canonical = consolidated.document
    try:
        assert len(consolidated.sources) == 2
        assert {c.url for c in consolidated.candidates} == {URL_A, URL_B}
        online = search_public_notice_c(db, "deux sources", now=100)
        assert online.known_matches == 2
        assert len(online.hits) == 2
        assert {hit.document for hit in online.hits} == {canonical}
        assert {hit.provider_id for hit in online.hits} == {
            "bnf", "numilog-catalog",
        }
        assert {hit.description for hit in online.hits} == {
            "Description spécifique BNF", "Description spécifique Numilog",
        }
        assert search_public_notice_c(
            db, "numilog", provider_ids=("bnf",), now=100,
        ).hits == ()
        snapshot = cached_notice_cover_snapshot(NS(storage=db), canonical)
        assert snapshot is not None
        assert {c.url for c in snapshot.candidates} == {URL_A, URL_B}

        purge(db, first, second)
        offline = search_public_notice_c(db, "deux sources", now=100)
        assert offline.known_matches == 2
        assert {hit.document for hit in offline.hits} == {canonical}
        assert {hit.title for hit in offline.hits} == {
            "Deux sources: BNF", "Deux sources: Numilog",
        }
        assert {hit.epoch for hit in offline.hits} == {consolidated.epoch}
        assert search_public_notice_c(
            db, "numilog", provider_ids=("numilog-catalog",),
            now=100,
        ).hits[0].document == canonical
        known = merge_public_notice_c_known_rows(
            [], offline, identity_id="alice",
        )
        assert len(known) == 1  # One C, not two competing cover cards.
        assert known[0]["_cover_document_ref"] == canonical
        assert known[0]["metadata"]["notice_c_source_alternatives"] == [{
            "provider_id": "numilog-catalog",
            "record_id": "numilog-source-9",
            "source_revision": None,
            "title": "Deux sources: Numilog",
            "author": "Une autrice",
            "publisher": None,
            "description": "Description spécifique Numilog",
        }]
        assert all(not row.get("download_url") for row in known)
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()

    reopened = Storage(filename)
    try:
        old = search_public_notice_c(
            reopened, "deux sources", now=100 + 90000,
        )
        assert len(old.hits) == 2
        assert {hit.document for hit in old.hits} == {canonical}
        assert all(hit.stale for hit in old.hits)
        assert read_consolidated_notice_cover(
            reopened, canonical,
        ).candidates == consolidated.candidates
        assert reopened.entitlements() == []
    finally:
        reopened.close()


def test_multi_source_relation_outranks_singleton_but_conflict_fails_closed(
    tmp_path,
):
    db = Storage(tmp_path / "ambiguous.sqlite")
    try:
        a, b = sources(db)
        a_self = persist_exact_public_search_cover(db, a).document
        b_self = persist_exact_public_search_cover(db, b).document
        assert a_self != b_self
        group = shared(db, a, b)
        # B has both its original singleton and the admitted multi-source
        # relation. Use the unique stronger group; never take DB row order.
        purge(db, a, b)
        found = search_public_notice_c(db, "deux sources", now=100)
        assert len(found.hits) == 2
        assert {x.document for x in found.hits} == {group.document}

        # Reintroduce B's same attested source; a contradictory SECOND
        # same-strength group cannot quietly choose another canonical C.
        b = sources_in_same_db_with_fresh_second(db, b)
        c = persist(db, verified_public(
            source_provider="paris", record_id="paris-source-1",
            title="Deux sources: Paris", thumbnail_url=URL_A,
        ), at=100)
        third = persist_consolidated_cover_source(
            db, canonical_edition_id=c.observation.key.edition_id,
            source=c, relation_proof_id="strong:fixture:paris",
        )
        persist_consolidated_cover_source(
            db, canonical_edition_id=c.observation.key.edition_id,
            source=b, relation_proof_id="strong:fixture:numilog-second",
        )
        purge(db, b, c)
        uncertain = search_public_notice_c(db, "deux sources", now=100)
        assert {hit.provider_id for hit in uncertain.hits} == {
            "bnf", "numilog-catalog", "paris",
        }
        assert {
            hit.provider_id: hit.document for hit in uncertain.hits
        } == {
            "bnf": group.document,
            # Two incompatible cross-provider relations cannot override
            # Numilog's independent self-identity canonical document.
            "numilog-catalog": b_self,
            "paris": third.document,
        }
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def sources_in_same_db_with_fresh_second(db, old):
    # Reconstitute a persisted source with the exact same coordinates;
    # source must exist at the transactional index writer admission.
    return persist(db, verified_public(
        source_provider="numilog-catalog", record_id="numilog-source-9",
        title="Deux sources: Numilog", author="Une autrice",
        description="Description spécifique Numilog",
        thumbnail_url=URL_B,
    ), at=100)


def test_stale_canonical_epoch_never_rebinds_changed_live_source(tmp_path):
    db = Storage(tmp_path / "stale.sqlite")
    try:
        source = persist(db, verified_public(
            record_id="same-coordinates", title="Édition initiale",
        ), at=100)
        indexed = persist_exact_public_search_cover(db, source)
        new_source = persist(db, verified_public(
            record_id="same-coordinates", title="Édition modifiée",
        ), at=101)
        assert new_source.epoch > source.epoch
        fresh = search_public_notice_c(db, "modifiée", now=101)
        assert len(fresh.hits) == 1
        assert fresh.hits[0].document == new_source.document
        assert fresh.hits[0].document != indexed.document
        assert fresh.hits[0].epoch == new_source.epoch
        assert db.entitlements() == []
    finally:
        db.close()



def test_grouped_pages_preserve_matching_sources_and_do_not_repeat_a_c(tmp_path):
    filename = tmp_path / "grouped-pages.sqlite"
    db = Storage(filename)
    try:
        a, b = sources(db)
        group = shared(db, a, b)
        other = persist(db, verified_public(
            source_provider="seller-z", record_id="other",
            title="Deux sources: Z",
        ), at=100)
        own = persist_exact_public_search_cover(db, other)
        def page(storage, n, **kwargs):
            return search_public_notice_c(
                storage, "deux sources", now=100, page=n, page_size=1,
                group_by_document=True, **kwargs,
            )
        def assert_pages(storage):
            first, second, last = (page(storage, n) for n in range(3))
            assert first.known_matches == second.known_matches == 3
            assert first.known_documents == second.known_documents == 2
            assert first.has_more and not second.has_more
            assert len(first.hits) == len(second.hits) == 1
            assert first.hits[0].document == group.document
            assert second.hits[0].document == own.document
            assert len(first.matching_sources) == 2
            assert {hit.provider_id for hit in first.matching_sources} == {
                "bnf", "numilog-catalog",
            }
            assert not last.hits and not last.matching_sources and not last.has_more
            for viewer in ("alice", "bob", "carol"):
                rows = merge_public_notice_c_known_rows([], first, identity_id=viewer)
                assert len(rows) == 1
                assert rows[0]["identity_id"] == viewer
                alternative = rows[0]["metadata"]["notice_c_source_alternatives"]
                assert len(alternative) == 1
                assert alternative[0]["provider_id"] == "numilog-catalog"
                assert rows[0]["thumbnail_url"] is None
                assert not rows[0].get("download_url")
            filtered = page(storage, 0, provider_ids=("numilog-catalog",))
            assert filtered.known_matches == filtered.known_documents == 1
            assert len(filtered.matching_sources) == 1
            assert filtered.hits[0].document == group.document
            assert not filtered.has_more
            assert storage.entitlements() == [] and storage.acquisitions() == []
        assert_pages(db)
        purge(db, a, b, other)
        assert_pages(db)
    finally:
        db.close()

    restored = Storage(filename)
    try:
        assert_pages(restored)
    finally:
        restored.close()


def test_only_exact_unpossessed_public_mirror_coordinates_are_suppressed(tmp_path):
    from contextlib import closing
    with closing(Storage(tmp_path / "mirror.sqlite")) as db:
        first, second = sources(db)
        group = shared(db, first, second)
        page = search_public_notice_c(db, "deux sources", now=100,
                                     group_by_document=True)
        assert page.hits[0].document == group.document
        base = {
            "source": "known_catalog", "identity_id": "alice",
            "provider_id": "numilog-catalog", "item_id": "numilog-source-9",
            "local": False, "state": "remote_catalog",
            "metadata": {
                "catalogue_scope_version": 2, "source_credential": "__public__",
                "source_collection": "public_catalog", "docbase": "digital-books",
                "medium": "digital",
            },
        }
        retained = [
            {**base, "key": "path", "local_path": "/local/book.epub"},
            {**base, "key": "artifact", "artifact_id": "known-artifact"},
            {**base, "key": "uncertain", "state": "unknown"},
            {**base, "key": "no-local-proof", "local": None},
            {**base, "key": "other-edition", "metadata": {
                **base["metadata"], "source_revision": "rev-b"}},
            {**base, "key": "malformed-v3", "metadata": {
                **base["metadata"], "catalogue_scope_version": 3}},
            {**base, "key": "foreign", "metadata": {
                **base["metadata"], "source_account_id": "private"}},
        ]
        rows = merge_public_notice_c_known_rows(
            [{**base, "key": "exact"}, *retained], page, identity_id="alice",
        )
        assert rows[0]["_cover_document_ref"] == group.document
        assert rows[1:] == retained
        assert db.entitlements() == [] and db.acquisitions() == []


def test_source_pages_keep_exact_edition_order_across_eviction_and_restart(tmp_path):
    from contextlib import closing
    from cifereca.core.notice_cover_repository import evict_notice_cover

    filename = tmp_path / "source-page-ties.sqlite"
    with closing(Storage(filename)) as db:
        # All tie on score, title and provider. Three exact immutable
        # records with two separate revisions must remain six results.
        observations = [
            persist(db, verified_public(
                source_provider="bnf", record_id=f"tie-{index}",
                revision_id=revision, title="Même titre public",
            ))
            for index in range(3) for revision in ("rev-1", "rev-2")
        ]
        canonical = [
            persist_exact_public_search_cover(db, source)
            for source in observations
        ]
        expected = sorted(source.observation.key.edition_id for source in observations)
        assert len(set(expected)) == 6
        expected_refs = {
            source.observation.key.edition_id: index.document
            for source, index in zip(observations, canonical)
        }

        def check_pages(storage):
            for grouped in (False, True):
                seen = []
                for number in range(7):
                    result = search_public_notice_c(
                        storage, "même titre", page=number, page_size=1,
                        group_by_document=grouped, now=100,
                    )
                    assert result.known_matches == 6
                    assert result.status == "partial"
                    assert result.has_more == (number < 5)
                    if grouped:
                        assert result.known_documents == 6
                    if number == 6:
                        assert not result.hits
                        continue
                    hit, = result.hits
                    seen.append(hit.edition_id)
                    assert hit.document == expected_refs[hit.edition_id]
                assert seen == expected
            assert storage.entitlements() == [] and storage.acquisitions() == []

        check_pages(db)
        for source in observations[::2]:
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
        check_pages(db)  # live and durable representations mixed
        for source in observations[1::2]:
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
        check_pages(db)

    with closing(Storage(filename)) as reopened:
        check_pages(reopened)


def test_public_search_never_groups_sources_through_mixed_private_relation(tmp_path):
    from contextlib import closing
    from tests.test_core_p1_6168_canonical_multi_provider_covers import _store
    from cifereca.core.notice_cover_repository import evict_notice_cover
    filename = tmp_path / "mixed-authority.sqlite"
    with closing(Storage(filename)) as db:
        a, b = sources(db)
        own_a = persist_exact_public_search_cover(db, a)
        own_b = persist_exact_public_search_cover(db, b)
        private = _store(db, "private-fixture", "https://private.example.org/cover.jpg",
                         identity="private-owner")
        for member in (a, b, private):
            mixed = persist_consolidated_cover_source(
                db, canonical_edition_id="edition:mixed-relation",
                source=member, relation_proof_id="strong:fixture:mixed",
            )
        assert len(mixed.sources) == 3
        def check(storage):
            page = search_public_notice_c(storage, "deux sources",
                                          group_by_document=True, now=100)
            assert page.known_documents == 2
            assert {hit.document for hit in page.hits} == {own_a.document, own_b.document}
            assert all(hit.document != mixed.document for hit in page.hits)
        check(db)
        for source in (a, b, private):
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
        check(db)
    with closing(Storage(filename)) as db:
        check(db)
        assert db.entitlements() == [] and db.acquisitions() == []
