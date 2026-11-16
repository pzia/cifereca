"""#6366: async known Search reads public C first, keeps rights independent."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

from cifereca.core import search_orchestration
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from cifereca.core.known_catalog_provenance import known_catalog_search_result
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import (
    persist, verified_public,
)
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row


def _known(db, query, *, identity="alice", field="all", scope="all",
           provider_ids=None, content_types=None, local=()):
    facade = NS(
        storage=db, catalog_entries=db.catalog_entries,
        library_entries=lambda **kw: list(local),
    )
    log = NS(info=lambda *_args: None)
    return search_orchestration.search_known_catalog(
        facade, query, scope=scope, field=field, identity_id=identity,
        provider_ids=provider_ids, content_types=content_types, logger=log,
    )


def test_public_c_precedes_provider_mirror_and_survives_viewer_change(tmp_path):
    db = Storage(tmp_path / "c-first.sqlite")
    try:
        first = verified_public(
            revision_id="edition-A", title="Les loups anciens",
        )
        second = verified_public(
            revision_id="edition-B", title="Les loups modernes",
        )
        c1, c2 = [persist(db, item) for item in (first, second)]
        # C can be searched before a legacy catalogue/mirror exists at all.
        alice = _known(db, "loups")
        assert len(alice) == 2
        assert [r["source"] for r in alice] == [
            "notice_c_public", "notice_c_public",
        ]
        assert {r["_cover_document_ref"] for r in alice} == {
            c1.document, c2.document,
        }
        assert all(r["thumbnail_url"] is None and r["local"] is False
                   and r["state"] == "remote_catalog" for r in alice)
        assert all(r["metadata"]["notice_c_query_status"] == "partial"
                   for r in alice)
        assert all(not r.get("download_url") and not r.get("placement_id")
                   for r in alice)
        assert all(known_catalog_search_result(row, identity_id="alice") is None
                   for row in alice)
        bob = _known(db, "loups", identity="bob")
        assert {r["_cover_document_ref"] for r in bob} == {
            c1.document, c2.document,
        }
        assert {r["identity_id"] for r in bob} == {"bob"}
        assert _known(db, "inexistant") == []
        assert _known(db, "loups", provider_ids=("unrelated",)) == []
        assert len(_known(db, "loups", field="title")) == 2
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_c_replaces_only_same_public_mirror_but_not_local_or_foreign_edition(tmp_path):
    db = Storage(tmp_path / "mixed.sqlite")
    try:
        public = verified_public(
            record_id="v1", revision_id="source-a", title="La forêt",
        )
        admitted = persist(db, public)
        # Actual legacy mirror of the *same* anonymous public source.
        assert search_orchestration._mirror_remote_results(
            NS(storage=db), [public], identity_id="alice",
        ) == 1
        known = _known(db, "forêt")
        assert len(known) == 1
        assert known[0]["source"] == "notice_c_public"
        assert known[0]["_cover_document_ref"] == admitted.document

        # Revisions and local possession MUST NOT be deleted as duplicates.
        variant = public_row(
            record_id="v1", revision_id="source-b", title="La forêt",
        )
        assert search_orchestration._mirror_remote_results(
            NS(storage=db), [variant], identity_id="alice",
        ) == 1
        known = _known(db, "forêt")
        assert len(known) == 2
        assert {r["source"] for r in known} == {
            "notice_c_public", "known_catalog",
        }
        owned = {
            "key": "artifact:local", "title": "La forêt",
            "provider_id": "bnf", "item_id": "v1",
            "identity_id": "alice", "local": True,
            "state": "local", "placement_id": "owned-1",
            "metadata": {},
        }
        retained = _known(db, "forêt", local=(owned,))
        assert any(r.get("placement_id") == "owned-1" for r in retained)
        assert any(r.get("source") == "notice_c_public" for r in retained)
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_partial_budget_and_stale_status_cannot_imply_a_complete_zero(tmp_path):
    db = Storage(tmp_path / "budget.sqlite")
    try:
        for idx in range(4):
            persist(db, verified_public(
                record_id=f"id-{idx}", title=f"Contes de la forêt {idx}",
            ))
        page = search_public_notice_c(db, "foret", now=100, max_scan=1)
        assert page.status == "budget_exceeded"
        assert page.has_more is True
        assert page.known_matches <= 1
        projected = merge_public_notice_c_known_rows(
            [], page, identity_id="alice",
        )
        assert len(projected) <= 1
        if projected:
            assert projected[0]["metadata"]["notice_c_query_status"] == "budget_exceeded"
        # Different identities reuse C but no rights from its presence.
        fresh = _known(db, "forêt")
        assert len(fresh) == 4
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
