"""#4708: private, lossless witness for a future reversible SQLite archive.

Codec only. No SQL writer, permission, recovery command or public report.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

_COLUMNS = (
    "placement_id", "artifact_id", "path", "identity_id", "provider_id",
    "source_item_id", "state", "last_seen_at", "metadata_json",
    "created_at", "updated_at",
)
_SCHEMA = "cifereca.missing-placement.archive.v1"
_MAX_BYTES = 1024 * 1024
_MAX_COORD = 4096


def _refuse():
    raise ValueError("missing_archive_invalid_envelope")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _refuse()
        result[key] = value
    return result


def _text(value, *, required=False):
    if value is None:
        return not required
    return (type(value) is str and len(value) <= _MAX_COORD
            and (not required or bool(value))
            and all(ord(ch) >= 32 and ord(ch) != 127 for ch in value))


def _integer(value, *, nullable=False):
    return ((nullable and value is None) or
            (type(value) is int and -(2 ** 63) <= value < 2 ** 63))


def _validated(row):
    if type(row) is not tuple or len(row) != len(_COLUMNS):
        _refuse()
    pid, artifact, path, identity, provider, source, state, seen, metadata, created, updated = row
    if (not all(_text(v, required=True) for v in (pid, artifact, path, identity))
            or not all(_text(v) for v in (provider, source))
            or state != "missing" or type(state) is not str
            or not _integer(seen, nullable=True)
            or not _integer(created) or not _integer(updated)
            or type(metadata) is not str or len(metadata) > _MAX_BYTES):
        _refuse()
    coordinate = Path(path)
    if (not coordinate.is_absolute() or coordinate == Path("/")
            or ".." in coordinate.parts or str(coordinate) != path):
        _refuse()
    # Do not normalize/re-serialize the original metadata string. Its
    # whitespace, Unicode and field order are part of the exact witness.
    # Reject ambiguous/unreadable JSON rather than inventing replacements.
    try:
        parsed = json.loads(metadata, object_pairs_hook=_pairs,
                            parse_constant=lambda _value: _refuse())
    except (ValueError, TypeError, RecursionError, OverflowError):
        _refuse()
    if type(parsed) is not dict:
        _refuse()
    return row


def _encode_archive_row(row):
    """Return canonical envelope bytes and SHA, not an action token."""
    _validated(row)
    payload = json.dumps(
        {"schema": _SCHEMA, "columns": _COLUMNS, "row": row},
        ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("ascii")
    if len(payload) > _MAX_BYTES:
        _refuse()
    return payload, hashlib.sha256(payload).hexdigest()


def _decode_archive_row(payload, digest):
    """Recover all original SQLite values exactly; no authority implied."""
    if (type(payload) is not bytes or not 0 < len(payload) <= _MAX_BYTES
            or type(digest) is not str
            or hashlib.sha256(payload).hexdigest() != digest):
        _refuse()
    try:
        value = json.loads(payload, object_pairs_hook=_pairs)
        if (type(value) is not dict or set(value) != {"schema", "columns", "row"}
                or value["schema"] != _SCHEMA or value["columns"] != list(_COLUMNS)
                or type(value["row"]) is not list):
            _refuse()
        row = tuple(value["row"])
        rebuilt, _ = _encode_archive_row(row)
        if rebuilt != payload:
            _refuse()
        return row
    except (TypeError, ValueError, RecursionError, OverflowError, UnicodeError):
        _refuse()
