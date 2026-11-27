"""Explicit local diagnostic ZIP of what the collector actually retained.

Unlike the reviewed LLM ZIP, this deliberately contains *private* values
(queries, original text, technical headers) and MUST NEVER be shared as if
anonymized. The source is encrypted at rest; a clear ZIP is written only after Save As.
Private payload and provider credentials are never logged.
"""
from __future__ import annotations

import json
from pathlib import Path
from cifereca.plugin_api.capture_vault import build_capture_zip, write_capture_zip

from browser_recorder import RecorderError
from browser_recorder_devkit import core_contract_kit
from browser_recorder_vault import (
    VaultError, StoredSession, _record, MAX_FILE,
)

README = (
    "PRIVATE CIFERECA BROWSER RECORDER DIAGNOSTIC\n\n"
    "Contains original observed text and URLs. DO NOT share without review.\n"
    "Not an anonymized LLM ZIP, not a full browser HAR/profile or session.\n"
    "Collector omitted Authorization, Cookie, Set-Cookie, binary and out-of-scope traffic.\n"
    "trace.json is the versioned, authenticated source observation snapshot.\n"
    "Read plugin-dev-kit/PROVIDER-DEVELOPMENT.md before implementing a provider.\n"
    "plugin-dev-kit/manifest.json inventories the offline SDK by SHA-256.\n"
)


def _zip_bytes(session: StoredSession) -> bytes:
    if type(session) is not StoredSession:
        raise VaultError("private_zip_invalid")
    if (getattr(session.trace, "vault_session_id", None)
            != session.session_id):
        raise VaultError("private_zip_invalid")
    snapshot = json.loads(_record(
        session.trace, session.session_id, session.state,
    ))
    # Preserve the original vault snapshot timestamp; ZIP creation time is
    # not evidence that the website was visited again.
    snapshot["saved_at"] = session.saved_at
    source = json.dumps(
        snapshot, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    # Both explicit LLM export modes get the same real, offline Core kit.
    # No capture contents are passed to its builder. The unreviewed ZIP keeps
    # private trace.json separate and does not claim anonymization.
    try:
        kit = core_contract_kit()
    except RecorderError as exc:
        raise VaultError("private_zip_kit_unavailable") from exc
    members = {"README.txt": README.encode("utf-8"), "trace.json": source}
    if any(name in members or not name.startswith("plugin-dev-kit/")
           for name in kit):
        raise VaultError("private_zip_kit_unavailable")
    members.update(kit)
    return build_capture_zip(members, max_bytes=MAX_FILE)


def save_session_llm_zip(
    session: StoredSession, destination: Path, *, confirmed=False,
) -> int:
    """Write one explicitly selected *cleartext* session ZIP for a user's LLM.

    Vault remains AES-GCM encrypted at rest; the plain ZIP exists only after
    a Save As confirmation. The destination may be the user's ordinary
    Documents/Downloads folder (no 0700 prerequisite). Create it as 0600,
    exclusively and without following symlinks; never overwrite. No upload.
    """
    if confirmed is not True:
        raise VaultError("private_zip_confirmation_required")
    destination = Path(destination)
    if not destination.is_absolute() or destination.suffix.lower() != ".zip":
        raise VaultError("private_zip_destination")
    # Validate and bound the snapshot before opening the destination.
    payload = _zip_bytes(session)
    return write_capture_zip(destination, payload, confirmed=confirmed, max_bytes=MAX_FILE)


