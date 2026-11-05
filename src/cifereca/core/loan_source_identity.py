"""Opaque source-bound identity for loan presentation (CoreUI P0 #1131).

The same upstream loan ID can be visible from two independent Paris connections.
A UI row's identity must include its exact source provider, credential, borrower
account, upstream backend, loan medium, item and library context. Do not change
DRM, borrower authorization or locking.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from ..models import Loan


def loan_source_key(loan: Any) -> str:
    """Stable, opaque key for a *source-qualified* loan, not a credential string."""
    parts = [
        str(getattr(loan, "source_provider", "") or "").casefold(),
        str(getattr(loan, "source_credential", "") or ""),
        getattr(loan, "source_account_id", None),
        str(getattr(loan, "id", "") or ""),
        str(getattr(loan, "record_id", "") or ""),
        # Upstream loan IDs may collide between backends and loan media
        # inside the same credential/borrower. A GTK row must not alias a
        # distinct physical or digital loan before a user action.
        str(getattr(loan, "provider", "") or ""),
        str(getattr(loan, "medium", "") or ""),
        str(getattr(loan, "identity_id", "") or ""),
    ]
    data = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return "loan-source:" + hashlib.sha256(data.encode("utf-8")).hexdigest()


def unambiguous_loan_rows(loans: Iterable[Any]) -> tuple[Loan, ...]:
    """Return only well-formed, source-unique loans suitable for GTK actions.

    A provider may replay one identical loan card more than once: display it
    once. Two DIFFERENT cards claiming the same exact SourceKey cannot both
    be actionable through one opaque row key; suppress that key rather than
    choosing first/last. No title/hash/account guesses or provider I/O.
    """
    rows: dict[str, Loan] = {}
    conflicting: set[str] = set()
    for loan in loans:
        if type(loan) is not Loan:
            continue
        key = loan_source_key(loan)
        if key in conflicting:
            continue
        previous = rows.get(key)
        if previous is None:
            rows[key] = loan
            continue
        try:
            identical = previous == loan
        except (ValueError, TypeError, RecursionError):
            identical = False
        if not identical:
            conflicting.add(key)
            rows.pop(key, None)
    return tuple(rows.values())


def unique_library_loan(
    loans: Iterable[Any], item: Mapping[str, Any],
) -> Any | None:
    """Allow a library-to-loan action only for a single proven source match.

    A merged library entry may contain just `loan_id`. Never select the first
    arbitrary borrower on this evidence. Optional exact provenance fields,
    when provided by the library, narrow the match; no UI active-identity
    fallback or title/ISBN heuristic is permitted.
    """
    loan_id = str(item.get("loan_id") or "")
    if not loan_id:
        return None
    candidates = [loan for loan in loans if str(getattr(loan, "id", "") or "") == loan_id]
    fields = (
        ("source_provider", "provider_id"),
        ("identity_id", "identity_id"),
        ("record_id", "item_id"),
        ("source_credential", "source_credential"),
    )
    for loan_attr, item_attr in fields:
        expected = item.get(item_attr)
        if expected is not None and str(expected) != "":
            candidates = [
                loan for loan in candidates
                if str(getattr(loan, loan_attr, "") or "") == str(expected)
            ]
    # An explicit empty primary-account ID is NOT an unknown borrower.
    # Converting both to strings made None and "" indistinguishable and
    # could attach an action to the wrong exact lending account.
    account = item.get("source_account_id")
    if account is not None:
        if type(account) is not str:
            return None
        candidates = [
            loan for loan in candidates
            if type(getattr(loan, "source_account_id", None)) is str
            and loan.source_account_id == account
        ]
    return candidates[0] if len(candidates) == 1 else None
