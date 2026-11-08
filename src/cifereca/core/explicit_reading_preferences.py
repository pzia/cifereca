"""Core-owned, explicitly confirmed reading preferences (P1 #4681).

No implicit EPUB/provider inference, background collection, or plugin access.
The preference map lives in the existing durable SQLite metadata table under
one identity-derived opaque key; there is no migration of legacy rights.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any

_MAX_ENTRIES = 256
_MAX_BYTES = 131072
_MAX_REVISION = 2**63 - 1
_ALLOWED = frozenset({"liked", "not_interested"})


@dataclass(frozen=True)
class ReadingPreferenceSnapshot:
    revision: int
    preferences: tuple[tuple[str, str], ...]


def _identity_key(identity_id: str) -> str:
    if (type(identity_id) is not str or not 0 < len(identity_id) <= 160
            or not identity_id.isprintable()):
        raise ValueError("reading_preference_identity_invalid")
    digest = sha256(
        b"cifereca:reading-preference:v1\x00" + identity_id.encode("utf-8")
    ).hexdigest()
    return "reading.preference.v1:" + digest


def _library_key(library_key: str) -> str:
    if (type(library_key) is not str or not 0 < len(library_key) <= 256
            or library_key != library_key.strip()
            or not library_key.isprintable()):
        raise ValueError("reading_preference_target_invalid")
    return library_key


def _decode(value: Any) -> tuple[int, dict[str, str]]:
    if value is None:
        return 0, {}
    if type(value) is not str or len(value.encode("utf-8")) > _MAX_BYTES:
        raise ValueError("reading_preference_state_invalid")
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        # json.loads normally keeps the *last* occurrence of a key. A
        # duplicated user preference or schema/revision is contradictory
        # evidence and must never silently overwrite an earlier choice.
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("reading_preference_state_invalid")
            result[key] = item
        return result

    try:
        payload = json.loads(value, object_pairs_hook=unique_object)
    except (TypeError, ValueError):
        raise ValueError("reading_preference_state_invalid") from None
    if (type(payload) is not dict or set(payload) != {"schema", "revision", "preferences"}
            or type(payload["schema"]) is not int or payload["schema"] != 1
            or type(payload["revision"]) is not int
            or not 1 <= payload["revision"] <= _MAX_REVISION
            or type(payload["preferences"]) is not dict
            or len(payload["preferences"]) > _MAX_ENTRIES):
        raise ValueError("reading_preference_state_invalid")
    for key, preference in payload["preferences"].items():
        try:
            _library_key(key)
        except ValueError:
            raise ValueError("reading_preference_state_invalid") from None
        if type(preference) is not str or preference not in _ALLOWED:
            raise ValueError("reading_preference_state_invalid")
    return payload["revision"], payload["preferences"]


def _read(db: Any, opaque_key: str) -> tuple[int, dict[str, str]]:
    row = db.execute("SELECT value FROM metadata WHERE key=?", (opaque_key,)).fetchone()
    return _decode(row[0] if row else None)


def preference_snapshot(storage: Any, identity_id: str) -> ReadingPreferenceSnapshot:
    """Read only the requested identity's durable preferences, without a scan."""
    key = _identity_key(identity_id)
    with storage._lock, storage._connect() as db:
        revision, prefs = _read(db, key)
    return ReadingPreferenceSnapshot(revision, tuple(sorted(prefs.items())))


def change_preference(
    storage: Any, identity_id: str, *,
    library_key: str | None,
    preference: str | None,
    expected_revision: int,
    user_confirmed: bool,
    clear_all: bool = False,
) -> ReadingPreferenceSnapshot:
    """CAS write/delete. No write is admitted without an explicit user action.

    Removing a missing key still increments the revision so stale workers are
    invalidated. Whole-profile clearing does not delete the revision fence.
    """
    opaque_key = _identity_key(identity_id)
    if (type(user_confirmed) is not bool or not user_confirmed
            or type(expected_revision) is not int
            or not 0 <= expected_revision < _MAX_REVISION
            or type(clear_all) is not bool):
        raise ValueError("reading_preference_confirmation_required")
    if clear_all:
        if library_key is not None or preference is not None:
            raise ValueError("reading_preference_target_invalid")
    else:
        _library_key(library_key)
        if preference is not None and (type(preference) is not str or preference not in _ALLOWED):
            raise ValueError("reading_preference_value_invalid")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        revision, existing = _read(db, opaque_key)
        if revision != expected_revision:
            raise ValueError("reading_preference_revision_changed")
        values = dict(existing)
        if clear_all:
            values.clear()
        elif preference is None:
            values.pop(library_key, None)
        else:
            values[library_key] = preference
        if len(values) > _MAX_ENTRIES:
            raise ValueError("reading_preference_capacity_exceeded")
        next_revision = revision + 1
        encoded = json.dumps(
            {"schema": 1, "revision": next_revision, "preferences": values},
            sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        )
        if len(encoded.encode("utf-8")) > _MAX_BYTES:
            raise ValueError("reading_preference_capacity_exceeded")
        db.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (opaque_key, encoded),
        )
    return ReadingPreferenceSnapshot(next_revision, tuple(sorted(values.items())))
