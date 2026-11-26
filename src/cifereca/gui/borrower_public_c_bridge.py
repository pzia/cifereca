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
    return borrower_public_c_receipt(
        loan, identity_id=identity_id,
        account_epoch=service._account_context_epoch,
        source_epoch=service._source_context_epoch,
    )


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
    return borrower_public_c_receipt(
        item, identity_id=identity_id,
        account_epoch=service._account_context_epoch,
        source_epoch=service._source_context_epoch,
    )


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
        from .loan_cover_decision_bridge import current_loan_embedded_conflict
        return (getattr(row, "key", None) == loan_source_key(source)
                and exact_loan_public_c(host, source) is receipt
                and not current_loan_embedded_conflict(host, source))
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
    from .loan_cover_decision_bridge import current_loan_embedded_conflict
    if current_loan_embedded_conflict(host, loan):
        return False
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
    request_borrower_public_web_action(host, panel, loan, receipt)
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
    request_borrower_public_web_action(host, panel, source, receipt)
    return True


def borrower_public_web_selection_check(host, panel, source, receipt):
    """Capture the actual selected occurrence and DetailPanel render.

    DetailPanel replaces its action list at every full render, revoking
    both old buttons and requests that have not yet created a button.
    """
    if type(source) is Loan:
        page_name, route, revision_name = "loan_page", "loans", "_loan_cover_decision_revision"
        exact = exact_loan_public_c
    elif type(source) is HistoryItem:
        page_name, route, revision_name = "history_page", "history", "_history_cover_revision"
        exact = exact_history_public_c
    else:
        return lambda: False
    service = getattr(host, "service", None)
    revision = getattr(host, revision_name, None)
    page = getattr(host, page_name, None)
    selection = getattr(page, "selection", None)
    selected = getattr(selection, "get_selected_item", None)
    row = selected() if callable(selected) else None
    actions = getattr(panel, "_action_buttons", None)

    def current():
        stack = getattr(host, "view_stack", None)
        visible = getattr(stack, "get_visible_child_name", None)
        return (
            not getattr(host, "_closed", False)
            and type(receipt) is VerifiedBorrowerPublicC
            and receipt.source_item is source
            and getattr(host, "service", None) is service
            and type(revision) is int
            and getattr(host, revision_name, None) == revision
            and getattr(host, page_name, None) is page
            and getattr(page, "detail", None) is panel
            and getattr(page, "selection", None) is selection
            and type(actions) is list
            and getattr(panel, "_action_buttons", None) is actions
            and callable(visible) and visible() == route
            and row is not None and callable(selected) and selected() is row
            and exact(host, source) is receipt
            and admit_borrower_public_c_cell(host, row, receipt.document)
        )

    return current


def request_borrower_public_web_action(host, panel, source, receipt):
    """Same public C action, never a private borrowing/renewal permission."""
    current = borrower_public_web_selection_check(host, panel, source, receipt)
    if not current():
        return False
    from .notice_c_web_action_controller import request_public_web_actions
    return request_public_web_actions(
        host, panel, receipt.document, expected_epoch=receipt.epoch,
        current=current,
    )


def bind_borrower_public_c_detail(
    host: Any, cover: Any, document: object,
    receipt: object,
) -> bool:
    """Keep a detail N3 response bound to its exact borrower occurrence.

    The Core worker already attached the only permitted public N2 reference.
    Here the GTK cell consumes it, but must recheck the same original receipt
    after the asynchronous N3 request, including A→B→A account/source and
    an intervening refresh of the same row ID. No supplier GET/SQLite/rights.
    """
    from .document_cover_service import DocumentCoverService

    service_owner = getattr(host, "service", None)
    source = getattr(receipt, "source_item", None)
    is_loan = type(source) is Loan
    is_history = type(source) is HistoryItem
    revision_name = (
        "_loan_cover_decision_revision" if is_loan
        else "_history_cover_revision"
    )
    revision = getattr(host, revision_name, None)
    document_service = getattr(host, "document_covers", None)

    def is_current() -> bool:
        if (getattr(host, "service", None) is not service_owner
                or getattr(host, revision_name, None) != revision
                or type(receipt) is not VerifiedBorrowerPublicC
                or receipt.document != document):
            return False
        if is_loan:
            from .loan_cover_decision_bridge import current_loan_embedded_conflict
            return (exact_loan_public_c(host, source) is receipt
                    and not current_loan_embedded_conflict(host, source))
        if is_history:
            return exact_history_public_c(host, source) is receipt
        return False

    def missing() -> bool:
        cover.bind_url(getattr(host, "covers", None), None, priority=0)
        return False

    if (type(document_service) is not DocumentCoverService
            or not is_current()):
        return missing()
    try:
        cover.bind_document(
            document_service, document, variant="detail", priority=0,
            is_current=is_current,
            minimum_revision=receipt.epoch,
        )
    except (TypeError, ValueError, RuntimeError):
        return missing()
    return True


__all__ = [
    "exact_loan_public_c", "exact_history_public_c",
    "admit_borrower_public_c_cell", "bind_borrower_public_c_detail",
    "show_public_loan_c", "show_public_history_c",
]
