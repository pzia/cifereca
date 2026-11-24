"""#4552 Q7: durable, bounded diagnostic transition per approved witness.

Core-only: callers must authorize the exact source and compute the review
from actual input bytes. No notice mutation, provider IO or rights.
"""
from __future__ import annotations

import json

from .notice_parse_witness import ParseWitnessReview, admitted_source_scope_sha256
from .notice_parse_witness_store import _PREFIX, _decode

_ALERT_PREFIX = "notice.c.parse-witness-alert.v1:"
_STATUSES = {"parser_regression_confirmed", "parser_drift_suspected", "source_changed", "unchanged"}


def record_parse_witness_review(storage, *, key, parser_id, generation, review):
    """Return True only for a new diagnostic transition; ignore unverified.

    Compare the approved generation inside the same SQLite transaction.
    A healthy review clears the active alert, allowing a later recurrence.
    One bounded record per pin; no private coordinates or raw values.
    """
    if (type(generation) is not int or generation < 1
            or type(review) is not ParseWitnessReview
            or review.executable is not False
            or type(review.status) is not str
            or type(review.affected_fields) is not tuple):
        raise ValueError("invalid_parse_witness_alert")
    if review.status == "unverified":
        return False
    if review.status not in _STATUSES:
        raise ValueError("invalid_parse_witness_alert")
    scope = admitted_source_scope_sha256(key, parser_id=parser_id)
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT value FROM metadata WHERE key=?",
                         (_PREFIX + scope,)).fetchone()
        if row is None:
            return False
        approved = _decode(row[0], scope)
        if approved.generation != generation:
            return False
        allowed = {"critical_field_" + str(i) for i in
                   range(1, len(approved.baseline.critical_fields) + 1)}
        fields = review.affected_fields
        if (len(fields) > len(allowed)
                or any(type(field) is not str or field not in allowed for field in fields)
                or len(set(fields)) != len(fields)
                or bool(fields) != (review.status in {
                    "parser_regression_confirmed", "parser_drift_suspected"})):
            raise ValueError("invalid_parse_witness_alert")
        alert_key = _ALERT_PREFIX + scope
        if review.status == "unchanged":
            db.execute("DELETE FROM metadata WHERE key=?", (alert_key,))
            return False
        raw = json.dumps({"generation": generation, "status": review.status,
                          "fields": sorted(fields)}, sort_keys=True, separators=(",", ":"))
        previous = db.execute("SELECT value FROM metadata WHERE key=?",
                              (alert_key,)).fetchone()
        if previous is not None and previous[0] == raw:
            return False
        db.execute("INSERT INTO metadata(key,value) VALUES(?,?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (alert_key, raw))
        return True
