"""P0 #3909: maintenance after committed B, not a second provider delivery.

Synthetic v14 SQLite + real source Sync, with fault injection between
publication, operation-journal finalization and physical staging cleanup.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cifereca.core import convergence_execution
from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3909_atomic_promotion import (
    rig, _isolate_delivery_lock, _drive,
)
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, SHA


def _published_with_deferred_cleanup(rig, monkeypatch):
    service, db, root, gets = _drive(rig)

    def refused(*args, **kwargs):
        raise OSError("SECRET PRIVATE URL should not escape logs")

    with monkeypatch.context() as patch:
        patch.setattr(convergence_execution, "cleanup_published_staging", refused)
        result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 1
    assert gets == ["signed-right-offer"]
    final, = db.placements()
    assert Path(final["path"]).parent == root / "A" / "Acquisitions"
    assert Path(final["path"]).read_bytes() == BYTES
    staged, = (root / "A" / ".canonical-delivery").glob("run-*/received.pdf")
    assert staged.read_bytes() == BYTES
    with db._lock, db._connect() as sql:
        assert sql.execute(
            "SELECT status FROM acquisition_ledger "
            "WHERE acquisition_id LIKE 'canonical-intake:%'",
        ).fetchone() == ("complete",)
        assert sql.execute(
            "SELECT COUNT(*) FROM origin_links WHERE origin_kind='provider'",
        ).fetchone()[0] == 1
    return service, db, root, gets, staged


def test_next_sync_cleans_post_B_staging_without_get_or_new_placement(
    rig, monkeypatch,
):
    service, db, root, gets, staged = _published_with_deferred_cleanup(rig, monkeypatch)
    before, = db.placements()
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["present_attested"] == 1
    assert result["canonical_execution"]["downloaded"] == 0
    assert gets == ["signed-right-offer"]
    assert not staged.exists()
    assert db.placements() == [before]
    assert Path(before["path"]).read_bytes() == BYTES
    assert list((root / "A" / ".canonical-delivery").glob("run-*")) == []
    third = synchronize_account_source(service, service.account)
    assert third["canonical_execution"]["present_attested"] == 1
    assert gets == ["signed-right-offer"]


def test_next_sync_finalizes_journal_lost_after_B_and_cleanup(rig, monkeypatch):
    service, db, root, gets = _drive(rig)

    def failed_flag(*args, **kwargs):
        raise RuntimeError("PRIVATE JOURNAL DATA")

    with monkeypatch.context() as patch:
        patch.setattr(
            convergence_execution, "mark_completed_delivery_published", failed_flag,
        )
        first = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["downloaded"] == 1
    assert not list((root / "A" / ".canonical-delivery").glob("run-*"))
    with db._lock, db._connect() as sql:
        assert sql.execute(
            "SELECT status,artifact_id FROM acquisition_ledger "
            "WHERE acquisition_id LIKE 'canonical-intake:%'",
        ).fetchone() == ("canonical_receipt_staged", None)
    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["present_attested"] == 1
    assert gets == ["signed-right-offer"]
    with db._lock, db._connect() as sql:
        assert sql.execute(
            "SELECT status,artifact_id FROM acquisition_ledger "
            "WHERE acquisition_id LIKE 'canonical-intake:%'",
        ).fetchone() == ("complete", "sha256:" + SHA)
    assert len(db.placements()) == 1


@pytest.mark.parametrize("fault", ["forged_receipt", "wrong_source", "symlink", "different_sha"])
def test_ambiguous_post_B_bytes_cannot_be_erased_or_adopted(
    rig, monkeypatch, fault,
):
    service, db, root, gets, staged = _published_with_deferred_cleanup(rig, monkeypatch)
    before, = db.placements()
    if fault == "symlink":
        outside = root.parent / "not-owned.pdf"
        outside.write_bytes(BYTES)
        staged.unlink()
        staged.symlink_to(outside)
    else:
        with db._lock, db._connect() as sql:
            key, raw = sql.execute(
                "SELECT acquisition_id,metadata_json FROM acquisition_ledger "
                "WHERE acquisition_id LIKE 'canonical-intake:%'",
            ).fetchone()
            checkpoint = json.loads(raw)
            if fault == "forged_receipt":
                checkpoint["receipt"] = "core-delivery:" + "f" * 64
            elif fault == "wrong_source":
                checkpoint["source"][3] = "OTHER ACCOUNT"
            else:
                checkpoint["sha256"] = "e" * 64
            sql.execute(
                "UPDATE acquisition_ledger SET metadata_json=? WHERE acquisition_id=?",
                (json.dumps(checkpoint), key),
            )
    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["present_attested"] == 1
    assert gets == ["signed-right-offer"]
    assert staged.exists() or staged.is_symlink()
    if fault == "symlink":
        assert staged.is_symlink()
        assert outside.read_bytes() == BYTES
    else:
        assert staged.read_bytes() == BYTES
    assert db.placements() == [before]
    assert Path(before["path"]).read_bytes() == BYTES


def test_without_live_right_consent_does_not_clean_published_staging(
    rig, monkeypatch,
):
    service, db, root, gets, staged = _published_with_deferred_cleanup(rig, monkeypatch)
    service._canonical_persisted_consent = lambda _: frozenset()
    second = synchronize_account_source(service, service.account)
    assert gets == ["signed-right-offer"]
    assert second["canonical_execution"]["downloaded"] == 0
    assert staged.read_bytes() == BYTES
    assert len(db.placements()) == 1
