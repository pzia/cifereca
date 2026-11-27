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


