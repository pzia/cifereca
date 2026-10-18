"""P0 #1905: malformed persisted artifact kinds are never proven local books."""
from __future__ import annotations

import json

import pytest

from tests.test_core_p0_1905_numilog_offline_preview import (
    _broker, _ledger, _owned, _preview,
)
from tests.test_core_p0_1862_numilog_store_mirror import _connect
from tests.test_sync_state_contract import _service


@pytest.mark.parametrize(("metadata", "trusted_file"), [
    ({}, True),  # Older stored file without the explicit kind remains valid.
    ({"artifact_kind": "file"}, True),
    ({"artifact_kind": "bundle"}, False),
    ({"artifact_kind": "not-a-valid-kind"}, False),
    ({"artifact_kind": "FILE"}, False),
    ({"artifact_kind": None}, False),
    ({"artifact_kind": True}, False),
    ({"artifact_kind": 17}, False),
])
@pytest.mark.parametrize("verify", [False, True])
def test_numilog_preview_rejects_explicit_invalid_artifact_kind(
    tmp_path, metadata, trusted_file, verify,
):
    service = _service(tmp_path)
    try:
        account, session, calls = _connect(service, number=0)
        item, entitlement_id = _owned(service, account)
        _ledger(service, account, item, entitlement_id)
        # artifact_put() sanitizes unknown kinds: simulate a legacy/corrupt
        # SQLite metadata row to test the actual read-time trust boundary.
        with service.storage._lock, service.storage._connect() as db:
            (artifact_id,) = db.execute(
                "SELECT artifact_id FROM artifacts"
            ).fetchone()
            db.execute(
                "UPDATE artifacts SET metadata_json=? WHERE artifact_id=?",
                (json.dumps(metadata), artifact_id),
            )
        session.library = lambda: pytest.fail("no remote inventory")
        session.download_direct = lambda *_args: pytest.fail("no acquisition")
        broker, source_id = _broker(service, account)

        result = _preview(broker, source_id, verify=verify)
        assert result.ok and result.changed == 0
        assert result.facts["acquisition_authorized"] is False
        counts = result.facts["counts"]
        assert counts["owned_purchases"] == 1
        assert counts["potentially_missing"] == 0
        if trusted_file:
            assert counts["already_local_linked"] == 1
            assert counts["already_local_verified"] == int(verify)
            assert counts["needs_file_verification"] == int(not verify)
            assert counts["needs_reconciliation"] == 0
        else:
            assert counts["already_local_linked"] == 0
            assert counts["already_local_verified"] == 0
            assert counts["needs_reconciliation"] == 1
        assert calls == []
    finally:
        service.close()
