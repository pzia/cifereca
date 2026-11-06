"""#3196 W2: a typed Kobo edition must never enter the legacy GET writer.

Synthetic manifest, SQLite and no network/provider content call. Neither a
canonical SourceReference nor an old 0.7 receipt licenses a second download.
"""
from __future__ import annotations

import logging

import pytest

from cifereca.core.kobo_backup import (
    _acquire_manifest_item, _kobo_typed_edition_exists, _skip_manifest_item,
)
from tests.test_kobo_backup_reconcile_prune import _manifest_row, _service
from tests.test_core_p0_3196_w2_interrupted_no_reget import (
    _checkpoint, _forbid_download,
)


def _typed(service, *, credential="vault-1", edition="book-1",
           owner="default", account="account", source_id="canonical-1"):
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "INSERT INTO source_references("
            "source_reference_id,identity_id,provider_id,credential_id,"
            "account_id,branch_id,edition_id,right_id,reference_kind,"
            "state,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (source_id, owner, "kobo", credential, account,
             "purchases", edition, "purchase", "purchase",
             "active", 1, 1),
        )


def _source_rows(service):
    with service.storage._lock, service.storage._connect() as db:
        return db.execute(
            "SELECT * FROM source_references ORDER BY source_reference_id"
        ).fetchall()


@pytest.mark.parametrize("owner,account", [
    ("default", "account"),
    ("default", "other-borrower"),
    ("other-profile", "another-borrower"),
])
def test_real_sync_typed_edition_never_creates_backup_or_gets(
        tmp_path, monkeypatch, owner, account):
    """W1's absent backup row cannot license W2's first legacy GET."""
    service = _service(tmp_path, manifest=[_manifest_row()])
    try:
        _typed(service, owner=owner, account=account)
        _forbid_download(service, monkeypatch)
        sources = _source_rows(service)
        rights = service.storage.entitlements()
        result = service.sync_kobo_backup()
        assert result["attempted"] == result["downloaded"] == 0
        assert result["skipped"] == 1
        assert service.storage.backup_get("kobo", "vault-1", "book-1") is None
        assert _source_rows(service) == sources
        assert service.storage.entitlements() == rights
        assert service.storage.placements() == []
    finally:
        service.close()


def test_typed_edition_with_existing_historical_receipt_keeps_it_unchanged(
        tmp_path, monkeypatch):
    service = _service(tmp_path, manifest=[_manifest_row(revision_id="new")])
    try:
        service.storage.backup_put(_checkpoint(
            status="complete", revision="old",
        ))
        before = service.storage.backup_get("kobo", "vault-1", "book-1")
        _typed(service)
        _forbid_download(service, monkeypatch)
        result = service.sync_kobo_backup()
        assert result["attempted"] == result["downloaded"] == 0
        assert result["skipped"] == 1
        assert service.storage.backup_get("kobo", "vault-1", "book-1") == before
    finally:
        service.close()


def test_typed_edition_inserted_between_preflight_and_execution_blocks_get(
        tmp_path, monkeypatch):
    """The second guard precedes even the acquiring legacy checkpoint."""
    service = _service(tmp_path, manifest=[])
    try:
        _forbid_download(service, monkeypatch)
        base = _checkpoint(status="pending")
        assert not _kobo_typed_edition_exists(service, "vault-1", "book-1")
        events = []
        summary = {"attempted": 0, "downloaded": 0, "skipped": 0}
        _typed(service)
        result = _acquire_manifest_item(
            service, None, None,
            ("rev-1", "Book One", "account", None, base),
            _manifest_row(), tmp_path, 0, 1, summary,
            lambda event, **kw: events.append((event, kw.get("code"))),
            lambda *_a, **_kw: pytest.fail("must not record legacy error"),
            logging.getLogger("test"),
        )
        assert result is False
        assert summary == {"attempted": 0, "downloaded": 0, "skipped": 1}
        assert events == [("skipped", "kobo_canonical_source_authority")]
        assert service.storage.backup_get("kobo", "vault-1", "book-1") is None
    finally:
        service.close()


@pytest.mark.parametrize("other_key", ["credential", "edition"])
def test_unrelated_typed_source_does_not_block_legacy_edition(
        tmp_path, other_key):
    service = _service(tmp_path, manifest=[])
    try:
        _typed(
            service, credential="other-vault" if other_key == "credential" else "vault-1",
            edition="other-book" if other_key == "edition" else "book-1",
        )
        assert not _kobo_typed_edition_exists(service, "vault-1", "book-1")
        base = _checkpoint(status="pending")
        base["status"] = "pending"
        summary = {"skipped": 0, "pending": 0}
        emitted = []
        assert _skip_manifest_item(
            service, ("rev-1", "Book One", "account", None, base),
            _manifest_row(), tmp_path, {}, True, {}, 0, 1,
            summary, lambda event, **kw: emitted.append(event),
        ) is False
        assert summary == {"skipped": 0, "pending": 0}
        assert emitted == []
    finally:
        service.close()
