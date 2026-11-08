"""Passive, provider-neutral projection of the one-shot loan.borrow checkpoint.

This is a *consumption* observation, never permission to retry a borrow.
The caller must validate the exact borrower, identity and edition before
reading the journal key. No private key, ID or raw journal is returned.
"""
from __future__ import annotations

import json


def observed_borrow_claim_state(raw: object) -> tuple[str, bool, bool]:
    """Return (public state, consumed, confirmed) without interpreting rights.

    Only None means that the checkpoint is absent. Any malformed or unknown
    record is consumed, never eligible for a new POST.
    """
    if raw is None:
        return ("no_checkpoint", False, False)
    if type(raw) is not str or len(raw) > 4096:
        return ("opaque_consumed", True, False)
    try:
        row = json.loads(raw)
    except (TypeError, ValueError):
        return ("opaque_consumed", True, False)
    if (type(row) is not dict or type(row.get("schema")) is not int
            or row["schema"] != 1 or row.get("operation") != "loan.borrow"
            or type(row.get("operation_id")) is not str
            or not row["operation_id"]):
        return ("opaque_consumed", True, False)
    status = row.get("status")
    if status == "confirmed":
        confirmed = (
            row.get("request_accepted") is True
            and row.get("verification") == "positive_active_loan"
            and type(row.get("verified_loan_id")) is str
            and 0 < len(row["verified_loan_id"]) <= 128
            and all(c.isascii() and (c.isalnum() or c in "_-")
                    for c in row["verified_loan_id"])
        )
        return ("confirmed" if confirmed else "opaque_consumed", True, confirmed)
    if type(status) is str and status in {
        "request_sent", "submitted", "verification_unavailable", "blocked",
    }:
        return (status, True, False)
    return ("opaque_consumed", True, False)
