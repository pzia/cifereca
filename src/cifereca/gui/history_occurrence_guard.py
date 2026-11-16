"""Fail-closed History occurrence uniqueness before list/detail/N2 projection.

History upstream IDs are not globally unique across provider credentials,
borrowers or profiles. The current GTK History model indexes by bare item.id;
therefore any collision must be suppressed, not silently last-write-wins.
This does not assert a cross-provider Notice C edition relation.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def unambiguous_history_occurrences(
    rows: Iterable[Any], *, identity_id: str | None = None,
) -> tuple[Any, ...]:
    """Return source-unique History occurrences in original order.

    Identical replay of the *same object* is idempotent. Distinct DTOs with
    the same ID, even if their visible fields agree, are ambiguous because
    provider provenance or mutable image evidence may have changed.
    """
    known: dict[str, Any] = {}
    collisions: set[str] = set()
    try:
        for index, item in enumerate(rows):
            if index >= 10000:
                return ()
            key = getattr(item, "id", None)
            if type(key) is not str or not key or len(key) > 512:
                continue
            owner = getattr(item, "identity_id", None)
            if (identity_id is not None and owner is not None
                    and owner != identity_id):
                continue
            if key in collisions:
                continue
            previous = known.get(key)
            if previous is None:
                known[key] = item
            elif previous is not item:
                collisions.add(key)
                known.pop(key, None)
    except Exception:
        return ()
    return tuple(known.values())


__all__ = ["unambiguous_history_occurrences"]
