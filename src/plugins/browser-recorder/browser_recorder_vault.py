"""Browser Recorder trace codec and adapter to the common capture engine.

Capture policy and typed validation remain local to the consumer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from uuid import uuid4
from cifereca.plugin_api.capture_vault import CaptureScope
import json
# Existing fault-injection tests exercise the shared OS/fcntl modules.
import os
import fcntl

from cifereca.plugin_api.capture_vault import (
    EncryptedCaptureVault, RetentionPlan, SessionWriter, VaultError,
    MAGIC, LABEL_MAGIC, MAX_FILE, MAX_LABEL_FILE, MAX_SESSIONS,
    MAX_VAULT_BYTES, MIN_STAGING_AGE_SECONDS,
    CAPTURE_SESSION_ID_PATTERN as _ID,
    CAPTURE_STAGING_PATTERN as _STAGE,
    CAPTURE_SESSION_STATES as _STATES,
    derive_capture_archive_key as _key,
    capture_archive_aead as _aead,
)
from browser_recorder_http import (
    MAX_BODY_BYTES, MAX_EXCHANGES, MAX_TOTAL_BODY_BYTES,
    PrivateExchange, PrivateHttpTrace, _BODY_REASONS,
    _AUTH_SCHEMA_REASONS, _valid_auth_schema, _auth_flow, _url_origin,
)

SCHEMA = "cifereca.browser-recorder.private-session/1"
_FIELD_NAMES = frozenset(field.name for field in fields(PrivateExchange))

def _validated_auth_structure_row(row: dict, allowed_origins: tuple[str, ...]) -> bool:
    """Validate optional version-1, values-free schema at both storage edges."""
    schema_keys = ("auth_request_schema", "auth_response_schema")
    state_keys = ("auth_request_schema_state", "auth_response_schema_state")
    for direction, schema_key, state_key in (
        ("request", schema_keys[0], state_keys[0]),
        ("response", schema_keys[1], state_keys[1]),
    ):
        schema = row.get(schema_key)
        state = row.get(state_key, "not_applicable")
        if (type(state) is not str or state not in _AUTH_SCHEMA_REASONS
                or not _valid_auth_schema(schema, direction=direction)
                or (state == "schema_captured") != (schema is not None)):
            return False
        if schema is not None and (
            row.get("metadata_only") is not False
            or _url_origin(row.get("url")) not in allowed_origins
            or not str(row.get("url", "")).startswith("https://")
            or not _auth_flow(row.get("url"))
            or (direction == "request" and (
                row.get("method") != "POST"
                or row.get("request_body") is not None
                or row.get("request_body_reason") != "auth_redacted"
            ))
            or (direction == "response" and (
                row.get("response_body") is not None
                or row.get("response_body_reason") != "auth_redacted"
                or row.get("response_body_state") != "body_omitted"
            ))
        ):
            return False
    return True


def _record(trace: PrivateHttpTrace, session_id: str, state: str) -> bytes:
    if state not in _STATES or not _ID.fullmatch(session_id):
        raise VaultError("vault_invalid")
    if len(trace.exchanges) > MAX_EXCHANGES:
        raise VaultError("vault_limit")
    entries = [asdict(e) for e in trace.exchanges]
    for row in entries:
        if not _validated_auth_structure_row(row, trace.origins):
            raise VaultError("vault_invalid")
    data = {
        "schema": SCHEMA,
        "id": session_id,
        "state": state,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "end_reason": trace.end_reason if (
            state == "completed" and trace.end_reason in (
                "button", "browser_closed",
            )
        ) else None,
        "origins": list(trace.origins),
        "metadata_only_origins": list(trace.metadata_only_origins),
        "metadata_only_counts": dict(trace._metadata_origin_counts),
        "content_negotiation": trace.content_negotiation,
        "compressed_responses_accepted": trace.allow_compressed_responses,
        "limit": trace.limit,
        "max_body": trace.max_body,
        "total_body_remaining": trace.total_body,
        "omitted": dict(trace.omitted),
        "exchanges": entries,
    }
    try:
        data_bytes = json.dumps(
            data, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise VaultError("vault_invalid") from exc
    if len(data_bytes) > MAX_FILE - 128:
        raise VaultError("vault_limit")
    return data_bytes


def _unrecord(blob: bytes, session_id: str):
    try:
        data = json.loads(blob)
        if (type(data) is not dict or data["schema"] != SCHEMA
                or data["id"] != session_id or data["state"] not in _STATES):
            raise ValueError("invalid schema")
        origins, third = data["origins"], data["metadata_only_origins"]
        if (type(origins) is not list or type(third) is not list
                or not origins or len(origins) > 6 or len(third) > 4):
            raise ValueError("invalid origins")
        trace = PrivateHttpTrace(
            tuple(origins), metadata_only_origins=tuple(third),
            limit=data["limit"], max_body=data["max_body"],
            total_body=data["total_body_remaining"],
        )
        # v1 archives without this optional counter remain readable.
        counts = data.get("metadata_only_counts", {})
        if (type(counts) is not dict
                or set(counts) - set(trace.metadata_only_origins)
                or any(type(count) is not int or not 0 <= count <= MAX_EXCHANGES
                       for count in counts.values())):
            raise ValueError("invalid per-origin coverage")
        trace._metadata_origin_counts.update(counts)
        rows = data["exchanges"]
        strategy = data.get("content_negotiation", "browser_default")
        if type(strategy) is not str or strategy not in {"browser_default", "identity_requested"}:
            raise ValueError("invalid capture strategy")
        trace.content_negotiation = strategy
        accepted = data.get("compressed_responses_accepted", False)
        if type(accepted) is not bool:
            raise ValueError("invalid capture strategy")
        trace.allow_compressed_responses = accepted
        if type(rows) is not list or len(rows) > MAX_EXCHANGES:
            raise ValueError("invalid entries")
        if sum(counts.values()) > len(rows):
            raise ValueError("invalid per-origin coverage")
        for index, row in enumerate(rows):
            # Old CFRVLT01 sessions predate per-direction reason codes.
            # Accept only those exact legacy fields, never arbitrary data.
            schema_fields = {
                "auth_request_schema", "auth_response_schema",
                "auth_request_schema_state", "auth_response_schema_state",
            }
            old_fields = _FIELD_NAMES - schema_fields
            without_reasons = {"request_body_reason", "response_body_reason"}
            legacy_fields = old_fields - without_reasons
            new_legacy_fields = _FIELD_NAMES - without_reasons
            if type(row) is not dict or set(row) not in (
                _FIELD_NAMES, new_legacy_fields, old_fields, legacy_fields,
            ):
                raise ValueError("invalid entry")
            if not _validated_auth_structure_row(row, trace.origins):
                raise ValueError("invalid auth structure")
            if any(
                type(row[key]) is not str or row[key] not in _BODY_REASONS
                for key in ("request_body_reason", "response_body_reason")
                if key in row
            ):
                raise ValueError("invalid body reason")
            if (type(row["sequence"]) is not int or row["sequence"] != index + 1
                    or type(row["url"]) is not str or len(row["url"]) > 8192
                    or type(row["method"]) is not str
                    or row["method"] not in {
                        "GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH",
                        "DELETE", "OTHER",
                    }
                    or any(type(row[key]) is not int or row[key] < 0 for key in (
                        "status", "started_ms", "duration_ms", "page_id",
                    ))
                    or row["status"] > 599
                    or type(row["metadata_only"]) is not bool
                    or type(row["network_error"]) is not bool):
                raise ValueError("invalid entry values")
            for key in ("request_body", "response_body", "redirect_to"):
                if row[key] is not None and (
                    type(row[key]) is not str or len(row[key]) > MAX_BODY_BYTES * 2
                ):
                    raise ValueError("invalid body")
            for key in ("request_headers", "response_headers"):
                if type(row[key]) is not dict or len(row[key]) > 25 or any(
                    type(k) is not str or type(v) is not str
                    or len(k) > 80 or len(v) > 256 for k, v in row[key].items()
                ):
                    raise ValueError("invalid headers")
            trace.exchanges.append(PrivateExchange(**row))
        omitted = data["omitted"]
        if type(omitted) is not dict or any(
            k not in trace.omitted or type(v) is not int or v < 0
            for k, v in omitted.items()
        ):
            raise ValueError("invalid omissions")
        trace.omitted.update(omitted)
        trace.end_reason = data.get("end_reason")
        trace.vault_session_id = session_id
        return StoredSession(trace, data["state"], data["saved_at"], session_id)
    except (TypeError, ValueError, KeyError, OverflowError) as exc:
        raise VaultError("vault_invalid") from exc
    except VaultError:
        raise
    except Exception as exc:
        # Invalid archived contents cannot escape through UI/Diagnostics.
        raise VaultError("vault_invalid") from exc


@dataclass(repr=False)
class StoredSession:
    trace: PrivateHttpTrace
    state: str
    saved_at: str
    session_id: str

    @property
    def recovery_status(self) -> str:
        """Status after opening from disk; never claim a live recorder.

        Legacy 'recording' snapshots mean the app never wrote a final
        completion marker. The ciphertext is preserved as-is for forensics.
        """
        return "partial" if self.state == "recording" else self.state

    def private_summary(self) -> dict[str, object]:
        """Visible *only after explicit user unlock*; never for Diagnostics.

        saved_at is the timestamp of the LAST durable snapshot, not the
        beginning of the browsing session.
        """
        return {
            "id": self.session_id,
            "saved_at": self.saved_at,
            "status": self.recovery_status,
            "origin": self.trace.origins[0] if self.trace.origins else "",
            "exchanges": len(self.trace.exchanges),
            "omissions": dict(self.trace.omitted),
        }

    def __repr__(self):
        return (f"StoredSession(state={self.recovery_status!r}, "
                f"exchanges={len(self.trace.exchanges)})")



class PrivateSessionVault(EncryptedCaptureVault):
    """Browser Recorder codec adapter; no storage or cryptography here."""

    _encode = staticmethod(_record)
    _decode = staticmethod(_unrecord)

    @staticmethod
    def _release(stored):
        stored.trace.destroy()

    @staticmethod
    def _mark_sealed(trace, session_id):
        trace.vault_session_id = session_id


class RecorderVault:
    """Typed consumer of the Core broker. No path, key or storage engine."""

    def __init__(self, port):
        self._port = port
        self._scope = CaptureScope()

    def begin(self, *, consented=False):
        writer = self._port.begin(self._scope, uuid4().hex, consented=consented)
        return _RecorderWriter(writer)

    def sessions(self):
        return self._port.sessions(self._scope)

    def read(self, session_id):
        row = self._port.read(self._scope, session_id)
        stored = _unrecord(row.payload, session_id)
        if stored.state != row.summary.state:
            stored.trace.destroy()
            raise VaultError("vault_invalid")
        stored.saved_at = row.summary.saved_at
        return stored

    def set_label(self, session_id, label):
        self._port.set_label(self._scope, session_id, label)

    def pin(self, session_id, *, pinned, confirmed=False):
        self._port.pin(self._scope, session_id, pinned=pinned, confirmed=confirmed)

    def delete(self, session_id, *, confirmed=False):
        self._port.delete(self._scope, session_id, confirmed=confirmed)


class _RecorderWriter:
    def __init__(self, writer):
        self._writer = writer
        self.session_id = writer.capture_id

    def save(self, trace, state="recording", *, force=False):
        self._writer.save(_record(trace, self.session_id, state), state, force=force)
        if state != "recording":
            trace.vault_session_id = self.session_id
