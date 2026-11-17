"""#6366: real Core search producer -> durable Notice C -> N2 read-through.

These tests run the actual catalogue mirror and SQLite store, not a fabricated
Notice C fixture; no supplier GET, GTK widget or signed transport is needed.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from cifereca.core import search_orchestration
from cifereca.core.notice_cover_projection import notice_cover_document_ref
from cifereca.core.notice_cover_repository import read_notice_cover
from cifereca.core.notice_search_cover_producer import admit_public_search_cover
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.models import SearchResult
from cifereca.storage import Storage


URL = "https://covers.example.org/exact.jpg"
OTHER_URL = "https://covers.example.org/updated.jpg"


def public_row(**changed):
    row = SearchResult(
        id="public-row", record_id="source-record-1", docbase="digital-books",
        medium="digital", title="Même titre", author="Même auteur",
        type="book", format="EPUB", publisher=None, published_at=None,
        description=None, thumbnail_url=URL, friendly_url=None,
        scenario="search", source_provider="bnf", source_credential="__public__",
        source_collection="public_catalog", source_account_id=None,
        owned=False, downloadable=False, download_url=None, metadata={},
    )
    return replace(row, **changed)


def reference(row, identity="alice", url=URL, observed_at=100):
    observation = admit_public_search_cover(
        row, identity_id=identity, observed_at=observed_at,
        public_cover_url=url,
    )
    assert observation is not None
    return notice_cover_document_ref(observation.key)


def mirror(db, rows, identity="alice"):
    return search_orchestration._mirror_remote_results(
        SimpleNamespace(storage=db), rows, identity_id=identity,
    )


def source_count(db):
    with db._lock, db._connect() as conn:
        return conn.execute(
            "SELECT count(*) FROM metadata WHERE key LIKE 'notice.c.observation.v1:%'"
        ).fetchone()[0]


def test_real_public_search_pipeline_persists_and_reads_same_n2_ref_after_restart(
    tmp_path, monkeypatch,
):
    clock = [200]
    monkeypatch.setattr(search_orchestration.time, "time", lambda: clock[0])
    path = tmp_path / "reader.sqlite"
    row = public_row()
    ref = reference(row)
    assert ref.identity_id is None and ref.kind == "public_catalogue"
    first = Storage(path)
    try:
        assert mirror(first, [row], identity="alice") == 1
        observed = read_notice_cover(first, ref)
        assert observed is not None
        assert observed.epoch == 1
        assert observed.attribute.state == "available"
        assert observed.attribute.candidates[0].url == URL
        assert observed.observation.fields_copy() == {
            "search_result": {"thumbnail_url": URL},
        }
        assert source_count(first) == 1
        assert cached_notice_cover_snapshot(
            SimpleNamespace(storage=first), ref,
        ).scope == ref
        # Repeating a public source from B does not create a per-viewer copy.
        clock[0] = 201
        assert mirror(first, [row], identity="bob") == 1
        assert source_count(first) == 1
        assert read_notice_cover(first, ref).epoch == 1
    finally:
        first.close()
    second = Storage(path)
    try:
        assert read_notice_cover(second, ref).attribute.candidates[0].url == URL
        snapshot = cached_notice_cover_snapshot(
            SimpleNamespace(storage=second), ref,
        )
        assert snapshot is not None and snapshot.revision == 1
        assert snapshot.candidates[0].url == URL
    finally:
        second.close()


def test_two_homonymous_editions_share_bytes_not_notice_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(search_orchestration.time, "time", lambda: 200)
    first = public_row(record_id="source-record-1")
    second = public_row(record_id="source-record-2")
    db = Storage(tmp_path / "reader.sqlite")
    try:
        assert mirror(db, [first, second]) == 2
        a, b = reference(first), reference(second)
        assert a != b and source_count(db) == 2
        assert read_notice_cover(db, a).attribute.candidates[0].url == URL
        assert read_notice_cover(db, b).attribute.candidates[0].url == URL
        assert read_notice_cover(db, a).epoch == 1
        assert read_notice_cover(db, b).epoch == 1
    finally:
        db.close()


@pytest.mark.parametrize("changed", [
    {"source_credential": "account-secret"},
    {"source_collection": "personal"},
    {"source_account_id": "account-1"},
    {"owned": True},
    {"downloadable": True},
    {"download_url": "https://delivery.example.org/get"},
    {"metadata": {"search_provenance": "known_catalog_stale"}},
    {"thumbnail_url": "https://covers.example.org/exact.jpg?token=abc"},
    {"thumbnail_url": "http://covers.example.org/exact.jpg"},
    {"thumbnail_url": None},
])
def test_unattested_or_non_public_rows_never_enter_shared_notice_c(
    tmp_path, monkeypatch, changed,
):
    monkeypatch.setattr(search_orchestration.time, "time", lambda: 200)
    db = Storage(tmp_path / "reader.sqlite")
    try:
        row = public_row(**changed)
        assert mirror(db, [row]) == 1
        assert source_count(db) == 0
    finally:
        db.close()


def test_real_search_revision_changes_only_its_exact_notice(tmp_path, monkeypatch):
    clock = [200]
    monkeypatch.setattr(search_orchestration.time, "time", lambda: clock[0])
    first = public_row(record_id="first")
    second = public_row(record_id="second")
    a, b = reference(first), reference(second)
    db = Storage(tmp_path / "reader.sqlite")
    try:
        assert mirror(db, [first, second]) == 2
        clock[0] = 201
        assert mirror(db, [replace(first, thumbnail_url=OTHER_URL)]) == 1
        assert read_notice_cover(db, a).epoch == 2
        assert read_notice_cover(db, a).attribute.candidates[0].url == OTHER_URL
        assert read_notice_cover(db, b).epoch == 1
        assert read_notice_cover(db, b).attribute.candidates[0].url == URL
        assert source_count(db) == 2
    finally:
        db.close()
