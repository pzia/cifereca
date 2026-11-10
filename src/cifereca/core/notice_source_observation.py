"""#2385/#4552: inert Core admission of bounded, raw parsed source notices.

Accepts an already-produced *parsed metadata object*, not HTTP body/DOM or
signed URL; keys and descriptive values retain the upstream parser's original
spelling/structure. The Core never normalizes Collection->Series, infers
age/language, merges editions or creates a right by observing a source.

No persistent storage, plugin execution, GET, reconciliation or acquisition.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
import re
from typing import Any
from urllib.parse import urlsplit, parse_qsl

_MAX_BYTES = 65536
_MAX_VALUE_BYTES = 8192
_MAX_KEY_BYTES = 128
_MAX_NODES = 4096
_MAX_DEPTH = 8
_MAX_FIELDS = 256
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}\Z")
_RESTRICTED_FIELD = re.compile(
    r"(?:access|refresh|auth|bearer|csrf|session|client|api)?"
    r"(?:token|secret|password|passwd|credential|cookie|authorization|signature|signedurl|apikey)",
    re.I,
)
# Compare normalized query *names*, never raw spellings: provider URLs may use
# hyphens, underscores, periods or percent-encoding for the same secret field.
# Signed AWS request fields are all authority-bearing even when their value is
# an algorithm, timestamp, credential scope or signed header list.
_RESTRICTED_QUERY_KEYS = frozenset((
    "token", "accesstoken", "refreshtoken", "securitytoken",
    "signature", "sig", "credential", "credentials", "secret",
    "key", "apikey", "auth", "authorization", "session",
    "sessionid", "sessionkey", "expires", "policy",
    "awsaccesskeyid", "googleaccessid", "jwt", "code",
))


def _restricted_query_key(value: str) -> bool:
    normalized = "".join(ch for ch in value.casefold() if ch.isalnum())
    return normalized in _RESTRICTED_QUERY_KEYS or normalized.startswith("xamz")


@dataclass(frozen=True, slots=True, repr=False)
class NoticeSourceKey:
    provider_id: str
    visibility: str  # public | private | embedded
    identity_id: str | None
    credential_id: str | None
    account_id: str | None
    branch_id: str
    record_id: str
    edition_id: str
    manifestation_role: str  # print | digital | other | unknown


@dataclass(frozen=True, slots=True, repr=False)
class NoticeSourceObservation:
    key: NoticeSourceKey
    parser_id: str
    parser_version: str
    observed_at: int
    status: str  # complete | partial ; partial is NEVER a tombstone
    source_revision: str
    raw_json: str = field(repr=False)

    def fields_copy(self) -> dict[str, Any]:
        """Return a new descriptive object, never a mutable internal alias."""
        return json.loads(self.raw_json)


def _id(value: object, *, required: bool = True) -> bool:
    return (value is None and not required) or (
        type(value) is str and _ID.fullmatch(value) is not None
    )


def _valid_key(key: object) -> bool:
    if type(key) is not NoticeSourceKey:
        return False
    if (not _id(key.provider_id) or not _id(key.branch_id)
            or not _id(key.record_id) or not _id(key.edition_id)
            or key.visibility not in ("public", "private", "embedded")
            or key.manifestation_role not in (
                "print", "digital", "other", "unknown"
            )):
        return False
    if key.visibility == "public":
        return (key.identity_id is None and key.credential_id is None
                and key.account_id is None)
    if key.visibility == "private":
        return (_id(key.identity_id) and _id(key.credential_id)
                and _id(key.account_id, required=False))
    # Embedded metadata belong to the physical copy/profile rather than
    # a supplier credential. The record ID is an attested artifact coordinate.
    return (_id(key.identity_id) and key.credential_id is None
            and key.account_id is None
            and re.fullmatch(r"sha256:[0-9a-f]{64}", key.record_id) is not None)


def _safe_scalar(text: str) -> bool:
    try:
        encoded = text.encode("utf-8")
    except UnicodeError:
        return False
    if len(encoded) > _MAX_VALUE_BYTES or "\x00" in text:
        return False
    # URLs are scheme-insensitive and may be whitespace-prefixed or
    # protocol-relative. Keep the raw descriptive text unchanged, but never
    # preserve a signed URL merely because its scheme was capitalized.
    candidate = text.lstrip()
    if candidate.casefold().startswith(("https://", "http://", "//")):
        try:
            source_url = ("https:" + candidate if candidate.startswith("//")
                          else candidate)
            u = urlsplit(source_url)
            if (not u.hostname or u.username is not None or u.password is not None
                    or u.fragment or
                    any(_restricted_query_key(k)
                        for k, _ in parse_qsl(u.query, keep_blank_values=True))):
                return False
        except ValueError:
            return False
    return True


def _validate_fields(value: object) -> bool:
    """Iterative depth/size/secret checks; no normalization or key rewriting."""
    if type(value) is not dict or len(value) > _MAX_FIELDS:
        return False
    seen = 0
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        seen += 1
        if seen > _MAX_NODES or depth > _MAX_DEPTH:
            return False
        if type(item) is dict:
            if len(item) > _MAX_FIELDS:
                return False
            for key, child in item.items():
                if (type(key) is not str or not _safe_scalar(key)
                        or len(key.encode("utf-8")) > _MAX_KEY_BYTES
                        or _RESTRICTED_FIELD.search(
                            re.sub(r"[^a-z0-9]", "", key.casefold())
                        )):
                    return False
                stack.append((child, depth + 1))
        elif type(item) is list:
            if len(item) > _MAX_NODES:
                return False
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            if not _safe_scalar(item):
                return False
        elif item is None or type(item) in (bool, int):
            if type(item) is int and (item < -2**63 or item > 2**63 - 1):
                return False
        elif type(item) is float:
            if not math.isfinite(item):
                return False
        else:
            return False
    return True


def admit_source_notice(
    *, key: NoticeSourceKey, parser_id: str,
    parser_version: str, observed_at: int,
    raw_fields: object, status: str = "complete",
) -> NoticeSourceObservation:
    """Snapshot a typed source notice, preserving raw JSON descriptive fields.

    A producer must have separately attested identity/session/record and
    parser provenance. This bounded acceptance only proves serializability
    and scope structure. A partial page never deletes a previously good row.
    """
    if (not _valid_key(key) or not _id(parser_id)
            or not _id(parser_version)
            or type(observed_at) is not int or observed_at < 1
            or type(status) is not str or status not in ("complete", "partial")
            or not _validate_fields(raw_fields)):
        raise ValueError("invalid_source_notice_observation")
    try:
        raw = json.dumps(
            raw_fields, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        if len(raw.encode("utf-8")) > _MAX_BYTES:
            raise ValueError
        coordinates = (
            key.provider_id, key.visibility, key.identity_id,
            key.credential_id, key.account_id, key.branch_id,
            key.record_id, key.edition_id, key.manifestation_role,
            parser_id, parser_version, status,
        )
        payload = json.dumps(
            (coordinates, raw), ensure_ascii=True, separators=(",", ":"),
        )
        digest = sha256(payload.encode("ascii")).hexdigest()
    except (TypeError, ValueError, UnicodeError, OverflowError) as exc:
        raise ValueError("invalid_source_notice_serialization") from exc
    return NoticeSourceObservation(
        key=key, parser_id=parser_id, parser_version=parser_version,
        observed_at=observed_at, status=status,
        source_revision="notice-raw.v1:" + digest, raw_json=raw,
    )


__all__ = [
    "NoticeSourceKey", "NoticeSourceObservation", "admit_source_notice",
]
