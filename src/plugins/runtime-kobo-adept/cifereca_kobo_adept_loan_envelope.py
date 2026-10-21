"""Narrow read-only Kobo-ADEPT loan-list envelope attestation (#1120).

Kobo-ADEPT can publish descriptive loan inventory JSON without an `ok`
acknowledgement. That is not proof of *any* action succeeding. The exception
is authorized only for one exact, non-mutating CLI command with a complete
minimum stable loans[] payload. No credentials, loan IDs, titles, paths or
CLI output are ever projected into diagnostics by this classifier.
"""
from __future__ import annotations

from typing import Any


def attested_readonly_loans_list(
    args: Any, *, payload: Any, returncode: Any, value: Any,
) -> bool:
    """Validate the entire descriptive loan-list document, never a mutation.

    Unexpected envelope shapes, conflicting status fields or a malformed loan
    fail closed through run_json's existing ok_missing classification.
    """
    if (
        type(args) is not list or len(args) != 5
        or args[:3] != ['loans', 'list', '--profile']
        or args[4] != '--json'
        or payload is not None or returncode != 0
        or type(value) is not dict
    ):
        return False
    name = args[3]
    if (type(name) is not str or not 1 <= len(name) <= 128
            or name != name.strip() or name.startswith('-')
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in name)):
        return False
    # A response explicitly claiming success/failure must obey the ordinary
    # typed-acknowledgement contract. Never infer success from error-bearing
    # or ambiguous top-level fields, regardless of a valid loans[] list.
    if any(field in value for field in (
        'ok', 'error', 'errors', 'success', 'status', 'failure',
    )):
        return False
    if 'profile' in value and (
        type(value['profile']) is not str or value['profile'] != name
    ):
        return False
    rows = value.get('loans')
    if type(rows) is not list or len(rows) > 5000:
        return False
    seen = set()
    for row in rows:
        if type(row) is not dict:
            return False
        loan_id = row.get('id')
        if (type(loan_id) is not str
                or not 1 <= len(loan_id) <= 256
                or loan_id != loan_id.strip()
                or loan_id.startswith('-')
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in loan_id)
                or loan_id in seen
                or any(type(row.get(key)) is not bool for key in (
                    'expired', 'returned', 'token_present',
                ))):
            return False
        seen.add(loan_id)
    return True
