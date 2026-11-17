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


def _has_native_action_source(loan: Any) -> bool:
    """An actionable loan must have native, exact provenance coordinates.

    A plugin may populate a Loan dataclass with non-native attributes despite
    its annotations. Stringifying those fields in a GTK SourceKey can invoke
    plugin code or alias a foreign borrower/backend to a valid row. None is a
    permitted *unknown* account or edition, never a guessed primary account.
    """
    return (
        type(loan) is Loan
        and type(loan.id) is str and bool(loan.id)
        and type(loan.source_provider) is str and bool(loan.source_provider)
        and type(loan.source_credential) is str and bool(loan.source_credential)
        and type(loan.source_account_id) in (str, type(None))
        and type(loan.record_id) in (str, type(None))
        and type(loan.identity_id) is str and bool(loan.identity_id)
        and type(loan.provider) is str and bool(loan.provider)
        and type(loan.medium) is str and bool(loan.medium)
    )


def unambiguous_loan_rows(loans: Iterable[Any]) -> tuple[Loan, ...]:
    """Return only well-formed, source-unique loans suitable for GTK actions.

    A provider may replay one identical loan card more than once: display it
    once. Two DIFFERENT cards claiming the same exact SourceKey cannot both
    be actionable through one opaque row key; suppress that key rather than
    choosing first/last. No title/hash/account guesses or provider I/O.
    """
    rows: dict[str, Loan] = {}
    conflicting: set[str] = set()
    try:
        for index, loan in enumerate(loans):
            if index >= 10000:
                # A truncated inventory cannot establish that any source key
                # is unique; the missing tail may contradict an earlier card.
                return ()
            if not _has_native_action_source(loan):
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
    except Exception:
        # Never publish early action rows from a failed lazy provider source.
        return ()
    return tuple(rows.values())


def unique_library_loan(
    loans: Iterable[Any], item: Mapping[str, Any],
) -> Any | None:
    """Select only a single loan matching an immutable native library scope.

    A legacy row containing only loan_id may resolve one unambiguous loan.
    Explicitly supplied source qualifiers must be native, nonempty strings;
    the account ID alone may be empty (the historical primary sentinel).
    Never assemble an action identity using a loan key read before a provider
    callback and source qualifiers read afterwards.
    """
    if not isinstance(item, Mapping):
        return None
    fields = (
        ("source_provider", "provider_id"),
        ("identity_id", "identity_id"),
        ("record_id", "item_id"),
        ("source_credential", "source_credential"),
    )
    scope_keys = ("loan_id", *(item_key for _, item_key in fields),
                  "source_account_id")
    try:
        # Pin the complete descriptor before enumerating provider-backed or
        # otherwise lazy loan rows, which can mutate the library selection.
        scope = tuple(item.get(key) for key in scope_keys)
    except Exception:
        return None
    loan_id, *qualifiers, account = scope
    if (type(loan_id) is not str or not 0 < len(loan_id) <= 1024
            or not loan_id.isprintable()):
        return None
    if any(value is not None and (
        type(value) is not str or not value or len(value) > 1024
    ) for value in qualifiers):
        return None
    if (account is not None
            and (type(account) is not str or len(account) > 1024)):
        return None

    candidates: list[Loan] = []
    try:
        for index, loan in enumerate(loans):
            if index >= 10000:
                # A truncated inventory cannot prove unique source ownership.
                return None
            if _has_native_action_source(loan) and loan.id == loan_id:
                candidates.append(loan)
        for (loan_attr, _), expected in zip(fields, qualifiers):
            if expected is not None:
                candidates = [
                    loan for loan in candidates
                    if type(getattr(loan, loan_attr, None)) is str
                    and getattr(loan, loan_attr) == expected
                ]
        if account is not None:
            candidates = [
                loan for loan in candidates
                if type(getattr(loan, "source_account_id", None)) is str
                and loan.source_account_id == account
            ]
        if len(candidates) != 1:
            return None
        # The library selection is another mutable provider/UI projection.
        # A late source/identity/borrower change cannot retarget the action.
        return candidates[0] if tuple(item.get(key) for key in scope_keys) == scope else None
    except Exception:
        return None
