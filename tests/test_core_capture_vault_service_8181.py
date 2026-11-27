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


def test_age_and_pin_retention(tmp_path):
    from datetime import datetime, timedelta, timezone

    core = service(tmp_path)
    port = core.for_plugin(RECORDER)
    first = port.begin(SCOPE_A, "first", consented=True)
    first.save(b"first", "completed", force=True)
    second = port.begin(SCOPE_A, "second", consented=True)
    second.save(b"second", "completed", force=True)
    port.set_label(SCOPE_A, second.capture_id, "fictional label")
    with pytest.raises(VaultError, match="vault_confirmation_required"):
        port.pin(SCOPE_A, second.capture_id, pinned=True)
    port.pin(SCOPE_A, second.capture_id, pinned=True, confirmed=True)
    row = port.read(SCOPE_A, second.capture_id).summary
    assert row.label == "fictional label" and row.pinned_until
    # Short retention, but the explicit pin protects the second session.
    core._policy_source = lambda: (1, 128 * 1024 * 1024)
    now = datetime.now(timezone.utc)
    assert core.maintain(now=now + timedelta(days=2))[0] == 1
    assert [row.capture_id for row in port.sessions(SCOPE_A)] == [second.capture_id]
    assert core.maintain(now=now + timedelta(days=31))[0] == 1
    assert port.sessions(SCOPE_A) == ()
    core.close()


def test_volume_retention(tmp_path):
    core = CoreCaptureVault(tmp_path / "vault", tmp_path / "secrets",
                            authorize_scope=lambda p, s: s == SCOPE_A,
                            policy=lambda: (90, 8 * 1024 * 1024))
    port = core.for_plugin(RECORDER)
    ids = []
    for i in range(4):
        writer = port.begin(SCOPE_A, "fixture-" + str(i), consented=True)
        writer.save(b"x" * (MAX_PAYLOAD - 1), "completed", force=True)
        ids.append(writer.capture_id)
    rows = port.sessions(SCOPE_A)
    assert len(rows) == 2
    assert {row.capture_id for row in rows} == set(ids[-2:])
    core.close()


def test_activation_lease_revokes_reader_and_writer(tmp_path):
    core = service(tmp_path)
    live = True
    port = core.for_activation(RECORDER, current=lambda: live)
    writer = port.begin(SCOPE_A, "fixture", consented=True)
    writer.save(b"first", force=True)
    live = False
    for action in (
        lambda: port.sessions(SCOPE_A),
        lambda: port.read(SCOPE_A, writer.capture_id),
        lambda: port.delete(SCOPE_A, writer.capture_id, confirmed=True),
        lambda: writer.save(b"second", "completed", force=True),
    ):
        with pytest.raises(VaultError, match="vault_activation_expired"):
            action()
    fresh = core.for_activation(RECORDER, current=lambda: True)
    assert fresh.read(SCOPE_A, writer.capture_id).payload == b"first"
    core.close()
    with pytest.raises(VaultError, match="vault_activation_expired"):
        fresh.sessions(SCOPE_A)
