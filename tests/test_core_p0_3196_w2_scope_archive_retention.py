"""#3196 W2: archive toggles cannot erase a non-retryable Kobo 403.

All provider traffic is simulated; no HTTP or real ADEPT transactions.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_kobo_backup_fault_isolation import _ready_adept_state, _service


def test_archive_unarchive_does_not_rearm_blocked_delegation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    item = {
        "product_id": "blocked",
        "title": "Synthetic denied edition",
        "revision_id": "rev-1",
        "format": "EPUB",
        "archived": True,
        "downloadable": False,
    }
    service, session = _service(tmp_path, [item])
    monkeypatch.setattr(
        service, "kobo_adept_state", lambda **_kw: _ready_adept_state(),
    )
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "account_id": "account",
        "item_id": "blocked", "revision_id": "rev-1",
        "title": "Synthetic denied edition", "format": "EPUB",
        "status": "attention_required",
        "attention_code": "missing_delegation_scope",
        "attempt_count": 1, "local_path": None,
    })
    original = service.storage.backup_get("kobo", "vault-1", "blocked")
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("scope refusal must never start an implicit GET"),
    )
    try:
        archived = service.sync_kobo_backup()
        assert archived["skipped_archived"] == 1
        assert archived["attempted"] == 0
        while_archived = service.storage.backup_get("kobo", "vault-1", "blocked")
        assert while_archived["status"] == "attention_required"
        assert while_archived["attention_code"] == "missing_delegation_scope"
        assert while_archived["attempt_count"] == original["attempt_count"]

        session._manifest = [{**item, "archived": False, "downloadable": True}]
        unarchived = service.sync_kobo_backup()
        assert unarchived["attempted"] == 0
        assert unarchived["blocked_non_retryable"] == 1
        assert unarchived["items_blocked_by_scope"] == 1
        retained = service.storage.backup_get("kobo", "vault-1", "blocked")
        assert retained["status"] == "attention_required"
        assert retained["attention_code"] == "missing_delegation_scope"
        assert retained["attempt_count"] == original["attempt_count"]
    finally:
        service.close()
