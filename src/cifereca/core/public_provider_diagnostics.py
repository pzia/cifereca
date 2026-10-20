"""Core-only public projection for provider-connection failures (#2370).

A configured credential/key/label is never a provider identifier. Only the
canonical id of an *installed, registered* provider may leave Core, and only
if the id follows a restrictive plugin-slug grammar. This is a disclosure
filter, not a plugin trust or source-health/online-search guarantee.
"""
from __future__ import annotations

import json
import re
from typing import Any

UNKNOWN_PROVIDER = "source_inconnue"
CONNECT_FAILURE_CODE = "provider_connect_failed"
MISSING_PROVIDER_CODE = "provider_not_registered"
_MAX_FAILURE_DETAILS = 40
_PUBLIC_ID = re.compile(r"[a-z][a-z0-9]{0,31}(?:[._-][a-z][a-z0-9]{0,31}){0,5}\Z", re.ASCII)
_PRIVATE_COMPONENTS = frozenset({
    "secret", "token", "password", "cookie", "credential", "credentials",
    "account", "identity", "barcode", "session", "authorization", "bearer",
})


def canonical_public_provider_id(value: object) -> str | None:
    """Validate a *canonical* provider id, never stringify input or sanitize it."""
    if type(value) is not str or len(value) > 64 or not _PUBLIC_ID.fullmatch(value):
        return None
    if any(segment in _PRIVATE_COMPONENTS for segment in re.split(r"[._-]", value)):
        return None
    return value


def registered_public_provider_id(registry: Any, value: object) -> str:
    """Only a Core-registered provider's exact canonical slug is publishable."""
    slug = canonical_public_provider_id(value)
    if slug is None:
        return UNKNOWN_PROVIDER
    try:
        plugin = registry.maybe_get(slug)
        known_id = plugin.descriptor.id if plugin is not None else None
    except Exception:
        return UNKNOWN_PROVIDER
    return slug if type(known_id) is str and known_id == slug else UNKNOWN_PROVIDER


def public_connection_failure(
    registry: Any, provider_id: object, *, code: str = CONNECT_FAILURE_CODE,
) -> dict[str, str]:
    """One public outcome per connection attempt, even for duplicate providers."""
    return {
        "provider_id": registered_public_provider_id(registry, provider_id),
        "code": code if code in {CONNECT_FAILURE_CODE, MISSING_PROVIDER_CODE}
        else CONNECT_FAILURE_CODE,
    }



# Only an already Core-attested connection failure may be emitted in this
# format. The log reader validates the entire canonical message, not arbitrary
# key=value tokens from a provider or plugin logger.
_CONNECTION_TRACE_PREFIX = "TRACE provider connection failure "


def public_connection_fields(value: object) -> dict[str, str]:
    """Closed shared projection of one connection failure, no account scope."""
    if type(value) is not dict or set(value) != {"provider_id", "code"}:
        return {}
    ident = canonical_public_provider_id(value["provider_id"])
    code = value["code"]
    if ident is None or type(code) is not str or code not in {
        CONNECT_FAILURE_CODE, MISSING_PROVIDER_CODE,
    }:
        return {}
    return {"provider_id": ident, "code": code}


def public_connection_log_message(fields: dict[str, str]) -> str:
    """Only a validated, canonical Core projection reaches this serializer."""
    return _CONNECTION_TRACE_PREFIX + json.dumps(
        fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )


def public_connection_log_fields(body: object) -> dict[str, str]:
    """Refuse noncanonical, extended or hostile log lines rather than guess."""
    if (type(body) is not str or len(body) > 512
            or not body.startswith(_CONNECTION_TRACE_PREFIX)):
        return {}
    try:
        fields = public_connection_fields(json.loads(body[len(_CONNECTION_TRACE_PREFIX):]))
    except (ValueError, TypeError, RecursionError):
        return {}
    return fields if fields and public_connection_log_message(fields) == body else {}

def bounded_connection_failures(rows: object) -> dict[str, object]:
    """Explicitly expose truncation, never duplicate private keys/accounts."""
    if type(rows) not in (list, tuple):
        return {"items": (), "truncated": 0}
    safe = []
    for row in rows[:_MAX_FAILURE_DETAILS]:
        if type(row) is not dict:
            continue
        ident = canonical_public_provider_id(row.get("provider_id"))
        code = row.get("code")
        if ident is None or code not in {CONNECT_FAILURE_CODE, MISSING_PROVIDER_CODE}:
            continue
        safe.append({"provider_id": ident, "code": code})
    return {"items": tuple(safe), "truncated": max(0, len(rows) - _MAX_FAILURE_DETAILS)}


__all__ = [
    "UNKNOWN_PROVIDER", "CONNECT_FAILURE_CODE", "MISSING_PROVIDER_CODE",
    "canonical_public_provider_id", "registered_public_provider_id",
    "public_connection_failure", "bounded_connection_failures",
    "public_connection_fields", "public_connection_log_message",
    "public_connection_log_fields",
]
