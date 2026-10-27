"""#3275: explicit Kobo B receipt with independent B hash proof after A -> B.

Both Kobo accounts, files, manifest and receipts are synthetic; no network,
download, DRM, rights transfer or provider-side mutation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.core import kobo_local_reconciliation as repair
from cifereca.library_files import sha256_file
from cifereca.models import Account
from tests.test_core_p1_3275_kobo_received_copy_preflight import _kobo_source
from tests.test_kobo_backup_guard import (
    KoboSession, _manifest_row, _service, _write_epub,
)


def test_b_independent_kobo_receipt_can_explicitly_link_received_bytes_and_resync(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    monkeypatch.setattr(
        repair, "acquisition_lock_file", lambda: tmp_path / "acquisition.lock",
    )
    try:
        # A owns its own local bytes; sending does NOT transmit any Kobo right.
        src, placement_a = _kobo_source(service, tmp_path)
        original_bytes = src.read_bytes()
        transferred = service.materialize_to_identity(placement_a, "bob")
        received = Path(transferred["path"])
        assert received.read_bytes() == original_bytes
        assert received != src

        # An independently preserved B receipt attests the *same edition's
        # exact content hash*. Product/title equality alone is never enough.
        b_evidence = tmp_path / "independent-b-evidence" / "book.epub"
        _write_epub(b_evidence, b"synthetic Kobo A book")
        b_sha = sha256_file(b_evidence)
        assert b_sha == sha256_file(received)
        b_size = b_evidence.stat().st_size
        b_evidence.unlink()
        service.storage.provider_connection_put({
            "identity_id": "bob", "provider_id": "kobo",
            "credential_id": "vault-b", "label": "Kobo B",
        })
        a_session = service._sessions[service._session_key("kobo", "vault-1")]
        b_session = KoboSession(
            descriptor=a_session.descriptor,
            credential_id="vault-b", label="Kobo B",
            credentials={}, options={}, manifest=[_manifest_row()],
        )
        b_session.connect()
        b_session._accounts = [Account(
            display_name="Kobo B", unique_id="account-b", provider_id="kobo",
            credential_id="vault-b", identity_id="bob",
        )]
        service._sessions[service._session_key("kobo", "vault-b")] = b_session

        service.storage.backup_put({
            "provider_id": "kobo", "credential_id": "vault-b",
            "account_id": "account-b", "identity_id": "bob",
            "item_id": "book-1", "revision_id": "rev-1",
            "title": "Book One", "format": "epub",
            "status": "reconcile_required", "local_path": None,
            "size": b_size, "sha256": b_sha, "entitlement_type": "owned",
        })
        before_b = [
            e for e in service.storage.entitlements()
            if e["credential_id"] == "vault-b" and e["identity_id"] == "bob"
        ]
        assert len(before_b) == 1
        assert not [
            e for e in service.storage.entitlements()
            if e["credential_id"] == "vault-1" and e["identity_id"] == "bob"
        ]
        monkeypatch.setattr(service, "_execute_kobo_acquisition",
                            lambda *_args, **_kwargs: pytest.fail("unexpected Kobo download"))

        # B's fresh manifest independently reports the same product; without
        # explicit confirmation the existing B receipt stays unresolved.
        first = service.sync_kobo_backup_account("vault-b")
        assert first["attempted"] == first["downloaded"] == 0
        assert first["reconcile_skipped"] >= 1
        assert service.storage.backup_get("kobo", "vault-b", "book-1")["status"] == "reconcile_required"

        args = {
            "credential_id": "vault-b", "account_id": "account-b",
            "item_id": "book-1", "identity_id": "bob",
            "expected_sha256": b_sha, "path": str(received),
        }
        before_placements = [
            p for p in service.storage.placements()
            if p.get("identity_id") == "bob"
        ]
        preview = repair.reconcile_kobo_local(service, **args)
        assert preview["status"] == "preview"
        assert preview["requires_explicit_confirmation"] is True
        assert service.storage.backup_get("kobo", "vault-b", "book-1")["status"] == "reconcile_required"
        assert repair.reconcile_kobo_local(
            service, **args, confirmation=preview["plan_token"],
        )["status"] == "applied"
        assert repair.reconcile_kobo_local(service, **args)["status"] == "already_linked"

        b_backup = service.storage.backup_get("kobo", "vault-b", "book-1")
        assert b_backup["status"] == "complete"
        assert b_backup["local_path"] == str(received)
        b_acquisitions = [
            row for row in service.storage.acquisitions()
            if row["credential_id"] == "vault-b"
        ]
        assert len(b_acquisitions) == 1
        assert b_acquisitions[0]["status"] == "complete"
        assert b_acquisitions[0]["identity_id"] == "bob"
        assert b_acquisitions[0]["account_id"] == "account-b"
        assert [e for e in service.storage.entitlements()
                if e["credential_id"] == "vault-b" and e["identity_id"] == "bob"] == before_b
        b_placements = [
            p for p in service.storage.placements()
            if p.get("identity_id") == "bob"
        ]
        assert len(b_placements) == len(before_placements) == 1
        assert b_placements[0]["metadata"]["materialized_from"] == placement_a
        assert b_placements[0]["metadata"]["transfer_kind"] == "local_copy"
        assert b_placements[0]["metadata"]["file_origin_identity_id"] == "alice"
        assert received.read_bytes() == src.read_bytes() == original_bytes

        # Same B entitlement/edition + already verified local bytes: no
        # second download, no duplicate placement and no transfer of A rights.
        second = service.sync_kobo_backup_account("vault-b")
        assert second["unchanged"] == 1
        assert second["attempted"] == second["downloaded"] == 0
        assert len([p for p in service.storage.placements()
                    if p.get("identity_id") == "bob"]) == 1
        assert service.storage.backup_get("kobo", "vault-b", "book-1")["status"] == "complete"
        assert received.read_bytes() == src.read_bytes() == original_bytes
    finally:
        service.close()
