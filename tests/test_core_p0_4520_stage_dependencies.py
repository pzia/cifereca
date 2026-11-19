"""#4520: external stage references block convergence before physical observation."""
import json
from pathlib import Path

import pytest

from cifereca.core import derived_variant_retention_convergence as review
from cifereca.core import derived_variant_stage_dependencies as dependencies
from cifereca.core import derived_variant_journal as journal
from tests.test_core_p0_4520_derived_journal import _prepared_publication, _publish_reserved


def _review(db, root):
    return review.review_converged_derived_retention(
        db, library_root=root, identity_id="id1", explicitly_requested=True,
    )


@pytest.mark.parametrize("kind", ["generic", "escaped", "backup", "origin", "event"])
def test_external_stage_reference_blocks_only_current_receipt(tmp_path, kind):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        assert _review(db, tmp_path)["counts"]["linked_pair_observed"] == 1
        stage = intent.staged_path
        with db._lock, db._connect() as sql:
            if kind in ("generic", "escaped"):
                raw = json.dumps({"path": stage})
                if kind == "escaped":
                    raw = raw.replace("/", chr(92) + "u002f")
                sql.execute("INSERT INTO metadata(key,value) VALUES(?,?)", ("test.external", raw))
            elif kind == "backup":
                sql.execute(
                    "INSERT INTO backup_items(provider_id,credential_id,item_id,title,local_path,status,updated_at) "
                    "VALUES(?,?,?,?,?,?,?)", ("synthetic", "card", "item", "fixture", stage, "complete", 1),
                )
            elif kind == "origin":
                raw = sql.execute(
                    "SELECT metadata_json FROM origin_links WHERE origin_kind='derived'"
                ).fetchone()[0]
                meta = json.loads(raw)
                meta["external_staging_dependency"] = stage
                sql.execute("UPDATE origin_links SET metadata_json=? WHERE origin_kind='derived'",
                            (json.dumps(meta),))
            else:
                sql.execute(
                    "INSERT INTO action_events(event_id,action_type,outcome,facts_json,occurred_at) "
                    "VALUES(?,?,?,?,?)", ("synthetic-stage", "synthetic", "pending", json.dumps({"path": stage}), 1),
                )
        before = journal.summarize_derived_journal(db)
        report = _review(db, tmp_path)
        assert report["counts"]["known_dependency"] == 1
        assert report["counts"]["linked_pair_observed"] == 0
        assert not report["all_dependencies_verified"] and not report["retention_authorized"]
        assert stage not in repr(report)
        assert journal.summarize_derived_journal(db) == before
        assert Path(stage).samefile(intent.candidate_path)
    finally:
        db.close()


def test_malformed_or_over_budget_metadata_is_not_clear(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        with monkeypatch.context() as patch:
            patch.setattr(dependencies, "_MAX_ROWS", 0)
            report = _review(db, tmp_path)
            assert report["counts"]["external_dependency_unverified"] == 1
        db.metadata_set("test.external", "{broken")
        result = _review(db, tmp_path)
        assert result["counts"]["external_dependency_unverified"] == 1
        assert result["counts"]["linked_pair_observed"] == 0
        assert not result["retention_authorized"]
        assert db.metadata_get("test.external") == "{broken"
    finally:
        db.close()


def test_stage_dependency_added_during_physical_observation_discards_page(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        original = review._observe

        def changed(intent, profile, current):
            db.metadata_set("test.external", json.dumps({"path": intent.staged_path}))
            return original(intent, profile, current)

        monkeypatch.setattr(review, "_observe", changed)
        report = _review(db, tmp_path)
        assert report["status"] == "evidence_changed"
        assert report["checked"] == 0
        assert not any(report["counts"].values())
    finally:
        db.close()


@pytest.mark.parametrize("escaped", [False, True])
def test_stage_reference_in_metadata_key_is_not_clear(tmp_path, escaped):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        key = "test.external:" + intent.staged_path
        if escaped:
            key = json.dumps({"path": intent.staged_path}).replace("/", chr(92) + "u002f")
        db.metadata_set(key, "value")
        result = _review(db, tmp_path)
        assert result["counts"]["known_dependency"] == 1
        assert result["counts"]["linked_pair_observed"] == 0
        assert not result["retention_authorized"]
    finally:
        db.close()


def test_metadata_key_bytes_count_against_scan_budget(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        db.metadata_set("x" * 4096, "v")
        monkeypatch.setattr(dependencies, "_MAX_ROW_BYTES", 4096)
        assert _review(db, tmp_path)["counts"]["external_dependency_unverified"] == 1
    finally:
        db.close()
