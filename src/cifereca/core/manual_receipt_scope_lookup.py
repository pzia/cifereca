"""Passive lookup of a previously completed, exactly scoped manual receipt.

Only the declared account/identity/reference is inspected. This helper does
not open paths, infer a loan or purchase, authorize a GET, or write SQLite.
An ambiguous completed receipt is an integrity issue, not a random winner.
"""
from __future__ import annotations

from pathlib import Path


def exact_manual_receipt(rows: object, *, identity_id: str,
                         metadata_ref: str, expected_ref: str):
    """Return (status, row, path) with status absent/matched/ambiguous.

    The caller must independently verify the selected artifact's physical
    bytes and placement under the current consent/account lease.
    """
    if (type(rows) not in (list, tuple)
            or len(rows) > 100_000
            or type(identity_id) is not str or not identity_id
            or type(metadata_ref) is not str or not metadata_ref
            or type(expected_ref) is not str or not expected_ref):
        return ("ambiguous", None, None)
    found = []
    for row in rows:
        if type(row) is not dict:
            return ("ambiguous", None, None)
        if row.get("identity_id") != identity_id:
            continue
        meta = row.get("metadata") or {}
        if type(meta) is not dict:
            return ("ambiguous", None, None)
        if meta.get(metadata_ref) != expected_ref:
            continue
        path = meta.get("path")
        if type(path) is not str or not path:
            return ("ambiguous", None, None)
        found.append((row, Path(path)))
        if len(found) > 1:
            return ("ambiguous", None, None)
    return (("matched", *found[0]) if found
            else ("absent", None, None))
