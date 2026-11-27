"""Core storage exercises a codec without importing Browser Recorder."""
from dataclasses import dataclass
import json

import pytest

from cifereca.core.capture_storage import CaptureVaultStorage, VaultError


@dataclass
class Session:
    payload: str
    session_id: str
    state: str
    saved_at: str = "2026-01-01T00:00:00+00:00"

    @property
    def recovery_status(self):
        return "partial" if self.state == "recording" else self.state


def store(path):
    return CaptureVaultStorage(
        path,
        encode=lambda payload, sid, state: json.dumps(
            [payload, sid, state]).encode(),
        decode=lambda raw, sid: Session(*json.loads(raw)),
        release=lambda session: None,
        summary=lambda session: {"id": session.session_id,
                                 "saved_at": session.saved_at},
        mark_saved=lambda payload, sid: None,
    )


def test_codec_independent_encrypted_restart_and_delete(tmp_path):
    pytest.importorskip("cryptography")
    key = "fictional-test-key-for-core-storage"
    path = tmp_path / "vault"
    writer = store(path).begin(key)
    writer.save("fictional-private-capture", "completed", force=True)
    sid = writer.session_id
    raw = (path / (sid + ".cbrv")).read_bytes()
    assert b"fictional-private-capture" not in raw
    assert path.stat().st_mode & 0o777 == 0o700
    assert (path / (sid + ".cbrv")).stat().st_mode & 0o777 == 0o600
    restarted = store(path)
    assert restarted.open(sid, key).payload == "fictional-private-capture"
    with pytest.raises(VaultError):
        restarted.open(sid, "different-fictional-key")
    restarted.set_label(sid, key, "fictional label")
    assert restarted.get_label(sid, key) == "fictional label"
    with pytest.raises(VaultError):
        restarted.delete(sid, key)
    restarted.delete(sid, key, confirmed=True)
    assert restarted.list_ids() == ()
