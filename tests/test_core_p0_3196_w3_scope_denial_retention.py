"""#3196 W3 — a transiently absent Kobo edition keeps its non-retryable 403.

All fixtures are synthetic. No actual provider HTTP or purchased books.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.storage import Storage


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


