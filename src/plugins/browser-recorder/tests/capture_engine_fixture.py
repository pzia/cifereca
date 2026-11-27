"""Test-only codec adapter to the internal Core engine.

Fault injection exercises the single production engine without exporting its
filesystem or key interface to any installed plugin. No runtime consumer.
"""
from cifereca.core.capture_vault import (
    EncryptedCaptureVault, VaultError, MAGIC, LABEL_MAGIC, MAX_FILE,
    MAX_LABEL_FILE, MAX_SESSIONS, MAX_VAULT_BYTES, MIN_STAGING_AGE_SECONDS,
)
from browser_recorder_vault import _record, _unrecord
import os
import fcntl
from hashlib import sha256
from time import time

class FixtureVault(EncryptedCaptureVault):
    """Synthetic codec for direct storage-engine fault-injection tests."""

    _encode = staticmethod(_record)
    _decode = staticmethod(_unrecord)

    @staticmethod
    def _release(stored):
        stored.trace.destroy()

    @staticmethod
    def _mark_sealed(trace, session_id):
        trace.vault_session_id = session_id



    # Deterministic fictional key material, for low-level fault tests only.
    @staticmethod
    def _fixture_key(value):
        if type(value) is bytes:
            return value
        return sha256(value.encode()).digest() if type(value) is str and len(value) >= 12 else b""

    def begin(self, value):
        return super().begin(self._fixture_key(value))

    def open(self, sid, value):
        return super().open(sid, self._fixture_key(value))

    def get_label(self, sid, value):
        return super().get_label(sid, self._fixture_key(value))

    def set_label(self, sid, value, label, *, guard=None):
        return super().set_label(sid, self._fixture_key(value), label, guard=guard)

    def delete(self, sid, value, *, confirmed=False):
        return super().delete(sid, self._fixture_key(value), confirmed=confirmed)

    def auto_prune(self, value, **kw):
        return super().auto_prune(self._fixture_key(value), **kw)

    def staging_summary(self):
        # Non-mutating test oracle, not another production retention method.
        if not self.path.exists():
            return (0, 0, 0)
        fd = self._open_dir(create=False)
        try:
            rows = self._staging(fd)
            now = time()
            return (len(rows), sum(info.st_size for _, info in rows),
                    sum(now - info.st_mtime >= MIN_STAGING_AGE_SECONDS for _, info in rows))
        finally:
            os.close(fd)
