"""Opaque source-bound identity for loan presentation (CoreUI P0 #1131).

The same upstream loan ID can be visible from two independent Paris connections.
A UI row's identity must include its exact provider, credential, borrower account,
item and library context. Do not change DRM, borrower authorization or locking.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any


def loan_source_key(loan: Any) -> str:
    """Stable, opaque key for a *source-qualified* loan, not a credential string."""
    parts = [
        str(getattr(loan, "source_provider", "") or "").casefold(),
        str(getattr(loan, "source_credential", "") or ""),
        getattr(loan, "source_account_id", None),
        str(getattr(loan, "id", "") or ""),
        str(getattr(loan, "record_id", "") or ""),
        str(getattr(loan, "identity_id", "") or ""),
    ]
    data = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return "loan-source:" + hashlib.sha256(data.encode("utf-8")).hexdigest()


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
    if item.get("source_account_id") is not None:
        candidates = [
            loan for loan in candidates
            if str(getattr(loan, "source_account_id", "") or "")
            == str(item["source_account_id"])
        ]
    return candidates[0] if len(candidates) == 1 else None
