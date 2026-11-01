"""#2847: safe, counts-only visibility of *blocked* missing Placement retirements.

Direct catalogue/backup counts are diagnostic; a zero does NOT imply that
opaque checkpoints, declarations, receipts or metadata have no references.
None of these blocked previews can ever authorize a SQLite DELETE.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.missing_placement_retirement import (
    prepare_missing_placement_retirement,
)
from tests.test_core_p0_2847_operator_session import _session
from tests.test_core_p0_2847_relocation_transaction import catalogue, rig


def test_linked_catalogue_reports_count_but_does_not_issue_retirement_consent(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    direct = prepare_missing_placement_retirement(
        storage, placement_id=pid, identity_id="alice", library_root=root,
    )
    assert direct == {
        "status": "blocked", "reason": "dependent_references",
        "catalogue_rows": 1, "backup_rows": 0,
    }
    service, operator = _session(rig)
    preview = operator.preview_retirement(placement_id=pid)
    assert preview == {
        "status": "blocked", "reason": "dependent_references",
        "catalogue_rows": 1, "backup_rows": 0, "read_only": True,
    }
    assert "plan_token" not in str(preview)
    assert str(old) not in str(preview)
    assert pid not in str(preview)
    assert operator.confirm(user_confirmed=True)["reason"] == "confirmation_required"
    assert storage.placement_get(pid)["state"] == "missing"
    assert storage.catalog_entry_get("catalogue:original")["placement_id"] == pid
    assert new.read_bytes() == payload


def test_opaque_id_only_checkpoint_blocks_even_with_both_counts_zero(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    storage.metadata_set("operator.pending", json.dumps({"placement_id": pid}))
    _, operator = _session(rig)
    preview = operator.preview_retirement(placement_id=pid)
    assert preview == {
        "status": "blocked", "reason": "dependent_references",
        "catalogue_rows": 0, "backup_rows": 0, "read_only": True,
    }
    assert operator.confirm(user_confirmed=True)["reason"] == "confirmation_required"
    assert storage.placement_get(pid)["path"] == str(old)
    assert storage.metadata_get("operator.pending") is not None


def test_legacy_backup_path_blocks_and_counts_without_exposing_account(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    with storage._lock, storage._connect() as sql:
        sql.execute(
            "INSERT INTO backup_items(provider_id,credential_id,item_id,"
            "title,status,updated_at,local_path) VALUES(?,?,?,?,?,?,?)",
            ("kobo", "private-credential", "book-a",
             "Private title", "complete", 1, str(old)),
        )
    _, operator = _session(rig)
    result = operator.preview_retirement(placement_id=pid)
    assert result == {
        "status": "blocked", "reason": "dependent_references",
        "catalogue_rows": 0, "backup_rows": 1, "read_only": True,
    }
    assert "private-credential" not in str(result)
    assert "Private title" not in str(result)
    assert str(old) not in str(result)
    assert operator.confirm(user_confirmed=True)["reason"] == "confirmation_required"
    assert storage.placement_get(pid)["state"] == "missing"


def test_orphan_remains_actionable_with_separate_human_confirmation(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    _, operator = _session(rig)
    preflight = operator.preview_retirement(placement_id=pid)
    assert preflight["status"] == "ready"
    assert preflight["requires_explicit_confirmation"] is True
    assert "plan_token" not in preflight
    assert operator.confirm()["reason"] == "confirmation_required"
    assert storage.placement_get(pid)["state"] == "missing"
