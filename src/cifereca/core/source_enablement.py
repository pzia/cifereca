"""Read-only source enablement rule shared by Core and Sync broker (#1346).

The only default-enable case is *no saved preference*. A malformed persisted
value is never permission to connect, fetch inventory or acquire owned files.
This decoder neither writes metadata nor exposes the raw stored value.
"""
from __future__ import annotations


def source_enabled(raw: object) -> bool:
    if raw is None:
        # Legacy installations: sources without a toggle remain visible.
        return True
    return type(raw) is str and raw.strip().lower() in {
        "1", "true", "yes", "on",
    }
