"""P0 #2797: a three-coordinate search alias never selects between borrowers.

The remote SearchResult has no account coordinate. Core can expose the
legacy open shortcut only when all complete receipts for its exact
provider/credential/product key agree on one borrower. No provider/network.
"""
from __future__ import annotations

import hashlib

import pytest

from tests.test_core_library_placement_identity_scope import _service, _placement


@pytest.mark.parametrize(
    "accounts,expect_open_alias",
    [
        (("buyer-1",), True),
        (("buyer-1", "buyer-1"), True),
        (("buyer-1", "buyer-2"), False),
        ((None, "buyer-1"), False),
        ((None, None), True),
        (("",), True),
        (("123",), True),  # Numeric strings can be legitimate borrower IDs.
    ],
)
def test_search_local_placement_alias_never_guesses_borrower(
    tmp_path, monkeypatch, accounts, expect_open_alias,
):
    service = _service(tmp_path)
    try:
        body = b"same epub contents"
        sha = hashlib.sha256(body).hexdigest()
        artifact = "sha256:" + sha
        service.storage.artifact_put({
            "artifact_id": artifact, "sha256": sha,
            "size": len(body), "media_format": "epub",
            "metadata": {},
        })
        placement = _placement(
            service, tmp_path, owner="alice",
            filename="book.epub", artifact_id=artifact, item_id="book",
        )
        for n, account in enumerate(accounts):
            service.storage.acquisition_put({
                "acquisition_id": f"completed-{n}",
                "identity_id": "alice",
                "provider_id": "paris",
                "credential_id": "paris-credential",
                "account_id": account,
                "item_id": "book",
                "artifact_id": artifact,
                "status": "complete",
                "title": "Synthetic edition",
            })

        def no_provider(*_args, **_kwargs):
            pytest.fail("search local projection must not perform provider I/O")
        for method in ("catalog_scan", "search", "opening_sync", "acquire_digital_loan"):
            monkeypatch.setattr(service, method, no_provider)

        before_placement = service.storage.placement_get(placement)
        before_acquisitions = service.storage.acquisitions()
        link = service.local_placements_for_sources("alice")
        alias = ("paris", "paris-credential", "book")
        if expect_open_alias:
            assert link[alias]["placement_id"] == placement
        else:
            assert alias not in link
        assert service.storage.placement_get(placement) == before_placement
        assert service.storage.acquisitions() == before_acquisitions
    finally:
        service.close()


def test_search_borrower_collision_is_scoped_to_one_supplier_key(tmp_path):
    service = _service(tmp_path)
    try:
        content = b"same epub contents"
        sha = hashlib.sha256(content).hexdigest()
        artifact = "sha256:" + sha
        service.storage.artifact_put({
            "artifact_id": artifact, "sha256": sha,
            "size": len(content), "media_format": "epub",
            "metadata": {},
        })
        placement = _placement(
            service, tmp_path, owner="alice",
            filename="book.epub", artifact_id=artifact, item_id="book",
        )
        for index, (provider, credential, account) in enumerate((
            ("paris", "main", "buyer-1"),
            ("paris", "main", "buyer-2"),
            ("paris", "different", "buyer-2"),
        )):
            service.storage.acquisition_put({
                "acquisition_id": f"r-{index}",
                "identity_id": "alice",
                "provider_id": provider, "credential_id": credential,
                "account_id": account, "item_id": "book",
                "artifact_id": artifact, "status": "complete",
            })
        matched = service.local_placements_for_sources("alice")
        assert ("paris", "main", "book") not in matched
        assert matched[("paris", "different", "book")]["placement_id"] == placement
    finally:
        service.close()



@pytest.mark.parametrize("invalid", (123, True, 1.5, b"binary-borrower"))
def test_acquisition_writer_refuses_ill_typed_borrower_before_sqlite_cast(
    tmp_path, invalid,
):
    service = _service(tmp_path)
    try:
        valid = {
            "acquisition_id": "same-ledger-receipt",
            "identity_id": "alice", "provider_id": "paris",
            "credential_id": "paris-credential", "account_id": "123",
            "item_id": "book", "status": "discovered",
        }
        service.storage.acquisition_put(valid)
        before = service.storage.acquisitions()
        with pytest.raises(ValueError, match="acquisition_account_scope_invalid"):
            service.storage.acquisition_put({
                **valid, "account_id": invalid, "status": "complete",
            })
        assert service.storage.acquisitions() == before
    finally:
        service.close()


def test_legacy_binary_borrower_receipt_never_proves_search_open_scope(
    tmp_path,
):
    """BLOB survives TEXT affinity; passive readers must not trust it."""
    import sqlite3

    service = _service(tmp_path)
    try:
        data = b"same epub contents"
        sha = hashlib.sha256(data).hexdigest()
        artifact = "sha256:" + sha
        service.storage.artifact_put({
            "artifact_id": artifact, "sha256": sha,
            "size": len(data), "media_format": "epub", "metadata": {},
        })
        placement = _placement(
            service, tmp_path, owner="alice",
            filename="book.epub", artifact_id=artifact, item_id="book",
        )
        service.storage.acquisition_put({
            "acquisition_id": "legacy-binary",
            "identity_id": "alice", "provider_id": "paris",
            "credential_id": "paris-credential", "account_id": "valid",
            "item_id": "book", "artifact_id": artifact, "status": "complete",
        })
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "UPDATE acquisition_ledger SET account_id=? WHERE acquisition_id=?",
                (sqlite3.Binary(b"private-raw-account"), "legacy-binary"),
            )
        before = service.storage.acquisitions()
        assert type(before[0]["account_id"]) is bytes
        assert ("paris", "paris-credential", "book") not in (
            service.local_placements_for_sources("alice")
        )
        assert service.storage.acquisitions() == before
        assert service.storage.placement_get(placement)["state"] == "present"
    finally:
        service.close()
