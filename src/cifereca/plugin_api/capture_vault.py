"""Transitional capture-engine adapter for the Browser Recorder cutover.

This is not the final broker contract: the path and passphrase interface
must be removed from the production plugin before #8181 is received.
"""
from ..core.capture_vault import (
    EncryptedCaptureVault, RetentionPlan, SessionWriter, VaultError,
    MAGIC, LABEL_MAGIC, MAX_FILE, MAX_LABEL_FILE, MAX_SESSIONS,
    MAX_VAULT_BYTES, MIN_STAGING_AGE_SECONDS, _ID, _STAGE, _STATES,
    _key, _aead,
)
