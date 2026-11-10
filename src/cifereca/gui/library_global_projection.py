"""P1 #2385: flatten the Core's read-only, owner-scoped global Library view.

The upstream API keeps each profile's entry independent. Never merge rows
by ISBN, SHA, title or provider, and never turn a descriptive row into a
provider permission or a new physical placement.
"""
from __future__ import annotations


def global_library_display_rows(rows: object) -> list[dict]:
    """Return bounded independent UI rows with collision-safe display keys."""
    if type(rows) not in (list, tuple) or len(rows) > 20000:
        return []
    seen: set[tuple[str, str]] = set()
    duplicated: set[tuple[str, str]] = set()
    staged: list[tuple[tuple[str, str], dict]] = []
    for wrapper in rows:
        if type(wrapper) is not dict or set(wrapper) != {
            "identity_id", "entry",
        }:
            continue
        owner, book = wrapper["identity_id"], wrapper["entry"]
        if (type(owner) is not str or not 0 < len(owner) <= 160
                or not owner.isprintable() or type(book) is not dict):
            continue
        declared_owner = book.get("identity_id")
        if declared_owner not in (None, owner):
            continue
        key = book.get("key")
        if (type(key) is not str or not 0 < len(key) <= 1024
                or not key.isprintable()):
            continue
        identity = (owner, key)
        if identity in seen:
            duplicated.add(identity)
            continue
        seen.add(identity)
        row = dict(book)
        row["identity_id"] = owner
        # Presentation-only list key; book["key"] remains the exact Core
        # identity for subsequent read-only library and reference operations.
        row["_library_ui_key"] = (
            f"global:{len(owner)}:{owner}:{len(key)}:{key}"
        )
        row["_library_view_owner"] = owner
        staged.append((identity, row))
    # Ambiguous book keys for the same owner are neither selectable nor
    # sufficient to resolve an action. Foreign profiles may share a key.
    return [row for identity, row in staged if identity not in duplicated]


__all__ = ["global_library_display_rows"]
