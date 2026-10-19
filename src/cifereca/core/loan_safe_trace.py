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
    # Provider-side loan descriptors may raise on attribute access or carry
    # str subclasses whose __str__/__getitem__ execute arbitrary plugin code.
    # A missing/untrusted coordinate is a blank *trace* coordinate only:
    # it never supplies evidence of borrower identity or loan rights.
    def coordinate(name: str) -> str:
        try:
            value = getattr(loan, name, None)
        except Exception:
            return ""
        return value if type(value) is str else ""

    coordinates = (
        coordinate("source_provider"),
        coordinate("source_credential"),
        coordinate("source_account_id"),
        coordinate("id"),
        coordinate("record_id"),
        identity_id if type(identity_id) is str else "",
    )
    # Preserve the historical HMAC for all native string coordinates. Six
    # length-prefixed bounded components prevent ambiguous concatenation.
    pieces = []
    for value in coordinates:
        raw = value[:1024].encode("utf-8", "surrogatepass")
        pieces.append(len(raw).to_bytes(2, "big") + raw)
    return hmac.new(
        _PROCESS_KEY, b"".join(pieces), hashlib.sha256,
    ).hexdigest()[:20]


def trace_integer(value: Any, *, minimum: int, maximum: int) -> int | None:
    """Allow only bounded exact integers in runtime TRACE facts (not bool)."""
    return value if type(value) is int and minimum <= value <= maximum else None


def unbound_loan_trace_ref(loan_id: Any) -> str:
    """Compatibility hooks lack owner coordinates: never imply owner proof.

    The reference joins only these loan-ID-only hooks in this process. It is
    not interchangeable with the fully scoped loan_ref in the orchestrator.
    """
    from types import SimpleNamespace
    return loan_trace_ref(SimpleNamespace(id=loan_id), None)


def trace_duration_ms(value: Any) -> int | None:
    """Only finite nonnegative durations up to one day; no coercion."""
    import math
    if type(value) in {int, float} and 0 <= value <= 86400 and math.isfinite(value):
        return int(value * 1000)
    return None
