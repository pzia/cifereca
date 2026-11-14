"""Historical 0.8.1 Kobo account-cardinality repair, invoked at most once.

This is a **versioned config-data migration**, not a Core provider policy.
It does not touch SQLite, provider sessions, a local EPUB, source consent or
ADEPT grants. Newly edited accounts must pass normal Settings validation.
A completed or malformed existing tombstone is never silently overwritten.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any, Callable

MIGRATION_ID = "legacy_kobo_account_cardinality_v1"


def migrate_once(
    *, data: dict[str, Any], identities: list[dict[str, Any]],
    rows: list[dict[str, Any]], default_identity: str,
    used_directories: set[str], directory_slug: Callable[[str, str], str],
) -> bool:
    """Perform the historical deterministic split and record one-way closure.

    Existing broken history is not replaced. A missing migration marker is
    considered eligible only when historical Kobo rows actually exist.
    Return True if account/identity rows were changed; the caller persists
    those rows using its existing load-time migration handling.
    """
    history = data.get("migration_history")
    if history is not None and type(history) is not dict:
        return False
    if type(history) is dict and MIGRATION_ID in history:
        return False
    if not any(str(row.get("provider") or "").strip().lower() == "kobo"
               for row in rows if type(row) is dict):
        return False

    seen_kobo: dict[str, str] = {}
    identity_by_id = {
        str(item.get("id") or ""): item
        for item in identities if type(item) is dict
    }
    split_count = 0
    for row in rows:
        if type(row) is not dict or str(row.get("provider") or "").strip().lower() != "kobo":
            continue
        credential_id = str(row.get("id") or "").strip()
        identity_id = str(row.get("identity_id") or default_identity).strip() or default_identity
        previous = seen_kobo.get(identity_id)
        if not previous or previous == credential_id:
            seen_kobo[identity_id] = credential_id
            continue
        suffix = hashlib.sha256(
            credential_id.encode("utf-8", "surrogatepass")
        ).hexdigest()[:8]
        base_id = f"{identity_id}-kobo-{suffix}"
        derived_id = base_id
        serial = 2
        while derived_id in identity_by_id:
            derived_id = f"{base_id}-{serial}"
            serial += 1
        source = identity_by_id.get(identity_id, {})
        derived_label = str(row.get("label") or f"{source.get('label') or identity_id} Kobo")
        directory_base = directory_slug(derived_label, derived_id)
        directory_name = directory_base
        serial_dir = 2
        while directory_name in used_directories:
            directory_name = f"{directory_base}-{serial_dir}"
            serial_dir += 1
        used_directories.add(directory_name)
        derived = {
            "id": derived_id,
            "label": derived_label,
            "directory_name": directory_name,
            "preferences": deepcopy(source.get("preferences") or {
                "prefer_original_language": False, "reading_languages": ["fr"],
            }),
        }
        identities.append(derived)
        identity_by_id[derived_id] = derived
        row["identity_id"] = derived_id
        seen_kobo[derived_id] = credential_id
        split_count += 1

    if history is None:
        history = {}
        data["migration_history"] = history
    history[MIGRATION_ID] = {
        "schema_version": 1,
        "status": "completed",
        "split_count": split_count,
    }
    return split_count > 0
