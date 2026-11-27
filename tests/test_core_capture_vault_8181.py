"""Storage engine independent of any installed technical plugin."""
from dataclasses import dataclass
import json

import pytest

from cifereca.core.capture_vault import EncryptedCaptureVault, VaultError


@dataclass
class Session:
    payload: str
    session_id: str
    state: str
    saved_at: str = "2026-01-01T00:00:00+00:00"

    @property
    def recovery_status(self):
        return "partial" if self.state == "recording" else self.state

    def private_summary(self):
        return {"id": self.session_id, "saved_at": self.saved_at}


class FixtureVault(EncryptedCaptureVault):
    @staticmethod
    def _encode(payload, sid, state):
        return json.dumps([payload, sid, state]).encode()

    @staticmethod
    def _decode(raw, sid):
        session = Session(*json.loads(raw))
        if session.session_id != sid:
            raise VaultError("vault_invalid")
        return session

    @staticmethod
    def _release(session):
        pass

    @staticmethod
    def _mark_sealed(payload, sid):
        pass


def test_codec_independent_encrypted_restart_and_delete(tmp_path):
    pytest.importorskip("cryptography")
    key = b"x" * 32
    path = tmp_path / "vault"
    writer = FixtureVault(path).begin(key)
    writer.save("fictional-private-capture", "completed", force=True)
    sid = writer.session_id
    raw = (path / (sid + ".cbrv")).read_bytes()
    assert b"fictional-private-capture" not in raw
    assert path.stat().st_mode & 0o777 == 0o700
    assert (path / (sid + ".cbrv")).stat().st_mode & 0o777 == 0o600
    restarted = FixtureVault(path)
    assert restarted.open(sid, key).payload == "fictional-private-capture"
    with pytest.raises(VaultError):
        restarted.open(sid, b"y" * 32)
    restarted.set_label(sid, key, "fictional label")
    assert restarted.get_label(sid, key) == "fictional label"
    with pytest.raises(VaultError):
        restarted.delete(sid, key)
    restarted.delete(sid, key, confirmed=True)
    assert restarted.list_ids() == ()
