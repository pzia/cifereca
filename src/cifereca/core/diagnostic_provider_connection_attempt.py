"""Core-only provider connection attempts: bounded two-channel observations (#3101).

The canonical operation_ref is minted at request time, NEVER reconstructed by
timestamp/event_id. scope_ref is an HMAC of the exact *connection* scope with
an ephemeral process secret; it is not a borrower/account identifier or a
permanent ID. No HTTP authentication count is asserted.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any

from .public_provider_diagnostics import (
    canonical_public_provider_id, registered_public_provider_id,
    UNKNOWN_PROVIDER,
)

_PROCESS_SECRET = secrets.token_bytes(32)
_LOG_PREFIX = "EVENT provider connection attempt "
_REF = re.compile(r"[0-9a-f]{24}\Z", re.ASCII)
_SCOPE = re.compile(r"[0-9a-f]{24}\Z", re.ASCII)
_PHASE = {"requested", "connect_invoked", "session_reused", "terminal"}
_STATUS = {"unknown", "success", "failed", "skipped", "cancelled"}
_REASON = {
    "requested", "connect_invoked", "session_reused", "connected",
    "connect_failed", "plugin_missing", "plugin_not_ready",
    "interactive_auth_required", "cancelled", "session_invalid",
}
_CALLER = {
    "startup/connect_configured", "opening_sync", "explicit_refresh",
    "profile_change", "plugin_activation", "other",
}
_SOURCE_KIND = {"configured", "singleton_public", "preview", "virtual"}
_FIELDS = {
    "provider_id", "operation_ref", "phase", "status", "reason_code",
    "caller_category", "source_kind", "network_auth", "duration_ms",
}
_PHASE_REASON = {
    "requested": ({"unknown"}, {"requested"}),
    "connect_invoked": ({"unknown"}, {"connect_invoked"}),
    "session_reused": ({"success"}, {"session_reused"}),
    "terminal": (
        {"success", "failed", "skipped", "cancelled"},
        _REASON - {"requested", "connect_invoked", "session_reused"},
    ),
}


def public_fields(value: object) -> dict[str, Any]:
    """Accept exactly the approved schema, without coercion or private prose."""
    if type(value) is not dict or set(value) not in (_FIELDS, _FIELDS | {"scope_ref", "scope_kind"}):
        return {}
    provider = canonical_public_provider_id(value.get("provider_id"))
    operation = value.get("operation_ref")
    phase = value.get("phase")
    status = value.get("status")
    reason = value.get("reason_code")
    duration = value.get("duration_ms")
    if (provider is None or type(operation) is not str or not _REF.fullmatch(operation)
            or type(phase) is not str or phase not in _PHASE
            or type(status) is not str or type(reason) is not str
            or status not in _PHASE_REASON[phase][0]
            or reason not in _PHASE_REASON[phase][1]
            or type(duration) is not int or not 0 <= duration <= 600000
            or value.get("network_auth") != "unknown"
            or type(value.get("caller_category")) is not str
            or value["caller_category"] not in _CALLER
            or type(value.get("source_kind")) is not str
            or value["source_kind"] not in _SOURCE_KIND):
        return {}
    if phase == "terminal" and (
        (status == "success") != (reason == "connected")
        and not (status == "skipped" and reason in {
            "plugin_missing", "plugin_not_ready", "interactive_auth_required",
        })
        and not (status == "cancelled" and reason == "cancelled")
        and not (status == "failed" and reason in {"connect_failed", "session_invalid"})
    ):
        return {}
    fields = {k: value[k] for k in _FIELDS}
    if "scope_ref" in value:
        if (type(value.get("scope_ref")) is not str
                or not _SCOPE.fullmatch(value["scope_ref"])
                or value.get("scope_kind") != "connection"):
            return {}
        fields["scope_ref"] = value["scope_ref"]
        fields["scope_kind"] = "connection"
    return fields


def log_message(value: object) -> str | None:
    fields = public_fields(value)
    if not fields:
        return None
    return _LOG_PREFIX + json.dumps(
        fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )


def log_fields(body: object) -> dict[str, Any]:
    if (type(body) is not str or len(body) > 850 or not body.startswith(_LOG_PREFIX)):
        return {}
    try:
        fields = public_fields(json.loads(body[len(_LOG_PREFIX):]))
    except (ValueError, TypeError, RecursionError):
        return {}
    return fields if fields and log_message(fields) == body else {}


def _connection_scope(identity: object, provider: str, credential: object) -> str | None:
    # No fallback to current GTK identity, book, borrower or account label.
    if (type(identity) is not str or not 0 < len(identity) <= 256
            or type(credential) is not str or not 0 < len(credential) <= 256
            or provider == UNKNOWN_PROVIDER):
        return None
    payload = json.dumps(
        ["connection-v1", identity, provider, credential],
        ensure_ascii=True, separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(_PROCESS_SECRET, payload, hashlib.sha256).hexdigest()[:24]


@dataclass(frozen=True, repr=False)
class ConnectionAttempt:
    provider_id: str
    operation_ref: str
    caller_category: str
    source_kind: str
    scope_ref: str | None
    started: float

    def emit(self, diagnostics: Any, phase: str, status: str,
             reason_code: str) -> None:
        fields = {
            "provider_id": self.provider_id,
            "operation_ref": self.operation_ref,
            "phase": phase, "status": status, "reason_code": reason_code,
            "caller_category": self.caller_category,
            "source_kind": self.source_kind, "network_auth": "unknown",
            "duration_ms": max(0, min(600000, int((time.monotonic()-self.started)*1000))),
        }
        if self.scope_ref is not None:
            fields["scope_ref"] = self.scope_ref
            fields["scope_kind"] = "connection"
        try:
            publish = getattr(diagnostics, "add_core_provider_connection_attempt", None)
            if callable(publish):
                publish(fields)
        except Exception:
            # Instrumentation cannot change a provider's outcome.
            pass


def new_attempt(registry: Any, provider_id: object, *,
                diagnostics: Any, identity_id: object = None,
                credential_id: object = None, source_kind: str = "configured",
                caller_category: str = "other") -> ConnectionAttempt:
    provider = registered_public_provider_id(registry, provider_id)
    attempt = ConnectionAttempt(
        provider_id=provider, operation_ref=secrets.token_hex(12),
        caller_category=caller_category if caller_category in _CALLER else "other",
        source_kind=source_kind if source_kind in _SOURCE_KIND else "configured",
        scope_ref=_connection_scope(identity_id, provider, credential_id),
        started=time.monotonic(),
    )
    attempt.emit(diagnostics, "requested", "unknown", "requested")
    return attempt
