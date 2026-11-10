"""Read-only access to archived Kobo W2 checkpoint metadata.

Archived checkpoints are never evidence of supplier rights, canonical
acquisition, or authorization to materialize a file.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _kobo_checkpoint_key(identity_id: str, credential_id: str) -> str:
    digest = hashlib.sha256(
        (identity_id + "\\x00" + credential_id).encode("utf-8")
    ).hexdigest()[:24]
    return "kobo_backup_checkpoint." + digest


def _kobo_checkpoint_for(self, identity_id: str, credential_id: str) -> dict[str, Any] | None:
    """One established v1 checkpoint contract, no second checkpoint store."""
    try:
        row = json.loads(
            self.storage.metadata_get(_kobo_checkpoint_key(identity_id, credential_id)) or "null"
        )
    except (TypeError, ValueError):
        return None
    if not isinstance(row, dict) or row.get("schema_version") != 1:
        return None
    return row
