"""P0 #4858 / W2: 0.7 GET result cannot change Kobo source coordinates.

Synthetic session, manifest and EPUB only. No external provider or DRM.
An untrusted result mismatch after GET must leave 'acquiring' for explicit
recovery, not mint a complete backup or allow a second automatic GET.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_kobo_backup_guard import _service, _write_epub


@pytest.mark.parametrize("mutation", (
    "foreign_borrower",
    "empty_other_borrower",
    "foreign_provider",
    "foreign_credential",
    "foreign_edition",
    "loan_in_purchase",
    "foreign_revision",
    "invalid_account_type",
))
def test_w2_rejects_cross_scope_completed_get_before_legacy_receipt(
    tmp_path, monkeypatch, mutation,
):
    service = _service(tmp_path)
    path = tmp_path / "opaque-result.epub"
    calls: list[str] = []

    def delivered(*_args, **_kwargs):
        calls.append("GET")
        _write_epub(path)
        result = {
            "path": path,
            "provider_id": "kobo",
            "credential_id": "vault-1",
            "account_id": "account",
            "item_id": "book-1",
            "loan_id": None,
            "metadata": {"revision_id": "rev-1"},
        }
        if mutation == "foreign_borrower":
            result["account_id"] = "other"
        elif mutation == "empty_other_borrower":
            result["account_id"] = ""
        elif mutation == "foreign_provider":
            result["provider_id"] = "paris"
        elif mutation == "foreign_credential":
            result["credential_id"] = "vault-other"
        elif mutation == "foreign_edition":
            result["item_id"] = "book-elsewhere"
        elif mutation == "loan_in_purchase":
            result["loan_id"] = "loan-foreign"
        elif mutation == "foreign_revision":
            result["metadata"] = {"revision_id": "rev-other"}
        elif mutation == "invalid_account_type":
            result["account_id"] = 7
        return SimpleNamespace(**result)

    monkeypatch.setattr(service, "_execute_kobo_acquisition", delivered)
    try:
        outcome = service.sync_kobo_backup_account("vault-1")
        assert outcome["status"] == "aborted"
        assert outcome["aborted"] is True
        assert outcome["downloaded"] == 0
        assert calls == ["GET"]
        backup = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert backup is not None
        assert backup["status"] == "acquiring"
        assert backup["local_path"] is None
        assert backup["sha256"] is None
        assert path.is_file()  # Never unlink bytes returned before failure.

        # An interrupted result is a quarantine, not a GET retry ticket.
        repeated = service.sync_kobo_backup_account("vault-1")
        assert repeated["attempted"] == repeated["downloaded"] == 0
        assert calls == ["GET"]
        assert service.storage.backup_get("kobo", "vault-1", "book-1")["status"] != "complete"
        with service.storage._lock, service.storage._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM origin_links").fetchone()[0] == 0
    finally:
        service.close()


def test_w2_absent_result_borrower_uses_only_pinned_current_account(
    tmp_path, monkeypatch,
):
    """Legacy executors can omit a borrower, but never invent another one."""
    service = _service(tmp_path)
    path = tmp_path / "fresh-result.epub"

    def delivered(*_args, **_kwargs):
        _write_epub(path)
        return SimpleNamespace(
            path=path, provider_id="kobo", credential_id="vault-1",
            account_id=None, item_id="book-1", loan_id=None,
            metadata={"revision_id": "rev-1"},
        )

    monkeypatch.setattr(service, "_execute_kobo_acquisition", delivered)
    try:
        report = service.sync_kobo_backup_account("vault-1")
        assert report["downloaded"] == 1
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert row["status"] == "complete"
        assert row["account_id"] == "account"
        assert Path(row["local_path"]) == path
    finally:
        service.close()


@pytest.mark.parametrize("field,value", (
    ("record_id", "wrong-edition"),
    ("revision_id", "wrong-revision"),
    ("source_provider", "paris"),
    ("source_credential", "wrong-credential"),
    ("owned", False),
    ("downloadable", False),
))
def test_w2_normalized_source_switch_cannot_start_get(
    tmp_path, monkeypatch, field, value,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    normalize = session.normalize_item
    calls: list[str] = []

    def switched(raw, **kwargs):
        item = normalize(raw, **kwargs)
        setattr(item, field, value)
        return item

    def no_get(*_args, **_kwargs):
        calls.append("GET")
        pytest.fail("A switched normalized source must never GET")

    monkeypatch.setattr(session, "normalize_item", switched)
    monkeypatch.setattr(service, "_execute_kobo_acquisition", no_get)
    try:
        report = service.sync_kobo_backup_account("vault-1")
        assert report["status"] == "aborted"
        assert report["downloaded"] == 0
        assert calls == []
        record = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert record is not None and record["status"] == "acquiring"
        # Replaying the same interrupted manifest cannot start another GET.
        again = service.sync_kobo_backup_account("vault-1")
        assert again["downloaded"] == again["attempted"] == 0
        assert calls == []
    finally:
        service.close()
