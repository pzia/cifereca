"""P0 #3909: deferred physical cleanup must not masquerade as completed recovery.

The synthetic provider writes one verified PDF; all subsequent checks are
SQLite/FS local and never issue another supplier GET.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from cifereca.core.canonical_promotion import cleanup_published_staging
from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3909_atomic_promotion import rig, _isolate_delivery_lock
from tests.test_core_p0_3909_postcommit_cleanup_dependencies import _checkpoint
from tests.test_core_p0_3909_postcommit_staging_reconciliation import (
    _published_with_deferred_cleanup,
)
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES


@pytest.fixture(autouse=True)
def _postcommit_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "cifereca.core.canonical_postcommit_reconcile.acquisition_lock_file",
        lambda: tmp_path / "state" / "acquisition.lock",
    )


def _add_backup_reference(db, staged: Path) -> None:
    with db._lock, db._connect() as sql:
        sql.execute(
            "INSERT INTO backup_items(provider_id,credential_id,item_id,"
            "title,local_path,status,updated_at) VALUES(?,?,?,?,?,?,?)",
            ("manual", "other", "other-import", "Other",
             str(staged), "complete", 1),
        )


def _remove_backup_reference(db) -> None:
    with db._lock, db._connect() as sql:
        sql.execute(
            "UPDATE backup_items SET local_path=NULL "
            "WHERE provider_id='manual' AND item_id='other-import'",
        )


def test_cleanup_result_is_false_for_referenced_and_missing_attempt(
    rig, monkeypatch,
):
    service, db, root, gets, staged = _published_with_deferred_cleanup(
        rig, monkeypatch,
    )
    completed = _checkpoint(db, service, staged)
    _add_backup_reference(db, staged)
    assert cleanup_published_staging(
        db, source=service.source, completed=completed, library_root=root,
    ) is False
    assert staged.read_bytes() == BYTES
    _remove_backup_reference(db)
    assert cleanup_published_staging(
        db, source=service.source, completed=completed, library_root=root,
    ) is True
    assert not staged.exists()
    assert cleanup_published_staging(
        db, source=service.source, completed=completed, library_root=root,
    ) is False
    assert gets == ["signed-right-offer"]


def test_postcommit_trace_reports_reconciliation_only_after_real_cleanup(
    rig, monkeypatch, caplog,
):
    service, db, root, gets, staged = _published_with_deferred_cleanup(
        rig, monkeypatch,
    )
    before, = db.placements()
    _add_backup_reference(db, staged)
    with caplog.at_level(logging.INFO, logger="cifereca"):
        blocked = synchronize_account_source(service, service.account)
    assert blocked["canonical_execution"]["present_attested"] == 1
    assert not any(
        "code=canonical_postcommit_reconciled" in record.getMessage()
        for record in caplog.records
    )
    assert staged.read_bytes() == BYTES
    assert db.placements() == [before]
    _remove_backup_reference(db)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="cifereca"):
        completed = synchronize_account_source(service, service.account)
    assert completed["canonical_execution"]["present_attested"] == 1
    assert any(
        "code=canonical_postcommit_reconciled" in record.getMessage()
        for record in caplog.records
    )
    assert not staged.exists()
    assert Path(before["path"]).read_bytes() == BYTES
    assert db.placements() == [before]
    assert gets == ["signed-right-offer"]
