"""#3171: published staging stock must require the exact enabled borrower.

The synthetic present Placement is deliberately NOT a trusted Core receipt:
a valid account progresses to origin verification, never to promotion. A stale
or foreign borrower must be refused earlier without a full account scan.
"""
import pytest

from cifereca.core.canonical_staging_stock import _record
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, SHA, new_source, register, target,
)


def _check(db, source, placement_id):
    with db._lock, db._connect() as sql:
        sql.execute("BEGIN")
        return _record(sql, source, placement_id)


def test_recovery_stock_uses_one_exact_connected_borrower_not_roster(tmp_path):
    db = Storage(tmp_path / "state.sqlite")
    try:
        source, _snapshot = new_source()
        for i in range(100):
            register(db, "A", "seller", "card-A", account=f"sibling-{i}")
        path = target(tmp_path, "A")
        artifact_id = "sha256:" + SHA
        db.artifact_put({
            "artifact_id": artifact_id, "sha256": SHA,
            "size": len(BYTES), "media_format": "pdf",
        })
        placement = db.placement_put({
            "path": str(path), "identity_id": "A",
            "artifact_id": artifact_id, "state": "present",
        })

        with pytest.raises(ValueError, match="canonical_stock_account_unbound"):
            _check(db, source, placement)
        register(db, "A", "seller", "card-A", account="buyer-A")
        # The borrower is admitted, but unlinked physical bytes are never
        # promoted merely because the SHA and profile match.
        with pytest.raises(ValueError, match="canonical_stock_exact_origin_missing"):
            _check(db, source, placement)

        register(db, "A", "unrelated", "card-B", account="other")
        with db._lock, db._connect() as sql:
            other = sql.execute(
                "SELECT connection_id FROM provider_connections "
                "WHERE provider_id='unrelated' AND credential_id='card-B'"
            ).fetchone()[0]
            sql.execute(
                "UPDATE provider_accounts SET connection_id=? "
                "WHERE identity_id='A' AND provider_id='seller' "
                "AND credential_id='card-A' AND account_id='buyer-A'",
                (other,),
            )
        with pytest.raises(ValueError, match="canonical_stock_account_unbound"):
            _check(db, source, placement)
        assert path.read_bytes() == BYTES
    finally:
        db.close()
