"""P0 #3196 W2: the Kobo 0.7 batch/recovery writer yields to typed Core sources.

SQLite and connected synthetic manifest only: no Kobo network, ADEPT, or
filesystem publication. No right or delivery is inferred from a historical row.
"""
from __future__ import annotations

import pytest

from cifereca.core import kobo_backup
from tests.test_kobo_backup_guard import _service


def _source(service, *, edition="book-1", account="account", owner="alice"):
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "INSERT INTO source_references("
            "source_reference_id,identity_id,provider_id,credential_id,"
            "account_id,branch_id,edition_id,right_id,reference_kind,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("source:" + edition, owner, "kobo", "vault-1", account,
             "purchases", edition, "purchase", "purchase", 1, 1),
        )


def _state(service):
    with service.storage._lock, service.storage._connect() as db:
        return tuple(
            tuple(db.execute("SELECT * FROM " + name + " ORDER BY 1").fetchall())
            for name in (
                "backup_items", "acquisition_ledger", "entitlements",
                "artifacts", "placements", "catalog_entries",
                "source_references", "origin_links",
            )
        )


@pytest.mark.parametrize("account,owner", [
    ("account", "alice"),
    ("different-account", "alice"),
    ("different-account", "bob"),
])
def test_typed_source_prevents_w2_legacy_batch_mutation_or_get(
    tmp_path, monkeypatch, account, owner,
):
    service = _service(tmp_path)
    try:
        _source(service, account=account, owner=owner)
        before = _state(service)
        monkeypatch.setattr(service, "_execute_kobo_acquisition",
                            lambda *_a, **_kw: pytest.fail("legacy duplicate GET"))
        result = service.sync_kobo_backup()
        assert result["attempted"] == result["downloaded"] == 0
        assert result["pending"] >= 1
        assert _state(service) == before
    finally:
        service.close()


def test_recovery_preserves_historical_interruption_if_core_source_exists(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        # A pre-canonical interrupted 0.7 claim is not Core publication proof.
        service.storage.backup_put({
            "provider_id": "kobo", "credential_id": "vault-1",
            "account_id": "account", "identity_id": "alice",
            "item_id": "book-1", "revision_id": "rev-1",
            "title": "Unresolved legacy row", "status": "acquiring",
        })
        _source(service)
        before = _state(service)
        monkeypatch.setattr(service, "_execute_kobo_acquisition",
                            lambda *_a, **_kw: pytest.fail("no historical replay GET"))
        result = service.sync_kobo_backup()
        assert result["recovered_interrupted"] == 0
        assert result["attempted"] == 0
        assert result["pending"] >= 1
        assert _state(service) == before
        assert service.storage.backup_get("kobo", "vault-1", "book-1")["status"] == "acquiring"
    finally:
        service.close()


def test_typed_source_appearing_after_refresh_prevents_legacy_acquisition(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    admitted = []
    try:
        def race(_self, *_args, **_kwargs):
            admitted.append("pre-acquire")
            _source(service)
            return False

        monkeypatch.setattr(kobo_backup, "_skip_manifest_item", race)
        monkeypatch.setattr(kobo_backup, "_acquire_manifest_item",
                            lambda *_a, **_kw: pytest.fail("legacy GET/write must be gated"))
        result = service.sync_kobo_backup()
        assert admitted == ["pre-acquire"]
        assert result["attempted"] == 0
        assert result["pending"] >= 1
        assert service.storage.backup_get("kobo", "vault-1", "book-1") is None
    finally:
        service.close()


def test_source_of_another_edition_does_not_disable_legacy_batch(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    from pathlib import Path
    from types import SimpleNamespace
    from tests.test_kobo_backup_guard import _write_epub
    acquired = []
    try:
        _source(service, edition="unrelated-edition")
        def fake_acquisition(item, target):
            acquired.append(item.record_id)
            path = Path(target) / "legacy.epub"
            _write_epub(path, b"synthetic")
            return SimpleNamespace(
                path=path, account_id="account", metadata={},
            )
        monkeypatch.setattr(service, "_execute_kobo_acquisition", fake_acquisition)
        summary = service.sync_kobo_backup()
        assert acquired == ["book-1"]
        assert summary["downloaded"] == 1
        assert service.storage.backup_get("kobo", "vault-1", "book-1")["status"] == "complete"
    finally:
        service.close()
