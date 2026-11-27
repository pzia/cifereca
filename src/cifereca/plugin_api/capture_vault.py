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


from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, repr=False)
class CaptureScope:
    """Exact scope, no wildcard matching. Empty fields denote manual capture.

    The instance is the persisted provider connection ID; account and identity
    refer to the exact account known by Core. Capture IDs are Core-generated.
    """
    provider_id: str = ""
    instance_id: str = ""
    account_id: str = ""
    identity_id: str = ""


@dataclass(frozen=True, repr=False)
class CaptureSummary:
    capture_id: str
    scope: CaptureScope
    source_session_ref: str
    state: str
    saved_at: str
    payload_bytes: int
    label: str = ""
    pinned_until: str = ""


@dataclass(frozen=True, repr=False)
class CapturedSession:
    summary: CaptureSummary
    payload: bytes


@runtime_checkable
class CaptureWriter(Protocol):
    @property
    def capture_id(self) -> str: ...
    def save(self, payload: bytes, state: str = "recording", *,
             force: bool = False) -> None: ...


@runtime_checkable
class CaptureVaultBroker(Protocol):
    """Technical captures only; never initiates HTTP or grants provider rights."""
    def begin(self, scope: CaptureScope, source_session_ref: str, *,
              consented: bool = False) -> CaptureWriter: ...
    def sessions(self, scope: CaptureScope) -> tuple[CaptureSummary, ...]: ...
    def read(self, scope: CaptureScope, capture_id: str) -> CapturedSession: ...
    def set_label(self, scope: CaptureScope, capture_id: str, label: str) -> None: ...
    def pin(self, scope: CaptureScope, capture_id: str, *,
            pinned: bool, confirmed: bool = False) -> None: ...
    def delete(self, scope: CaptureScope, capture_id: str, *,
               confirmed: bool = False) -> None: ...


from ..core.capture_export import build_capture_zip, write_capture_zip

from ..core.capture_devkit import core_contract_kit, _PUBLIC_SOURCES as CAPTURE_KIT_PUBLIC_SOURCES
from ..core.capture_devkit_templates import fixture_plugin_files, FIXTURE_MANIFEST


@runtime_checkable
class DiagnosticsCaptureVaultBroker(CaptureVaultBroker, Protocol):
    """Opt-in snapshot of the current HTTP Trace, without extra HTTP activity."""
    def archive_http_trace(self, *, consented: bool = False) -> str: ...
    def export_http_archive(self, capture_id: str, destination: str, *,
                            confirmed: bool = False) -> int: ...
