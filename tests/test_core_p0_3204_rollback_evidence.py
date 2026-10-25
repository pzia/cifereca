"""P0 #3204 — physical rollback proof is read-only and fail closed.

All fixtures use isolated XDG trees and synthetic Core/plugin bytes.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from cifereca import updater
from tests.test_updater import _transactional_update_fixture


def _restored(tmp_path, monkeypatch):
    root, previous, store, update = _transactional_update_fixture(
        tmp_path, monkeypatch,
    )
    result = updater.rollback()
    transaction_path = Path(update["transaction"])
    saved = updater._read_transaction(transaction_path)
    assert result["ok"] is True
    assert saved["state"] == "rolled_back"
    updater._verify_restored_transaction_state(root, saved)
    return root, previous, store, transaction_path, saved


def test_physical_evidence_attests_absent_and_inactive_packages(tmp_path, monkeypatch):
    root, previous, store, _path, saved = _restored(tmp_path, monkeypatch)
    assert updater._symlink_target(root / "current").resolve() == previous.resolve()
    assert updater._symlink_target(root / "previous") is None
    assert saved["packages"]["org.test"]["before"]["active"] is None
    assert not (store / "org.only-b").exists()
    assert (store / "org.test" / "1.0.0" / "payload.bin").read_bytes() == b"A bytes"
    # The probe is idempotent and cannot heal or rewrite anything.
    before = updater._package_identity("org.test")
    updater._verify_restored_transaction_state(root, saved)
    assert updater._package_identity("org.test") == before


@pytest.mark.parametrize("tamper", ["bytes", "pointer", "unexpected_package"])
def test_verifier_rejects_mutated_bytes_activation_and_unexpected_tree_without_writes(
    tmp_path, monkeypatch, tamper,
):
    root, previous, store, _path, saved = _restored(tmp_path, monkeypatch)
    if tamper == "bytes":
        victim = store / "org.test" / "1.0.0" / "payload.bin"
        victim.write_bytes(b"TAMPERED")
    elif tamper == "pointer":
        victim = store / "org.test" / "current.json"
        victim.write_text('{"active":"1.0.0"}\n', encoding="utf-8")
    else:
        victim = store / "org.only-b" / "unexpected"
        victim.parent.mkdir(parents=True)
        victim.write_bytes(b"NOT PREVIOUSLY INSTALLED")
    preserved = victim.read_bytes()
    with pytest.raises(updater.UpdateError, match="rollback_package_state_mismatch") as failure:
        updater._verify_restored_transaction_state(root, saved)
    assert str(victim) not in str(failure.value)
    assert victim.read_bytes() == preserved
    assert updater._symlink_target(root / "current").resolve() == previous.resolve()


def test_verifier_rejects_core_symlink_drift_without_fixing_it(tmp_path, monkeypatch):
    root, old, store, _path, saved = _restored(tmp_path, monkeypatch)
    replacement = root / "releases" / "unexpected"
    replacement.mkdir(parents=True)
    current = root / "current"
    current.unlink()
    current.symlink_to(os.path.relpath(replacement, root))
    with pytest.raises(updater.UpdateError, match="rollback_core_state_mismatch"):
        updater._verify_restored_transaction_state(root, saved)
    assert updater._symlink_target(root / "current").resolve() == replacement.resolve()
    assert str(old) not in str(replacement)


def test_failed_read_only_proof_prevents_successful_rollback_receipt(tmp_path, monkeypatch):
    root, old, store, update = _transactional_update_fixture(tmp_path, monkeypatch)
    previous_smoke = updater._smoke_restored_plugins

    def corrupt_after_smoke(release, transaction):
        result = previous_smoke(release, transaction)
        # Simulate a concurrently resurrected package immediately before
        # the post-rollback read-only attestation.
        forged = store / "org.only-b" / "unexpected"
        forged.parent.mkdir(parents=True)
        forged.write_bytes(b"concurrent")
        return result

    monkeypatch.setattr(updater, "_smoke_restored_plugins", corrupt_after_smoke)
    with pytest.raises(updater.UpdateError, match="restoration transactionnelle incomplète|Restauration transactionnelle incomplète"):
        updater.rollback()
    transaction = updater._read_transaction(Path(update["transaction"]))
    assert transaction["state"] == "restoration_incomplete"
    assert transaction["restoration_errors"] == ["verification"]
    assert (store / "org.only-b" / "unexpected").read_bytes() == b"concurrent"
    assert updater._symlink_target(root / "current").resolve() == old.resolve()
