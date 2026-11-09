"""Single stable source identifier encoder for the historical Sync namespace.

This encoding is an opaque *lookup key*, not account ownership, an opt-in
grant or a source lease. Never silently change the primary fallback: existing
SQLite policy keys and their ON/OFF revisions depend on byte-for-byte identity.
"""
from __future__ import annotations

import hashlib


def legacy_source_id(
    identity_id: str, provider_id: str, credential_id: str, account_id: str,
) -> str:
    """Stable sync:SHA256[:24] of exactly four NUL-separated coordinates.

    The upstream account's empty/whitespace identifier is the historical
    provider:credential:primary alias. A *literal* upstream ID equal to that
    alias must be refused separately by the legacy consent collision guard.
    """
    account = account_id.strip() or f"{provider_id}:{credential_id}:primary"
    raw = "\0".join((identity_id, provider_id, credential_id, account))
    return "sync:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
