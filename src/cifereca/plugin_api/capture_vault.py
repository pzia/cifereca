"""Transitional capture-engine adapter for the Browser Recorder cutover.

This is not the final broker contract: the path and passphrase interface
must be removed from the production plugin before #8181 is received.
"""
from ..core.capture_vault import (
    EncryptedCaptureVault, RetentionPlan, SessionWriter, VaultError,
    MAGIC, LABEL_MAGIC, MAX_FILE, MAX_LABEL_FILE, MAX_SESSIONS,
    MAX_VAULT_BYTES, MIN_STAGING_AGE_SECONDS,
    _ID as CAPTURE_SESSION_ID_PATTERN,
    _STAGE as CAPTURE_STAGING_PATTERN,
    _STATES as CAPTURE_SESSION_STATES,
    _key as derive_capture_archive_key,
    _aead as capture_archive_aead,
)
