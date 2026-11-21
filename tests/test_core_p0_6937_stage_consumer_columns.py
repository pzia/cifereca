"""#6937: known SQL consumers outside initial path/metadata allowlist."""
import json
from pathlib import Path

import pytest

from cifereca.core import derived_variant_retention_convergence as convergence
from cifereca.core import derived_variant_stage_dependencies as dependencies
from cifereca.core import derived_variant_journal as journal
from tests.test_core_p0_4520_derived_journal import _prepared_publication, _publish_reserved


def review(db, root):
    return convergence.review_converged_derived_retention(
        db, library_root=root, identity_id="id1", explicitly_requested=True,
    )


@pytest.mark.parametrize("source", [
    "identity", "device", "sync", "cache_payload", "cache_key", "cache_namespace",
    "origin_evidence", "relative_leaf", "relative_variants", "relative_parent",
])
def test_additional_path_consumers_preserve_committed_receipt(tmp_path, source):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        assert review(db, tmp_path)["counts"]["linked_pair_observed"] == 1
        stage = Path(intent.staged_path)
        alias = str(stage)
        if source == "relative_leaf":
            alias = stage.name
        elif source == "relative_variants":
            alias = "variants/" + stage.name
        elif source == "relative_parent":
            alias = "../other/../variants/" + stage.name
        raw = json.dumps({"nested": [alias]})
        with db._lock, db._connect() as sql:
            if source == "identity":
                sql.execute("UPDATE identities SET metadata_json=? WHERE identity_id='id1'", (raw,))
            elif source == "device":
                sql.execute("INSERT INTO device_bindings(device_id,identity_id,metadata_json,created_at,updated_at) "
                            "VALUES('fixture-device','id1',?,1,1)", (raw,))
            elif source == "sync":
                sql.execute("INSERT INTO sync_runs(sync_run_id,identity_id,scope,status,started_at,detail_json,created_at,updated_at) "
                            "VALUES('fixture-sync','id1','local','complete',1,?,1,1)", (raw,))
            elif source.startswith("cache_"):
                namespace = alias if source == "cache_namespace" else "fixture"
                key = alias if source == "cache_key" else "fixture"
                payload = raw.replace("/", chr(92) + "u002f") if source == "cache_payload" else "[]"
                sql.execute("INSERT INTO cache(namespace,cache_key,payload,stored_at,expires_at) VALUES(?,?,?,?,?)",
                            (namespace, key, payload, 1, 2))
            elif source == "origin_evidence":
                sql.execute("UPDATE origin_links SET evidence_ref=? WHERE origin_kind='derived'", (alias,))
            else:
                sql.execute("INSERT INTO metadata(key,value) VALUES('fixture.alias',?)", (raw,))
        before = journal.summarize_derived_journal(db)
        result = review(db, tmp_path)
        # An altered canonical origin can fail its stronger canonical
        # proof before the consumer scan; neither verdict releases bytes.
        if source == "origin_evidence":
            assert result["counts"]["known_dependency"] + result["counts"]["canonical_sql_unverified"] == 1
        else:
            assert result["counts"]["known_dependency"] == 1
        assert result["counts"]["linked_pair_observed"] == 0
        assert not result["retention_authorized"] and not result["all_dependencies_verified"]
        assert journal.summarize_derived_journal(db) == before
        assert stage.samefile(intent.candidate_path)
    finally:
        db.close()


@pytest.mark.parametrize("payload", ["{broken", "oversize"])
def test_unreadable_or_large_cache_is_not_absence_proof(tmp_path, monkeypatch, payload):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        if payload == "oversize":
            payload = json.dumps("x" * 32768)
            monkeypatch.setattr(dependencies, "_MAX_ROW_BYTES", 16384)
        with db._lock, db._connect() as sql:
            sql.execute("INSERT INTO cache(namespace,cache_key,payload,stored_at,expires_at) VALUES('fixture','key',?,1,2)",
                        (payload,))
        result = review(db, tmp_path)
        assert result["counts"]["external_dependency_unverified"] == 1
        assert not result["retention_authorized"]
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        db.close()
