"""P0 #3909: don't lose an exact Core staging receipt behind 32 legacy rows.

A real v14 SQLite + synthetic source Sync creates a committed historical B
whose Placement still points into Core staging. The next source Sync must
promote only an independently bound Core receipt and never call GET again.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3909_historical_staging_stock import (
    rig, _isolate_delivery_lock, _stock_shared_acquisition_lock,
    _legacy_placement,
)
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, SHA


def _prefix_journal_with_unrelated(db, *, amount: int, canonical_prefix: bool):
    with db._lock, db._connect() as sql:
        checkpoint = sql.execute(
            "SELECT acquisition_id,identity_id,provider_id,credential_id,"
            "account_id,item_id,status,artifact_id,metadata_json,"
            "created_at,updated_at FROM acquisition_ledger "
            "WHERE acquisition_id LIKE 'canonical-intake:%'",
        ).fetchone()
        assert checkpoint is not None and checkpoint[6] == "complete"
        # Put unrelated rows first in the SQLite journal's natural/index
        # iteration order. The old LIMIT 32 masked the canonical proof.
        sql.execute(
            "DELETE FROM acquisition_ledger WHERE acquisition_id=?",
            (checkpoint[0],),
        )
        for idx in range(amount):
            key = (
                f"canonical-intake:{idx:064x}" if canonical_prefix
                else f"legacy-acquisition-{idx:06d}"
            )
            if key == checkpoint[0]:
                key += "-untrusted"
            sql.execute(
                "INSERT INTO acquisition_ledger("
                "acquisition_id,identity_id,provider_id,credential_id,"
                "account_id,item_id,status,artifact_id,metadata_json,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (key, *checkpoint[1:6], "complete", checkpoint[7], "{}",
                 checkpoint[9], checkpoint[10]),
            )
        sql.execute(
            "INSERT INTO acquisition_ledger("
            "acquisition_id,identity_id,provider_id,credential_id,"
            "account_id,item_id,status,artifact_id,metadata_json,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            checkpoint,
        )
        assert sql.execute(
            "SELECT COUNT(*) FROM acquisition_ledger WHERE item_id=?",
            (checkpoint[5],),
        ).fetchone()[0] >= amount + 1


@pytest.mark.parametrize("canonical_prefix", [False, True])
def test_48_unrelated_receipts_cannot_hide_exact_published_staging_receipt(
    rig, monkeypatch, canonical_prefix,
):
    service, db, root, old, gets = _legacy_placement(rig, monkeypatch)
    original_id = db.placements()[0]["placement_id"]
    _prefix_journal_with_unrelated(
        db, amount=48, canonical_prefix=canonical_prefix,
    )
    recovered = synchronize_account_source(service, service.account)
    assert recovered["canonical_execution"]["recovered"] == 1
    assert gets == ["signed-right-offer"]
    current, = db.placements()
    assert current["placement_id"] == original_id
    final = Path(current["path"])
    assert final.parent == root / "A" / "Acquisitions"
    assert final.read_bytes() == BYTES
    assert current["artifact_id"] == "sha256:" + SHA
    assert not old.exists()
    with db._lock, db._connect() as sql:
        rows = sql.execute(
            "SELECT COUNT(*) FROM origin_links WHERE origin_kind='provider'",
        ).fetchone()[0]
        assert rows == 1
    again = synchronize_account_source(service, service.account)
    assert again["canonical_execution"]["present_attested"] == 1
    assert gets == ["signed-right-offer"]


def test_more_than_512_canonical_candidates_refuses_uncertain_stock_repair(
    rig, monkeypatch,
):
    service, db, root, old, gets = _legacy_placement(rig, monkeypatch)
    _prefix_journal_with_unrelated(db, amount=513, canonical_prefix=True)
    denied = synchronize_account_source(service, service.account)
    assert denied["canonical_execution"]["failed"] == 1
    assert gets == ["signed-right-offer"]
    assert db.placements()[0]["path"] == str(old)
    assert old.read_bytes() == BYTES
    destination = root / "A" / "Acquisitions"
    assert not destination.exists() or list(destination.iterdir()) == []
    assert db.placements()[0]["artifact_id"] == "sha256:" + SHA
