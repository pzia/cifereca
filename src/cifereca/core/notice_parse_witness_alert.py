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
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        return record_parse_witness_review_txn(
            db, key=key, parser_id=parser_id, generation=generation, review=review,
        )


def record_parse_witness_review_txn(db, *, key, parser_id, generation, review):
    """Record within the caller's already fenced publication transaction."""
    if not db.in_transaction:
        raise ValueError("notice_work_transaction_required")
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
    if previous is not None:
        prior = json.loads(previous[0])
        if {k: prior.get(k) for k in ("generation", "status", "fields")} == json.loads(raw):
            return False
    db.execute("INSERT INTO metadata(key,value) VALUES(?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (alert_key, raw))
    return True


def read_parse_witness_diagnostic(storage, *, key, parser_id):
    """Core projection for an exact source already authorized by the caller.

    Read pin, alert and capture in one SQLite snapshot. No raw input,
    documentary fields, filesystem paths or provider coordinates escape.
    """
    from .notice_regression_capture_intent import _PREFIX as capture_prefix, _SHA
    scope = admitted_source_scope_sha256(key, parser_id=parser_id)
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        pin = db.execute("SELECT value FROM metadata WHERE key=?",
                         (_PREFIX + scope,)).fetchone()
        row = db.execute("SELECT value FROM metadata WHERE key=?",
                         (_ALERT_PREFIX + scope,)).fetchone()
        if pin is None or row is None:
            return None
        approved = _decode(pin[0], scope)
        alert = json.loads(row[0])
        if alert.get("generation") != approved.generation:
            return None
        result = {name: alert[name] for name in ("generation", "status", "fields")}
        diagnostic = alert.get("capture_id")
        if type(diagnostic) is str and _SHA.fullmatch(diagnostic):
            capture = db.execute("SELECT value FROM metadata WHERE key=?",
                                 (capture_prefix + diagnostic,)).fetchone()
            result["capture_id"] = diagnostic
            result["capture_state"] = "metadata_unavailable"
            if capture is not None:
                record = json.loads(capture[0])
                if (record.get("generation") == approved.generation
                        and record.get("status") == alert["status"]
                        and record.get("fields") == alert["fields"]):
                    result["capture_state"] = record["capture_state"]
        elif alert.get("capture_state") == "capacity_refused":
            result["capture_state"] = "capacity_refused"
        else:
            result["capture_state"] = "not_recorded"
        return result
