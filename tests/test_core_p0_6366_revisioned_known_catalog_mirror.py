"""#6366/#2385: versioned supplier editions remain independent in known SQLite.

The legacy mirrored catalogue is NOT Notice C authority. In the interim,
a read-only v3 mirror must not overwrite one revision with another or turn
an offline known row into a new holding/borrow/download capability.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

from cifereca.core.known_catalog_provenance import known_catalog_search_result
from cifereca.core.notice_cover_repository import read_notice_cover
from cifereca.core.search import KnownCatalogSearch
from cifereca.core.search_orchestration import (
    _mirror_remote_results, _mirrored_catalogue_key,
    mirrored_catalogue_revisioned_id,
)
from cifereca.storage import Storage
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row
from tests.test_search_results_view import _host


def _search(db, identity):
    return KnownCatalogSearch(
        db.catalog_entries, lambda **_kwargs: [],
    ).search("Même titre", identity_id=identity)


def test_two_revisions_survive_sqlite_restart_and_remain_read_only(tmp_path):
    path = tmp_path / "catalogue.sqlite"
    v1 = public_row(revision_id="supplier-1")
    v2 = replace(v1, revision_id="supplier-2")
    db = Storage(path)
    try:
        assert _mirror_remote_results(
            NS(storage=db), [v1, v2], identity_id="alice",
        ) == 2
        keys = [_mirrored_catalogue_key("alice", row)[0] for row in (v1, v2)]
        assert len(set(keys)) == 2
        assert all(mirrored_catalogue_revisioned_id("alice", row) in keys for row in (v1, v2))
        stored = db.catalog_entries(identity_id="alice")
        assert len(stored) == 2
        assert {row["catalog_entry_id"] for row in stored} == set(keys)
        assert {row["metadata"]["source_revision"] for row in stored} == {
            "supplier-1", "supplier-2",
        }
        assert {row["metadata"]["catalogue_scope_version"] for row in stored} == {3}
        known = _search(db, "alice")
        assert len(known) == 2
        assert len({row["key"] for row in known}) == 2
        assert all(known_catalog_search_result(row, identity_id="alice") is None for row in known)
        assert _search(db, "bob") == []
        refs = [v1._core_notice_cover_document_ref, v2._core_notice_cover_document_ref]
        assert len(set(refs)) == 2
        assert all(read_notice_cover(db, ref) is not None for ref in refs)
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()

    db = Storage(path)
    try:
        known = _search(db, "alice")
        assert len(known) == 2
        assert len({row["key"] for row in known}) == 2
        assert _search(db, "bob") == []
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_v3_account_and_manifestation_scope_cannot_transfer_a_known_edition(tmp_path):
    db = Storage(tmp_path / "scopes.sqlite")
    a = public_row(revision_id="supplier-1")
    b = replace(a, source_account_id="other-source-account")
    c = replace(a, medium="physical")
    try:
        assert _mirror_remote_results(
            NS(storage=db), [a, b, c], identity_id="alice",
        ) == 3
        known = _search(db, "alice")
        assert len(known) == 3
        assert len({row["key"] for row in known}) == 3
        assert len({row["metadata"]["source_revision"] for row in known}) == 1
        assert all(known_catalog_search_result(row, identity_id="alice") is None for row in known)

        host = _host()
        exact_known = host._exact_known_catalogue_scope.__func__
        exact_remote = host._exact_remote_catalogue_scope.__func__
        remote = (a, b, c)
        known_scopes = {exact_known(row) for row in known}
        remote_scopes = {exact_remote(row) for row in remote}
        assert None not in known_scopes
        assert len(known_scopes) == len(remote_scopes) == 3
        assert known_scopes == remote_scopes

        # Each revision/account/manifestation is suppressed only by its
        # own exact live result, never a same-title/same-image neighbour.
        for row in remote:
            clone = replace(row, revision_id="supplier-2")
            assert exact_remote(clone) not in known_scopes
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_invalid_revision_cannot_overwrite_attested_v2_mirror(tmp_path):
    db = Storage(tmp_path / "unsafe.sqlite")
    safe = public_row()
    bad = replace(safe, revision_id="token=opaque-secret")
    try:
        v2, qualified = _mirrored_catalogue_key("alice", safe)
        rejected, accepted = _mirrored_catalogue_key("alice", bad)
        assert qualified is True and accepted is False and v2 != rejected
        assert mirrored_catalogue_revisioned_id("alice", bad) is None
        assert _mirror_remote_results(
            NS(storage=db), [safe, bad], identity_id="alice",
        ) == 2
        saved = {row["catalog_entry_id"]: row for row in db.catalog_entries(identity_id="alice")}
        assert len(saved) == 2
        assert saved[v2]["metadata"]["catalogue_scope_version"] == 2
        assert saved[rejected]["metadata"]["catalogue_scope_version"] is None
        assert "token=opaque-secret" not in str(saved[rejected])
        assert len(_search(db, "alice")) == 2
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
