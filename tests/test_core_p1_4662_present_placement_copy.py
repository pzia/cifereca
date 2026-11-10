"""P1 #4662: placement A is enough for a separate consented native send to B.

SQLite and filesystem tests, with no provider loan, ADEPT request or network.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.test_core0925_open_destinations import _service
from tests.test_p1_4291_shared_transfer_placement import source


def _setup(tmp_path):
    service = _service(tmp_path)
    path, placement_id = source(
        service, tmp_path / "EBooks", "paris",
        item="ebook-exact", data=b"real local bytes from A",
    )
    service.select_identity("bob")
    return service, path, placement_id


def _send(service, placement_id, *, confirmed=True, **changes):
    scope = dict(
        source_placement_id=placement_id,
        source_identity_id="alice",
        source_artifact_id="sha256:legacy",
        requesting_identity_id="bob",
        expected_account_context_epoch=service._account_context_epoch,
        expected_source_context_epoch=service._source_context_epoch,
        confirmed=confirmed,
    )
    scope.update(changes)
    return service.send_present_placement_copy(**scope)


def test_present_placement_allows_explicit_copy_without_a_provider_loan(tmp_path):
    service, src, placement = _setup(tmp_path)
    before_loans = service.storage.entitlements()
    before_acquisitions = service.storage.acquisitions()
    service.acquire_digital_loan = Mock(side_effect=AssertionError("no GET"))
    service.execute_acquisition_request = Mock(
        side_effect=AssertionError("no ADEPT"),
    )
    try:
        assert before_loans == []
        assert _send(service, placement, confirmed=False)["reason_code"] == (
            "local_send_confirmation_required"
        )
        assert not any(p["identity_id"] == "bob"
                       for p in service.storage.placements())

        created = _send(service, placement)
        assert created == {
            "status": "complete", "loan": "not_attempted",
            "delivery": "present", "transfer": "completed",
            "copy": "created",
            "reason_code": "independent_local_copy_completed",
            "retry_borrow": False,
        }
        rows = [row for row in service.storage.placements()
                if row["identity_id"] == "bob"]
        assert len(rows) == 1
        dest = Path(rows[0]["path"])
        assert dest.parent == tmp_path / "EBooks" / "bob" / "Acquisitions"
        assert dest.read_bytes() == src.read_bytes()
        assert dest.stat().st_ino != src.stat().st_ino
        assert service.storage.entitlements() == before_loans
        assert service.storage.acquisitions() == before_acquisitions
        assert service.storage.catalog_entries() != []
        copy_again = _send(service, placement)
        assert copy_again["status"] == "complete"
        assert copy_again["copy"] == "already_present"
        assert len([row for row in service.storage.placements()
                    if row["identity_id"] == "bob"]) == 1
        service.acquire_digital_loan.assert_not_called()
        service.execute_acquisition_request.assert_not_called()
    finally:
        service.close()


@pytest.mark.parametrize("changes", [
    {"source_identity_id": "bob"},
    {"source_artifact_id": "sha256:wrong-edition"},
    {"requesting_identity_id": "alice"},
    {"source_placement_id": "nonexistent-placement"},
    {"source_identity_id": ""},
    {"source_artifact_id": True},
    {"confirmed": "yes"},
    {"expected_source_context_epoch": True},
])
def test_scope_is_strict_and_cannot_create_a_copy(tmp_path, changes):
    service, _src, placement = _setup(tmp_path)
    try:
        outcome = _send(service, placement, **changes)
        assert outcome["status"] == "blocked"
        assert outcome["retry_borrow"] is False
        assert not any(row["identity_id"] == "bob"
                       for row in service.storage.placements())
    finally:
        service.close()


def test_bob_to_alice_to_bob_revokes_old_ui_epoch(tmp_path):
    service, _src, placement = _setup(tmp_path)
    epoch = service._account_context_epoch
    try:
        service.select_identity("alice")
        service.select_identity("bob")
        assert service._account_context_epoch != epoch
        outcome = _send(service, placement,
                        expected_account_context_epoch=epoch)
        assert outcome["reason_code"] == "local_send_context_revoked"
        assert not any(row["identity_id"] == "bob"
                       for row in service.storage.placements())
    finally:
        service.close()


def test_missing_or_replaced_file_stays_blocked_without_supplier_retry(tmp_path):
    service, src, placement = _setup(tmp_path)
    src.unlink()
    try:
        outcome = _send(service, placement)
        assert outcome["status"] == "blocked"
        assert outcome["loan"] == "not_attempted"
        assert outcome["transfer"] == "blocked"
        assert not any(row["identity_id"] == "bob"
                       for row in service.storage.placements())
    finally:
        service.close()


def test_sender_foreign_symlink_does_not_create_recipient_copy(tmp_path):
    service, src, placement = _setup(tmp_path)
    external = tmp_path / "private.epub"
    external.write_bytes(b"other profile bytes")
    src.unlink()
    src.symlink_to(external)
    try:
        assert _send(service, placement)["status"] == "blocked"
        assert external.read_bytes() == b"other profile bytes"
        assert not any(row["identity_id"] == "bob"
                       for row in service.storage.placements())
    finally:
        service.close()
