"""P0 #3196: a historical Kobo receipt is not proof of present usable bytes.

Only synthetic local files and SQLite. No provider GET, ADEPT, writing to
user-owned paths or changing ownership; the target is inspected passively.
"""
from __future__ import annotations

import hashlib

import pytest

from tests.test_owned_library_download import case
from tests.test_core_p0_3196_owned_download_account_scope import (
    _old_completed_receipt, _lookup,
)


def test_intact_scoped_receipt_still_vetoes_redundant_owned_download(case):
    file = _old_completed_receipt(case, account="account")
    assert file.is_file()
    assert _lookup(case, account_id="account") is not None
    assert case.service.owned_library_download_target(case.entry) is None
    assert not case.calls and not case.session.resolve_calls


@pytest.mark.parametrize("change", [
    "same_size_different_sha", "symlink_same_bytes",
    "foreign_profile_directory", "artifact_digest_diverged", "missing_file",
])
def test_incorrect_historical_bytes_never_block_exact_owned_download(
    case, change,
):
    path = _old_completed_receipt(case, account="account")
    physical = path.read_bytes()
    before = case.service.storage.entitlements()
    if change == "same_size_different_sha":
        path.write_bytes(physical[:-1] + bytes([physical[-1] ^ 1]))
    elif change == "symlink_same_bytes":
        sibling = path.with_name("synthetic-symlink-target.pdf")
        sibling.write_bytes(physical)
        path.unlink()
        path.symlink_to(sibling)
    elif change == "foreign_profile_directory":
        other = case.root / "EBooks" / "different-profile" / "relocated.pdf"
        other.parent.mkdir(parents=True, exist_ok=True)
        path.rename(other)
        with case.service.storage._lock, case.service.storage._connect() as db:
            db.execute("UPDATE placements SET path=? WHERE path=?",
                       (str(other), str(path)))
    elif change == "artifact_digest_diverged":
        # Simulate an inconsistent ledger/artifact after historical import;
        # its file remains unchanged and no receipt may be promoted to proof.
        digest = hashlib.sha256(physical).hexdigest()
        with case.service.storage._lock, case.service.storage._connect() as db:
            db.execute("UPDATE artifacts SET sha256=? WHERE artifact_id=?",
                       ("0" * 64, "sha256:" + digest))
    else:
        path.unlink()
    # A physical symlink is no longer a readable legacy Placement either:
    # strict local-book opening must not follow a substituted inode, even
    # when the linked file happens to have the same bytes. This is stricter
    # than the former four-key lookup, not an acquisition/reconciliation veto.
    if change in {"missing_file", "symlink_same_bytes"}:
        assert _lookup(case) is None
    else:
        assert _lookup(case) is not None
    assert _lookup(case, account_id="account") is None
    target = case.service.owned_library_download_target(case.entry)
    assert target is not None and target.entitlement_id == "right-1"
    assert case.service.storage.entitlements() == before
    assert not case.calls and not case.session.resolve_calls


def test_legacy_shared_profile_directory_never_attests_a_kobo_receipt(case):
    path = _old_completed_receipt(case, account="account")
    assert _lookup(case, account_id="account") is not None
    directory = case.service.storage.identity_get("default")["directory_name"]
    # In production this collision is prevented by UNIQUE. Simulate a
    # corrupted imported DB only in this disposable fixture.
    with case.service.storage._lock, case.service.storage._connect() as db:
        db.execute("DROP INDEX uq_identity_directory_name")
    try:
        case.service.storage.identity_put({
            "identity_id": "shadow-owner", "label": "Synthetic shadow",
            "directory_name": directory,
        })
        assert _lookup(case, account_id="account") is None
        assert case.service.owned_library_download_target(case.entry) is not None
        assert path.is_file() and not case.calls
    finally:
        with case.service.storage._lock, case.service.storage._connect() as db:
            db.execute("DELETE FROM identities WHERE identity_id='shadow-owner'")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_identity_directory_name "
                "ON identities(directory_name)"
            )
    assert _lookup(case, account_id="account") is not None


def test_many_unrelated_profiles_do_not_change_exact_local_byte_proof(case):
    path = _old_completed_receipt(case, account="account")
    for i in range(220):
        case.service.storage.identity_put({
            "identity_id": f"unrelated-{i:04d}", "label": "Synthetic",
            "directory_name": f"other-root-{i:04d}",
        })
    assert _lookup(case, account_id="account") is not None
    assert case.service.owned_library_download_target(case.entry) is None
    assert path.is_file() and not case.calls
