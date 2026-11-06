"""#3196 W3 — a transiently absent Kobo edition keeps its non-retryable 403.

All fixtures are synthetic. No actual provider HTTP or purchased books.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.kobo.vault import KoboVaultError
from cifereca.storage import Storage
from tests.test_kobo_backup_fault_isolation import _ready_adept_state, _service


def _row(item_id, *, provider="kobo", credential="vault",
         status="attention_required", code="missing_delegation_scope"):
    return {
        "provider_id": provider,
        "credential_id": credential,
        "identity_id": "default",
        "account_id": "account",
        "item_id": item_id,
        "revision_id": "rev-1",
        "title": "Synthetic edition",
        "entitlement_type": "owned",
        "status": status,
        "attention_code": code,
        "local_path": None,
        "attempt_count": 1,
    }


def test_empty_manifest_keeps_exact_denied_scope_only_for_kobo(tmp_path: Path):
    storage = Storage(tmp_path / "library.sqlite3")
    try:
        storage.backup_put(_row("denied"))
        storage.backup_put(_row("ordinary-attention", code="other_attention"))
        storage.backup_put(_row("stale-pending", status="pending", code=None))
        storage.backup_put(_row("other-credential", credential="different"))
        storage.backup_put(_row("other-provider", provider="external"))
        prior_denied = storage.backup_get("kobo", "vault", "denied")
        prior_other = storage.backup_get("kobo", "different", "other-credential")
        before_entitlements = storage.entitlements()
        before_ledger = storage.acquisitions()

        result = storage.backup_prune_missing("kobo", "vault", set())
        assert result == {
            "pruned": 0,
            "local_preserved": 0,
            "entitlements_inactivated": 0,
        }
        assert storage.backup_get("kobo", "vault", "denied") == prior_denied
        assert storage.backup_get("kobo", "vault", "ordinary-attention") is not None
        assert storage.backup_get("kobo", "vault", "stale-pending") is not None
        assert storage.backup_get("kobo", "different", "other-credential") == prior_other
        assert storage.backup_get("external", "vault", "other-provider")
        assert storage.entitlements() == before_entitlements
        assert storage.acquisitions() == before_ledger

        assert storage.backup_prune_missing("kobo", "vault", set())["pruned"] == 0
        assert storage.backup_prune_missing("external", "vault", set())["pruned"] == 1
    finally:
        storage.close()


def test_absent_then_reappearing_kobo_edition_does_not_auto_retry_403(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    edition = {
        "product_id": "blocked-1",
        "revision_id": "rev-1",
        "title": "Blocked edition",
        "format": "EPUB",
    }
    service, session = _service(tmp_path, [edition])
    monkeypatch.setattr(
        service, "kobo_adept_state", lambda **_kw: _ready_adept_state(),
    )
    attempts = []

    def reject_once(item, _target):
        attempts.append(item.record_id)
        raise KoboVaultError(
            "synthetic denied delegated acquisition",
            code="missing_delegation_scope",
            status_code=403,
        )

    monkeypatch.setattr(service, "_execute_kobo_acquisition", reject_once)
    try:
        denied = service.sync_kobo_backup()
        assert denied["attempted"] == 1
        assert attempts == ["blocked-1"]
        original = service.storage.backup_get("kobo", "vault-1", "blocked-1")
        assert original["status"] == "attention_required"
        assert original["attention_code"] == "missing_delegation_scope"

        session._manifest = []
        absent = service.sync_kobo_backup()
        assert absent["attempted"] == 0
        assert absent["legacy_pruned"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "blocked-1") == original

        session._manifest = [edition]
        monkeypatch.setattr(
            service, "_execute_kobo_acquisition",
            lambda *_a, **_kw: pytest.fail(
                "manifest reappearance cannot override a previous 403"
            ),
        )
        reappeared = service.sync_kobo_backup()
        assert reappeared["attempted"] == 0
        assert reappeared["blocked_non_retryable"] == 1
        assert reappeared["items_blocked_by_scope"] == 1
        assert service.storage.backup_get(
            "kobo", "vault-1", "blocked-1",
        )["attempt_count"] == original["attempt_count"]
        assert attempts == ["blocked-1"]
    finally:
        service.close()
