"""Transitional capture storage adapter for the existing technical plugin.

The final scoped broker will own construction and key acquisition. No new
plugin should instantiate this adapter directly.
"""
from cifereca.core.capture_storage import (
    CaptureVaultStorage, RetentionPlan, SessionWriter, VaultError,
    MAGIC, LABEL_MAGIC, MAX_LABEL_FILE, MAX_FILE, MAX_SESSIONS, MAX_VAULT_BYTES,
    MIN_STAGING_AGE_SECONDS, _ID, _STATES, _key, _aead,
)
