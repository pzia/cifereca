"""#3196 W3: a manifest gap cannot lose a provider 409 archive veto.

Synthetic SQLite/provider simulations only; no live provider HTTP.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.kobo.vault import KoboVaultError
from cifereca.storage import Storage
from tests.test_kobo_backup_fault_isolation import (
    _ready_adept_state, _service, _write_epub,
)


@pytest.mark.parametrize("status,code", [
    ("skipped_archived", None),
    ("attention_required", "archived_book"),
])
def test_archive_receipt_survives_empty_manifest_without_touching_rights(
    tmp_path: Path, status: str, code: str | None,
):
    storage = Storage(tmp_path / "db.sqlite3")
    try:
        def row(item, *, provider="kobo", state="pending", attention=None):
            return {
                "provider_id": provider, "credential_id": "vault",
                "identity_id": "default", "account_id": "account",
                "item_id": item, "revision_id": "r1",
                "title": "Synthetic edition", "entitlement_type": "owned",
                "local_path": None, "status": state, "attention_code": attention,
            }

        storage.backup_put(row("archived", state=status, attention=code))
        storage.backup_put(row("normal"))
        storage.backup_put(row("unrelated-provider", provider="external",
                               state="skipped_archived"))
        archive = storage.backup_get("kobo", "vault", "archived")
        rights = storage.entitlements()
        receipts = storage.acquisitions()
        result = storage.backup_prune_missing("kobo", "vault", set())
        assert result == {
            "pruned": 0, "local_preserved": 0, "entitlements_inactivated": 0,
        }
        assert storage.backup_get("kobo", "vault", "archived") == archive
        assert storage.backup_get("kobo", "vault", "normal") is not None
        assert storage.backup_get("external", "vault", "unrelated-provider")
        assert storage.entitlements() == rights
        assert storage.acquisitions() == receipts
        assert storage.backup_prune_missing("kobo", "vault", set())["pruned"] == 0
        assert storage.backup_prune_missing("external", "vault", set())["pruned"] == 1
    finally:
        storage.close()


def test_409_then_missing_then_return_does_not_retry_without_unarchive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    item = {
        "product_id": "book-409", "revision_id": "rev-1",
        "title": "Archived edition", "format": "EPUB",
    }
    service, session = _service(tmp_path, [item])
    monkeypatch.setattr(
        service, "kobo_adept_state", lambda **_kw: _ready_adept_state(),
    )
    calls = []

    def archived(search_item, _target):
        calls.append(search_item.record_id)
        raise KoboVaultError(
            "synthetic Kobo archived 409", code="archived_book", status_code=409,
        )

    monkeypatch.setattr(service, "_execute_kobo_acquisition", archived)
    try:
        first = service.sync_kobo_backup_account("vault-1")
        assert first["attempted"] == 1
        assert calls == ["book-409"]
        original = service.storage.backup_get("kobo", "vault-1", "book-409")
        assert original["status"] == "skipped_archived"

        session._manifest = []
        missing = service.sync_kobo_backup_account("vault-1")
        assert missing["attempted"] == 0
        assert missing["legacy_pruned"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "book-409") == original

        session._manifest = [dict(item)]  # no explicit archived=False
        monkeypatch.setattr(
            service, "_execute_kobo_acquisition",
            lambda *_a, **_kw: pytest.fail(
                "archive checkpoint cannot be lost on manifest reappearance"
            ),
        )
        returned = service.sync_kobo_backup_account("vault-1")
        assert returned["attempted"] == 0
        assert returned["skipped_archived"] == 1
        assert service.storage.backup_get(
            "kobo", "vault-1", "book-409",
        )["attempt_count"] == original["attempt_count"]

        def restored(_search_item, target):
            calls.append("unarchived")
            file = Path(target) / "restored.epub"
            _write_epub(file)
            return SimpleNamespace(path=file, account_id="account", metadata={})

        session._manifest = [{**item, "archived": False}]
        monkeypatch.setattr(service, "_execute_kobo_acquisition", restored)
        resumed = service.sync_kobo_backup_account("vault-1")
        assert resumed["attempted"] == 1
        assert resumed["downloaded"] == 1
        assert calls == ["book-409", "unarchived"]
    finally:
        service.close()
