"""#6366/#2385: bounded Core Notice C batch writes preserve exact CAS semantics.

No extra image/book cache table, account-dependent media scope or GTK writer.
Test 30k-row scalability at a bounded representative size and row isolation.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

from cifereca.core import search_orchestration
from cifereca.core.notice_cover_repository import (
    persist_notice_cover_many, read_notice_cover, persist_notice_cover,
)
from cifereca.core.notice_cover_projection import notice_cover_document_ref
from cifereca.storage import Storage
from tests.test_core_p1_2385_notice_cover_persistence import (
    PUBLIC, PRIVATE, URL, OTHER, notice,
)
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row


FIELDS = ("media", "cover")


def batch(db, observations):
    return persist_notice_cover_many(
        db, observations, field_path=FIELDS,
        cover_inventory_complete=True,
    )


def count_sources(db):
    with db._lock, db._connect() as connection:
        return connection.execute(
            "SELECT count(*) FROM metadata WHERE key LIKE 'notice.c.observation.v1:%'"
        ).fetchone()[0]


def test_301_exact_editions_use_only_three_bounded_sqlite_write_transactions(
    tmp_path, monkeypatch,
):
    db = Storage(tmp_path / "notices.sqlite")
    original = db._connect
    count = []

    def counted_connection():
        count.append(True)
        return original()

    observations = [
        notice(key=replace(PUBLIC, edition_id=f"edition:{i:04d}"),
               observed_at=100)
        for i in range(301)
    ]
    try:
        monkeypatch.setattr(db, "_connect", counted_connection)
        results = batch(db, observations)
        assert len(results) == 301 and all(result is not None for result in results)
        assert len(count) == 3  # Ceil(301 / 128), not 301 DB opens.
        assert len({result.document for result in results}) == 301
        assert all(result.epoch == 1 for result in results)
        assert count_sources(db) == 301
        for index in (0, 127, 128, 255, 256, 300):
            result = results[index]
            assert read_notice_cover(db, result.document).attribute.candidates[0].url == URL
    finally:
        db.close()


def test_batch_retains_partial_watermarks_exact_epochs_and_other_sources(
    tmp_path,
):
    db = Storage(tmp_path / "notices.sqlite")
    other = replace(PUBLIC, edition_id="other-edition")
    corrupted = replace(notice(key=other), source_revision="0" * 64)
    try:
        observations = [
            notice(URL, observed_at=100),
            notice(OTHER, observed_at=101),
            notice(URL, observed_at=100),  # stale changed source: reject only row
            notice(None, observed_at=102, status="partial"),  # retain full
            notice(URL, observed_at=101),  # earlier than partial watermark
            notice(OTHER, observed_at=103),  # same evidence, same epoch
            corrupted,  # invalid, never a new edition or tombstone
            notice(URL, key=other, observed_at=104),
            notice(URL, observed_at=104),  # fresh changed cover -> epoch 3
        ]
        results = batch(db, observations)
        assert [r.epoch if r else None for r in results] == [
            1, 2, None, 2, None, 2, None, 1, 3,
        ]
        first = read_notice_cover(db, notice_cover_document_ref(PUBLIC))
        second = read_notice_cover(db, notice_cover_document_ref(other))
        assert first.epoch == 3 and first.attribute.candidates[0].url == URL
        assert second.epoch == 1 and second.attribute.candidates[0].url == URL
        assert count_sources(db) == 2
        # CAS-based single-source writers still share the EXACT same rule.
        later = persist_notice_cover(
            db, notice(OTHER, observed_at=105),
            field_path=FIELDS, cover_inventory_complete=True,
            expected_epoch=first.epoch,
        )
        assert later.epoch == 4
        assert read_notice_cover(db, later.document).epoch == 4
    finally:
        db.close()


def test_corrupt_source_rollback_is_local_and_does_not_tombstone_other_cover(
    tmp_path,
):
    db = Storage(tmp_path / "notices.sqlite")
    private = notice(key=PRIVATE)
    public = notice()
    try:
        good = batch(db, [private, public])
        assert all(r is not None for r in good)
        with db._lock, db._connect() as conn:
            conn.execute(
                "UPDATE metadata SET value=? WHERE key=?",
                ('{"schema":1,"schema":1}',
                 "notice.c.observation.v1:" + public.key.edition_id[:0] + good[1].document.source_key.split(":")[-1]),
            )
        result = batch(db, [
            notice(OTHER, observed_at=200),
            notice(OTHER, key=PRIVATE, observed_at=200),
        ])
        assert result[0] is None
        assert result[1] is not None and result[1].epoch == 2
        assert read_notice_cover(db, good[0].document).attribute.candidates[0].url == OTHER
    finally:
        db.close()


def test_core_search_producer_batch_preserves_independent_document_stamps(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(search_orchestration.time, "time", lambda: 200)
    db = Storage(tmp_path / "search.sqlite")
    rows = [public_row(record_id=f"record-{i:04d}") for i in range(260)]
    try:
        assert search_orchestration._mirror_remote_results(
            NS(storage=db), rows, identity_id="alice",
        ) == 260
        refs = [r._core_notice_cover_document_ref for r in rows]
        epochs = [r._core_notice_cover_document_epoch for r in rows]
        assert len(set(refs)) == 260 and set(epochs) == {1}
        assert count_sources(db) == 260
        # Bob is another viewer of the public source, not a new N2 / C writer.
        assert search_orchestration._mirror_remote_results(
            NS(storage=db), rows[128:131], identity_id="bob",
        ) == 3
        assert all(r._core_notice_cover_document_epoch == 1 for r in rows[128:131])
        assert count_sources(db) == 260
    finally:
        db.close()
