"""Adversarial fail-closed tests for disk/permission failures in the vault.

This test injects deterministic ENOSPC/EACCES at filesystem syscall
boundaries, rather than consuming the CI runner's real disk. All HTTP
observations are fictitious. No ciphertext or raw private payload is exported.
"""
from __future__ import annotations

import errno
from hashlib import sha256
import os

import pytest
pytest.importorskip("cryptography")

import browser_recorder_vault as vault_impl
from browser_recorder_http import PrivateExchange, PrivateHttpTrace
from browser_recorder_vault import PrivateSessionVault, VaultError

PASSWORD = "FICTIONAL-DISK-FAILURE-TEST-KEY-2026"
ACCOUNT = "FICTIONAL_PRIVATE_ACCOUNT_197"
ORIGIN = "https://fictional-full-disk.example.org"


def _trace():
    value = PrivateHttpTrace((ORIGIN,))
    value.exchanges.append(PrivateExchange(
        1, "GET",
        ORIGIN + "/catalog?account=" + ACCOUNT,
        status=200,
        mime="application/json",
        response_body='{"account":"' + ACCOUNT + '"}',
        response_body_state="captured",
    ))
    return value


def _enospc(*_args, **_kwargs):
    raise OSError(errno.ENOSPC, "No space left on device (synthetic)")


def test_disk_full_before_first_fsync_leaves_no_snapshot_or_plaintext(
    tmp_path, monkeypatch,
):
    vault = PrivateSessionVault(tmp_path / "vault")
    trace = _trace()
    writer = vault.begin(PASSWORD)
    with monkeypatch.context() as m:
        m.setattr(vault_impl.os, "fsync", _enospc)
        with pytest.raises(VaultError, match="vault_write_failed"):
            writer.save(trace, "recording", force=True)
    assert vault.list_ids() == ()
    assert not list(vault.path.glob("*.part"))
    assert not list(vault.path.glob(".*.part"))
    assert not list(vault.path.glob("*.har"))
    assert not list(vault.path.glob("*.json"))
    assert ACCOUNT.encode() not in b"".join(
        x.read_bytes() for x in vault.path.iterdir() if x.is_file()
    )
    writer.save(trace, "completed", force=True)
    assert vault.list_ids() == (writer.session_id,)
    stored = vault.open(writer.session_id, PASSWORD)
    assert stored.recovery_status == "completed"
    assert len(stored.trace.exchanges) == 1
    stored.trace.destroy()
    trace.destroy()


def test_disk_full_during_incremental_save_preserves_prior_committed_snapshot(
    tmp_path, monkeypatch,
):
    vault = PrivateSessionVault(tmp_path / "vault")
    trace = _trace()
    writer = vault.begin(PASSWORD)
    writer.save(trace, "recording", force=True)
    source = vault.path / (writer.session_id + ".cbrv")
    initial = source.read_bytes()
    trace.exchanges.append(PrivateExchange(
        2, "POST", ORIGIN + "/details", status=302,
        request_body='{"account":"' + ACCOUNT + '"}',
        request_body_state="captured",
    ))
    with monkeypatch.context() as m:
        m.setattr(vault_impl.os, "fsync", _enospc)
        with pytest.raises(VaultError, match="vault_write_failed"):
            writer.save(trace, "completed", force=True)
    assert source.read_bytes() == initial
    assert not list(vault.path.glob(".*.part"))
    restored = vault.open(writer.session_id, PASSWORD)
    assert restored.recovery_status == "partial"
    assert len(restored.trace.exchanges) == 1
    restored.trace.destroy()
    # The writer was NOT sealed by a failed final save: retry is possible.
    writer.save(trace, "completed", force=True)
    assert vault.open(writer.session_id, PASSWORD).recovery_status == "completed"
    assert len(vault.list_ids()) == 1
    trace.destroy()


def test_label_fsync_enospc_does_not_change_name_or_capture(tmp_path, monkeypatch):
    vault = PrivateSessionVault(tmp_path / "vault")
    trace = _trace()
    writer = vault.begin(PASSWORD)
    writer.save(trace, "completed", force=True)
    original_source = vault.path / (writer.session_id + ".cbrv")
    before = sha256(original_source.read_bytes()).hexdigest()
    vault.set_label(writer.session_id, PASSWORD, "FICTIONAL_PRIVATE_LABEL_1")
    name_file = vault.path / (writer.session_id + ".cbrn")
    old_ciphertext = name_file.read_bytes()
    with monkeypatch.context() as m:
        m.setattr(vault_impl.os, "fsync", _enospc)
        with pytest.raises(VaultError, match="vault_write_failed"):
            vault.set_label(writer.session_id, PASSWORD, "NEW_FAKE_PRIVATE_LABEL")
    assert name_file.read_bytes() == old_ciphertext
    assert vault.get_label(writer.session_id, PASSWORD) == "FICTIONAL_PRIVATE_LABEL_1"
    assert sha256(original_source.read_bytes()).hexdigest() == before
    assert not list(vault.path.glob(".*.labelpart"))
    assert ACCOUNT.encode() not in name_file.read_bytes()
    trace.destroy()


def test_unsafe_directory_and_snapshot_permissions_fail_closed(tmp_path):
    vault = PrivateSessionVault(tmp_path / "vault")
    trace = _trace()
    writer = vault.begin(PASSWORD)
    writer.save(trace, "recording", force=True)
    snapshot = vault.path / (writer.session_id + ".cbrv")
    original = snapshot.read_bytes()
    try:
        vault.path.chmod(0o755)
        with pytest.raises(VaultError, match="vault_unsafe_directory"):
            vault.list_ids()
        with pytest.raises(VaultError, match="vault_unsafe_directory"):
            writer.save(trace, "completed", force=True)
        assert snapshot.read_bytes() == original
    finally:
        vault.path.chmod(0o700)
    snapshot.chmod(0o644)
    try:
        with pytest.raises(VaultError, match="vault_unsafe_file"):
            vault.open(writer.session_id, PASSWORD)
        with pytest.raises(VaultError, match="vault_unsafe_file"):
            writer.save(trace, "completed", force=True)
        assert snapshot.read_bytes() == original
    finally:
        snapshot.chmod(0o600)
    assert vault.list_ids() == (writer.session_id,)
    assert vault.open(writer.session_id, PASSWORD).recovery_status == "partial"
    trace.destroy()


def test_disk_full_on_encrypted_name_does_not_create_sidecar(
    tmp_path, monkeypatch,
):
    vault = PrivateSessionVault(tmp_path / "vault")
    trace = _trace()
    writer = vault.begin(PASSWORD)
    writer.save(trace, "recording", force=True)
    source = vault.path / (writer.session_id + ".cbrv")
    old_ciphertext = source.read_bytes()
    with monkeypatch.context() as m:
        m.setattr(vault_impl.os, "fsync", _enospc)
        with pytest.raises(VaultError, match="vault_write_failed"):
            vault.set_label(writer.session_id, PASSWORD, "FICTIONAL_LABEL")
    assert not list(vault.path.glob("*.cbrn"))
    assert not list(vault.path.glob(".*.labelpart"))
    assert source.read_bytes() == old_ciphertext
    assert vault.open(writer.session_id, PASSWORD).recovery_status == "partial"
    trace.destroy()
