"""Fictional captures: independent service, transparent restart and isolation."""
import os

import pytest

from cifereca.core.capture_vault import VaultError
from cifereca.core.capture_vault_service import CoreCaptureVault, MAX_PAYLOAD
from cifereca.plugin_api.capture_vault import CaptureScope, CaptureVaultBroker

pytest.importorskip("cryptography")
RECORDER = "ui.page.browser-recorder"
DIAGNOSTICS = "ui.page.diagnostics"
SCOPE_A = CaptureScope("fixture", "instance-a", "account-a", "identity-a")
SCOPE_B = CaptureScope("fixture", "instance-b", "account-b", "identity-b")


def service(tmp_path):
    return CoreCaptureVault(tmp_path / "vault", tmp_path / "secrets",
                            authorize_scope=lambda p, s: s in (SCOPE_A, SCOPE_B))


def test_scoped_broker_restart_and_fail_closed_key_loss(tmp_path):
    core = service(tmp_path)
    assert core.for_plugin("provider.fixture") is None
    a = core.for_plugin(RECORDER)
    b = core.for_plugin(DIAGNOSTICS)
    assert isinstance(a, CaptureVaultBroker)
    assert not (tmp_path / "vault").exists()
    assert a.sessions(SCOPE_A) == ()
    with pytest.raises(VaultError, match="vault_confirmation_required"):
        a.begin(SCOPE_A, "fictional-session")
    writer = a.begin(SCOPE_A, "fictional-session", consented=True)
    writer.save(b"fictional private HTTP observation", "completed", force=True)
    sid = writer.capture_id
    key_path = tmp_path / "secrets" / "key"
    key = key_path.read_bytes()
    assert len(key) == 32 and key_path.stat().st_mode & 0o777 == 0o600
    stored_file = tmp_path / "vault" / (sid + ".cbrv")
    assert b"fictional private" not in stored_file.read_bytes()
    assert b"instance-a" not in stored_file.read_bytes()
    assert b.sessions(SCOPE_A) == ()
    assert a.sessions(SCOPE_B) == ()
    for port, scope in ((b, SCOPE_A), (a, SCOPE_B)):
        with pytest.raises(VaultError, match="vault_scope_denied"):
            port.read(scope, sid)
        with pytest.raises(VaultError, match="vault_scope_denied"):
            port.delete(scope, sid, confirmed=True)
    a = service(tmp_path).for_plugin(RECORDER)
    assert a.read(SCOPE_A, sid).payload == b"fictional private HTTP observation"
    assert a.sessions(SCOPE_A)[0].source_session_ref == "fictional-session"
    key_path.unlink()
    with pytest.raises(VaultError, match="vault_key_unavailable"):
        a.read(SCOPE_A, sid)
    with pytest.raises(VaultError, match="vault_key_unavailable"):
        a.begin(SCOPE_A, "another-session", consented=True)
    assert not key_path.exists() and stored_file.exists()


def test_limits_consent_and_key_permissions(tmp_path):
    a = service(tmp_path).for_plugin(RECORDER)
    with pytest.raises(VaultError, match="vault_scope_invalid"):
        a.begin(CaptureScope("fixture"), "session", consented=True)
    writer = a.begin(SCOPE_A, "session", consented=True)
    with pytest.raises(VaultError, match="vault_limit"):
        writer.save(b"x" * (MAX_PAYLOAD + 1), force=True)
    writer.save(b"fictional", "completed", force=True)
    with pytest.raises(VaultError, match="vault_confirmation_required"):
        a.delete(SCOPE_A, writer.capture_id)
    key_path = tmp_path / "secrets" / "key"
    os.chmod(key_path, 0o644)
    with pytest.raises(VaultError, match="vault_key_unavailable"):
        a.read(SCOPE_A, writer.capture_id)
    os.chmod(key_path, 0o600)
    a.delete(SCOPE_A, writer.capture_id, confirmed=True)
    assert a.sessions(SCOPE_A) == ()
