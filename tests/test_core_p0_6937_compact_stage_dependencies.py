"""#6937: compact shadows are exempt only as exact self-receipt metadata."""
from dataclasses import asdict, replace
import json
from pathlib import Path

import pytest

from cifereca.core import derived_variant_compact_receipt as compact
from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_stage_dependencies as dependencies
from cifereca.core import derived_variant_retention_convergence as convergence
from cifereca.core import derived_variant_compact_shadow as shadow
from tests.test_core_p0_6937_compact_shadow import committed


def review(db, root):
    return convergence.review_converged_derived_retention(
        db, library_root=root, identity_id="id1", explicitly_requested=True,
    )


def test_exact_shadow_is_not_external_consumer(committed, tmp_path):
    db, intent, _placement, key, raw, pk, proof, args = committed
    assert review(db, tmp_path)["counts"]["linked_pair_observed"] == 1
    before = journal.summarize_derived_journal(db)
    assert shadow.write_compact_shadow(db, **args)["status"] == "shadow_written"
    result = review(db, tmp_path)
    assert result["counts"]["linked_pair_observed"] == 1
    assert not result["all_dependencies_verified"]
    assert not result["retention_authorized"]
    assert not result["quota_refund_authorized"]
    assert not result["destructive_cleanup_allowed"]
    assert db.metadata_get(key) == raw and db.metadata_get(pk) == proof
    assert journal.summarize_derived_journal(db) == before
    assert Path(intent.staged_path).samefile(intent.candidate_path)
    db.metadata_set("test.external", json.dumps({"path": intent.staged_path}))
    assert review(db, tmp_path)["counts"]["known_dependency"] == 1


@pytest.mark.parametrize("kind", ["malformed", "wrong_digest", "orphan"])
def test_invalid_shadow_is_not_an_absence_witness(committed, kind):
    db, intent, _placement, key, raw, pk, proof, _args = committed
    if kind == "malformed":
        db.metadata_set(pk, "unreadable")
    elif kind == "wrong_digest":
        db.metadata_set(compact._PREFIX + "0" * 64, proof)
    else:
        db.metadata_set(pk, proof)
        with db._lock, db._connect() as sql:
            sql.execute("DELETE FROM metadata WHERE key=?", (key,))
    with db._lock, db._connect() as sql:
        sql.execute("BEGIN")
        observed = dependencies.scan_stage_references(sql, {intent.staged_path: key})
    assert observed[intent.staged_path] in ("unverified", "referenced")
    assert Path(intent.staged_path).samefile(intent.candidate_path)
    if kind != "orphan":
        assert db.metadata_get(key) == raw


def test_other_receipt_shadow_does_not_hide_reference(committed):
    db, intent, _placement, key, _raw, pk, proof, _args = committed
    db.metadata_set(pk, proof)
    # A raw scanner selection for another receipt must not exempt this
    # receipt's shadow even if it names the same path.
    other = replace(intent, parent_placement_id="other-placement")
    other_raw = journal._encode({
        "schema": "derived.intent.v2", "nonce": "f" * 32, "intent": asdict(other),
    })
    other_key = journal._COMMITTED + journal._receipt_digest(other_raw)
    with db._lock, db._connect() as sql:
        sql.execute("BEGIN")
        observed = dependencies.scan_stage_references(
            sql, {intent.staged_path: other_key},
        )
    assert observed[intent.staged_path] == "referenced"
    assert db.metadata_get(pk) == proof



@pytest.mark.parametrize("kind", ["title", "extension", "blob", "unknown_table", "attached"])
def test_unclassified_sql_consumers_never_look_clear(committed, kind):
    db, intent, _placement, key, raw, pk, proof, args = committed
    stage = intent.staged_path
    with db._lock, db._connect() as sql:
        if kind == "title":
            sql.execute("UPDATE catalog_entries SET title=?",
                        (stage,))
            # A committed variant can lack a catalogue row in a fixture;
            # use a guaranteed existing identity label as well.
            sql.execute("UPDATE identities SET label=? WHERE identity_id='id1'", (stage,))
        elif kind in ("extension", "blob"):
            sql.execute("ALTER TABLE identities ADD COLUMN future_reference TEXT")
            value = json.dumps({"ref": stage}) if kind == "extension" else b"opaque"
            sql.execute("UPDATE identities SET future_reference=? WHERE identity_id='id1'", (value,))
        elif kind == "unknown_table":
            sql.execute("CREATE TABLE future_reader (path TEXT)")
            sql.execute("INSERT INTO future_reader VALUES(?)", (stage,))
        else:
            sql.execute("ATTACH DATABASE ':memory:' AS other")
        sql.execute("BEGIN") if not sql.in_transaction else None
        result = dependencies.scan_stage_references(sql, {stage: key})
        assert result[stage] == ("referenced" if kind in ("title", "extension") else "unverified")
    assert db.metadata_get(key) == raw
    assert Path(stage).samefile(intent.candidate_path)


@pytest.mark.parametrize("case", ["literal", "dynamic", "temp", "trigger"])
def test_sql_schema_consumers_are_never_silently_ignored(committed, case):
    db, intent, _placement, key, raw, pk, proof, args = committed
    stage = intent.staged_path
    literal = "'" + stage.replace("'", "''") + "'"
    with db._lock, db._connect() as sql:
        if case == "literal":
            sql.execute(f"CREATE VIEW deferred_reader AS SELECT {literal} AS path")
        elif case == "dynamic":
            sql.execute("CREATE VIEW deferred_reader AS SELECT value AS path FROM metadata WHERE 0")
        elif case == "temp":
            sql.execute("CREATE TEMP VIEW deferred_reader AS SELECT 1 AS path")
        else:
            sql.execute(f"CREATE TRIGGER deferred_reader AFTER UPDATE ON identities "
                        f"BEGIN SELECT {literal}; END")
        if not sql.in_transaction:
            sql.execute("BEGIN")
        observed = dependencies.scan_stage_references(sql, {stage: key})
    assert observed[stage] == ("referenced" if case in ("literal", "trigger") else "unverified")
    assert db.metadata_get(key) == raw
    assert Path(stage).samefile(intent.candidate_path)
