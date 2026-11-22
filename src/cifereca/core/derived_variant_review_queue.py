"""Private, scoped discovery of exact pending review handles (#4520)."""
from __future__ import annotations

from .derived_variant_journal import (
    _read_logical_receipt,
    DerivedJournalConflict, DerivedReservation, _PREFIX, _decode, _digest,
    _measure, _receipt_digest,
)


def pending_derived_review_page(storage, *, identity_id, after_key="", limit=32):
    """Read only an exact identity's pending reservations, without filesystem IO.

    A handle identifies an exact journal payload, not an approved operation.
    The owning Core controller must pin the active profile, display a private
    review, and obtain a separate explicit confirmation before any recovery.
    Never return paths, titles, recipe details or content digests.
    """
    if (type(identity_id) is not str or not identity_id or len(identity_id) > 1024
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in identity_id)
            or type(limit) is not int or not 1 <= limit <= 32
            or type(after_key) is not str
            or (after_key and (not after_key.startswith(_PREFIX)
                               or not _digest(after_key[len(_PREFIX):])))):
        raise DerivedJournalConflict("derived_review_queue_invalid")
    with storage._lock:
        db = storage._connect()
        # A borrowed connection may already own a caller transaction. Never
        # enter its context manager (which could commit/rollback that work).
        if db.in_transaction:
            raise DerivedJournalConflict("derived_review_queue_nested_transaction")
        with db:
            db.execute("BEGIN")
            profile = db.execute(
                "SELECT directory_name FROM identities WHERE identity_id=? LIMIT 2",
                (identity_id,),
            ).fetchall()
            if len(profile) != 1:
                raise DerivedJournalConflict("derived_review_queue_scope")
            directory = profile[0][0]
            if (type(directory) is not str or not directory or directory in (".", "..")
                    or "/" in directory or chr(92) in directory
                    or db.execute(
                        "SELECT identity_id FROM identities WHERE directory_name=? LIMIT 2",
                        (directory,),
                    ).fetchall() != [(identity_id,)]):
                raise DerivedJournalConflict("derived_review_queue_scope")
            rows, _total = _measure(db)
            handles = []
            cursor_seen = not after_key
            for key, _size in rows:
                if not key.startswith(_PREFIX):
                    continue
                raw = _read_logical_receipt(db, key)
                intent = _decode(key, raw)
                if intent.identity_id != identity_id:
                    continue
                if key == after_key:
                    cursor_seen = True
                if key > after_key and len(handles) <= limit:
                    handles.append(DerivedReservation(key, _receipt_digest(raw)))
            # A completed/removed cursor or a foreign cursor cannot quietly skip
            # work. The supervisor must restart from a fresh first page.
            if not cursor_seen:
                raise DerivedJournalConflict("derived_review_queue_cursor_changed")
    more = len(handles) > limit
    visible = tuple(handles[:limit])
    return {
        "reservations": visible,
        "next_key": visible[-1].key if more else None,
        "complete": not more,
        "recovery_authorized": False,
        "destructive_cleanup_allowed": False,
    }
