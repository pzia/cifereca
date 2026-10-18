"""Ephemeral, non-reversible references for automatic-loan TRACE events.

A loan, account, Adobe profile, ACSM URL, path, title or identity must never
be put directly in a structured log. Process-keyed HMAC allows correlation of
bounded local phase events within one invocation without exposing identifiers
or permitting offline dictionary recovery of low-entropy borrower values.
No reference is persisted; a new process starts with a new random key.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

_PROCESS_KEY = secrets.token_bytes(32)


def loan_trace_ref(loan: Any, identity_id: str | None) -> str:
    coordinates = (
        getattr(loan, "source_provider", None),
        getattr(loan, "source_credential", None),
        getattr(loan, "source_account_id", None),
        getattr(loan, "id", None),
        getattr(loan, "record_id", None),
        identity_id,
    )
    # The six length-prefixed components prevent ambiguous concatenation.
    # Individual 1024-character bounds make this cheap even for malformed
    # provider objects; never expose or repr() original values.
    pieces = []
    for value in coordinates:
        raw = str(value if isinstance(value, str) else "")[:1024].encode(
            "utf-8", "surrogatepass",
        )
        pieces.append(len(raw).to_bytes(2, "big") + raw)
    return hmac.new(
        _PROCESS_KEY, b"".join(pieces), hashlib.sha256,
    ).hexdigest()[:20]
