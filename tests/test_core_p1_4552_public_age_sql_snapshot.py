"""#4552: transaction-scoped age source facts from durable Notice C."""
import json
import sqlite3

from cifereca.core.notice_c_public_age_sql_snapshot import (
    public_age_facts_txn, capture_public_age_sources_txn,
)
from cifereca.core.notice_cover_projection import notice_cover_document_ref
from cifereca.core.notice_cover_repository import (
    _opaque_key, _encode_source_row, _fence_key, _source_fingerprint,
)
from tests.test_core_p1_4552_public_age_descriptive_facts import observation


def _seed(db, source):
    document = notice_cover_document_ref(source.key)
    raw = _encode_source_row(
        source, ("search_result", "thumbnail_url"), False, 1,
    )
    db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("INSERT INTO metadata VALUES (?, ?)", (_opaque_key(document), raw))
    return document


def test_sql_snapshot_reads_exact_persisted_source():
    db = sqlite3.connect(":memory:")
    source = observation()
    document = _seed(db, source)
    facts = public_age_facts_txn(db, document)
    assert len(facts) == 1
    assert facts[0]["edition_id"] == source.key.edition_id


def test_sql_snapshot_rejects_tampered_revision():
    db = sqlite3.connect(":memory:")
    source = observation()
    document = _seed(db, source)
    key = _opaque_key(document)
    row = json.loads(db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()[0])
    row["source_revision"] = "notice-raw.v1:" + "0" * 64
    db.execute("UPDATE metadata SET value=? WHERE key=?", (json.dumps(row), key))
    assert public_age_facts_txn(db, document) == ()


def test_sql_snapshot_rejects_other_source_document():
    from dataclasses import replace
    db = sqlite3.connect(":memory:")
    source = observation()
    document = _seed(db, source)
    other = notice_cover_document_ref(replace(source.key, record_id="search:other"))
    assert public_age_facts_txn(db, other) == ()


def test_sql_capture_matches_coordinator_contract():
    db = sqlite3.connect(":memory:")
    source = observation()
    document = _seed(db, source)
    facts, links = capture_public_age_sources_txn(
        db, documents=(document, document),
        target_edition_id=source.key.edition_id,
    )
    assert len(facts) == 1
    assert links == {}
    other, links = capture_public_age_sources_txn(
        db, documents=(document,), target_edition_id="edition:other",
    )
    assert other == () and links == {}


def test_sql_capture_rejects_invalid_inventory():
    import pytest
    db = sqlite3.connect(":memory:")
    with pytest.raises(ValueError, match="invalid_public_age_snapshot_scope"):
        capture_public_age_sources_txn(
            db, documents=tuple([None] * 129), target_edition_id="edition:one",
        )
    with pytest.raises(ValueError, match="invalid_public_age_snapshot_document"):
        capture_public_age_sources_txn(
            db, documents=(None,), target_edition_id="edition:one",
        )


def test_sql_snapshot_respects_current_source_fence():
    db = sqlite3.connect(":memory:")
    source = observation()
    document = _seed(db, source)
    key = _opaque_key(document)
    proof = _source_fingerprint(
        source, ("search_result", "thumbnail_url"), False,
    )
    db.execute(
        "INSERT INTO metadata VALUES (?, ?)",
        (_fence_key(key), json.dumps({
            "epoch": 1, "last_seen_at": source.observed_at, "proof": proof,
        })),
    )
    assert len(public_age_facts_txn(db, document)) == 1
    db.execute(
        "UPDATE metadata SET value=? WHERE key=?",
        (json.dumps({"epoch": 1, "last_seen_at": source.observed_at,
                     "proof": "0" * 64}), _fence_key(key)),
    )
    assert public_age_facts_txn(db, document) == ()


def test_sql_snapshot_rejects_evicted_source_fence():
    db = sqlite3.connect(":memory:")
    source = observation()
    document = _seed(db, source)
    key = _opaque_key(document)
    db.execute(
        "INSERT INTO metadata VALUES (?, ?)",
        (_fence_key(key), json.dumps({
            "epoch": 2, "last_seen_at": source.observed_at,
            "proof": "0" * 64, "evicted": True,
        })),
    )
    assert public_age_facts_txn(db, document) == ()


def test_exact_age_retention_matches_live_and_refuses_superseded_revision(tmp_path):
    from contextlib import closing
    from cifereca.storage import Storage
    from cifereca.core.notice_cover_repository import evict_notice_cover
    from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
    from tests.test_core_p0_4530_public_age_c import original, project
    from tests.test_core_p0_6366_public_c_sqlite_source_search import persist

    path = tmp_path / "age-retention.sqlite"
    with closing(Storage(path)) as storage:
        source = persist(storage, project(original()), at=100)
        persist_exact_public_search_cover(storage, source)
        args = dict(documents=(source.document,),
                    target_edition_id=source.observation.key.edition_id)

        def capture(db):
            with db._lock, db._connect() as sql:
                sql.execute("BEGIN")
                return capture_public_age_sources_txn(sql, **args)

        live = capture(storage)
        assert len(live[0]) == 1 and live[1] == {}
        assert evict_notice_cover(storage, source.document, expected_epoch=source.epoch)
        assert capture(storage) == live
    with closing(Storage(path)) as storage:
        assert capture(storage) == live
        changed = original()
        changed.metadata["bibliography_evidence"]["youth_age"]["ranges"][0]["age_min"] = 14
        newer = persist(storage, project(changed), at=101)
        assert newer.document == source.document
        assert capture(storage) != live
        assert evict_notice_cover(storage, newer.document, expected_epoch=newer.epoch)
        assert capture(storage) == ((), {})
        assert storage.entitlements() == storage.acquisitions() == []


def test_retained_age_needs_exact_qualified_eviction(tmp_path):
    from contextlib import closing
    from cifereca.storage import Storage
    from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
    from tests.test_core_p0_4530_public_age_c import original, project
    from tests.test_core_p0_6366_public_c_sqlite_source_search import persist

    with closing(Storage(tmp_path / "legacy.sqlite")) as storage:
        source = persist(storage, project(original()))
        persist_exact_public_search_cover(storage, source)
        with storage._lock, storage._connect() as sql:
            sql.execute("DELETE FROM metadata WHERE key=?", (_opaque_key(source.document),))
            assert capture_public_age_sources_txn(
                sql, documents=(source.document,),
                target_edition_id=source.observation.key.edition_id,
            ) == ((), {})
