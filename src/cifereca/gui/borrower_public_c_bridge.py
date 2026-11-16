"""Core-originated public Notice C N2 fallback for image-less Loan/History.

No account/database/provider queries on GTK. The source private occurrence
remains authoritative for every action, due date, borrower and return.
Public C carries only the catalogue cover. Never use an unrelated title/ISBN
or a weaker HistoryCoverResolver search result as documentary source.
"""
from __future__ import annotations

from typing import Any, Callable

from ..models import Loan, HistoryItem
from ..core.loan_source_identity import loan_source_key
from ..core.notice_c_borrower_public_projection import (
    VerifiedBorrowerPublicC, borrower_public_c_receipt,
)


def exact_loan_public_c(host: Any, loan: Loan):
    service = getattr(host, "service", None)
    identity_id = getattr(
        getattr(service, "current_identity", None),
        "identity_id", None,
    )
    if (type(loan) is not Loan or loan.identity_id != identity_id
            or type(identity_id) is not str or not identity_id
            or getattr(host, "loan_objects", {}).get(
                loan_source_key(loan),
            ) is not loan
            or type(getattr(service, "_account_context_epoch", None)) is not int
            or type(getattr(service, "_source_context_epoch", None)) is not int):
        return None
    return borrower_public_c_receipt(loan, identity_id=identity_id)


def exact_history_public_c(host: Any, item: HistoryItem):
    service = getattr(host, "service", None)
    identity_id = getattr(
        getattr(service, "current_identity", None),
        "identity_id", None,
    )
    if (type(item) is not HistoryItem or item.identity_id != identity_id
            or type(identity_id) is not str or not identity_id
            or type(getattr(host, "history_objects", None)) is not dict
            or host.history_objects.get(item.id) is not item
            or type(getattr(service, "_account_context_epoch", None)) is not int
            or type(getattr(service, "_source_context_epoch", None)) is not int
            or type(getattr(host, "_history_cover_revision", None)) is not int
            or host._history_cover_revision <= 0):
        return None
    return borrower_public_c_receipt(item, identity_id=identity_id)


def admit_borrower_public_c_cell(host: Any, row: Any, document: object) -> bool:
    """Selection/ref/epoch gate for a recycled GTK list cell."""
    receipt = getattr(row, "_cover_borrower_public_c_receipt", None)
    source = getattr(row, "_borrower_public_c_source", None)
    if (type(receipt) is not VerifiedBorrowerPublicC
            or receipt.source_item is not source
            or document != receipt.document
            or getattr(row, "_cover_document_ref", None) != document):
        return False
    service = getattr(host, "service", None)
    active = getattr(
        getattr(service, "current_identity", None), "identity_id", None,
    )
    context = getattr(row, "_borrower_public_c_context", None)
    if (type(context) is not tuple
            or len(context) != 3
            or context[0] != active
            or context[1] != getattr(service, "_account_context_epoch", None)
            or context[2] != getattr(service, "_source_context_epoch", None)):
        return False
    if type(source) is Loan:
        return (getattr(row, "key", None) == loan_source_key(source)
                and exact_loan_public_c(host, source) is receipt)
    if type(source) is HistoryItem:
        return (getattr(row, "key", None) == source.id
                and getattr(row, "_history_cover_revision", None) ==
                    getattr(host, "_history_cover_revision", None)
                and exact_history_public_c(host, source) is receipt)
    return False


def show_public_loan_c(
    host: Any, loan: Loan, display: Callable[[], Loan],
) -> bool:
    """Fallback ONLY after attested EPUB and direct supplier cover fail."""
    receipt = exact_loan_public_c(host, loan)
    if receipt is None:
        return False
    presentation = display()
    if (type(presentation) is not Loan
            or presentation.thumbnail_url is not None):
        return False
    panel = getattr(getattr(host, "loan_page", None), "detail", None)
    show = getattr(panel, "show_loan_public_c", None)
    if not callable(show):
        return False
    show(presentation, document=receipt.document)
    return True


def show_public_history_c(host: Any, source: HistoryItem, presentation: HistoryItem) -> bool:
    """Use same public C in History detail as in History list."""
    receipt = exact_history_public_c(host, source)
    if receipt is None or type(presentation) is not HistoryItem:
        return False
    panel = getattr(getattr(host, "history_page", None), "detail", None)
    show = getattr(panel, "show_history_public_c", None)
    if not callable(show):
        return False
    show(presentation, document=receipt.document)
    return True


__all__ = [
    "exact_loan_public_c", "exact_history_public_c",
    "admit_borrower_public_c_cell",
    "show_public_loan_c", "show_public_history_c",
]
