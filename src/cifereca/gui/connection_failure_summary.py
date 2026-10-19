"""Safe, bounded, user-facing provider connection failures (#2370).

Consumes the Core's already-redacted projection, never private keys from
connect_configured.errors, account labels or untrusted plugin exceptions.
"""
from __future__ import annotations

from ..core.public_provider_diagnostics import (
    CONNECT_FAILURE_CODE, MISSING_PROVIDER_CODE, UNKNOWN_PROVIDER,
    canonical_public_provider_id,
)


def connection_failure_summary(projection: object, *, max_sources: int = 3) -> str:
    """Render only validated public provider slugs and closed failure codes."""
    if type(projection) is not dict:
        return ""
    records = projection.get("items")
    if type(records) not in (tuple, list):
        return ""
    max_sources = max(1, min(5, int(max_sources)))
    unique: dict[tuple[str, str], int] = {}
    for row in records[:40]:
        if type(row) is not dict:
            continue
        source, code = row.get("provider_id"), row.get("code")
        if source != UNKNOWN_PROVIDER and canonical_public_provider_id(source) is None:
            continue
        if type(code) is not str or code not in {
            CONNECT_FAILURE_CODE, MISSING_PROVIDER_CODE,
        }:
            continue
        key = (source, code)
        unique[key] = unique.get(key, 0) + 1
    if not unique:
        return ""
    parts = []
    for (source, code), count in list(unique.items())[:max_sources]:
        count_text = f" ×{count}" if count > 1 else ""
        parts.append(f"{source}{count_text} [{code}]")
    extra = sum(unique.values()) - sum(list(unique.values())[:max_sources])
    truncated = projection.get("truncated")
    if type(truncated) is int and 0 < truncated <= 10000:
        extra += truncated
    if extra:
        parts.append(f"{extra} autre(s)")
    return "Sources en échec : " + ", ".join(parts)


__all__ = ["connection_failure_summary"]
