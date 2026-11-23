"""#4552: transaction-scoped age source facts from durable Notice C."""
from dataclasses import dataclass
import json
import sqlite3

from cifereca.core.notice_c_public_age_sql_snapshot import public_age_facts_txn
from cifereca.core.notice_cover_projection import notice_cover_document_ref
from cifereca.core.notice_cover_repository import (
    _opaque_key, _encode_source_row, _source_fingerprint,
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
