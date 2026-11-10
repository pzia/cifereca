"""#4988: inert shared Search/Recommendations detail for already owned loans.

A prior confirmed lender loan is a fact, not a new borrowing opportunity.
This presenter never discovers provider eligibility or opens an action.
"""
from __future__ import annotations

from ..models import SearchResult
from ..core.borrow_for_identity_existing_loan import ExistingLenderLoanCandidate


def append_existing_lender_loan_summary(panel, item: SearchResult) -> bool:
    """Show only privacy-safe counts and delivery states for an exact notice.

    The common DetailPanel is used by normal Search and exact Recommendations.
    SQLite reads are scoped by the Core and never authorize a supplier POST,
    local copy, account switch or access to private lender file paths.
    """
    if type(item) is not SearchResult:
        return False
    try:
        visible = panel._rendered_search_detail
        if (type(visible) is not tuple or len(visible) != 2
                or visible[0] is not item):
            return False
        window = panel.window
        service = window.service
        identity = service.current_identity
        account_epoch = service._account_context_epoch
        source_epoch = service._source_context_epoch
        reader = service.existing_lender_loan_candidates
        if (type(account_epoch) is not int or account_epoch < 0
                or type(source_epoch) is not int or source_epoch < 0
                or not callable(reader)):
            return False
        rows = reader(exact_notice=item)
        if (type(rows) is not tuple or len(rows) > 64
                or any(type(row) is not ExistingLenderLoanCandidate
                       or row.loan != "confirmed"
                       or row.executable is not False
                       or row.retry_borrow is not False
                       or row.status not in ("present", "awaiting_delivery")
                       for row in rows)):
            return False
        if not rows:
            return False
        if (service.current_identity is not identity
                or service._account_context_epoch != account_epoch
                or service._source_context_epoch != source_epoch
                or panel._rendered_search_detail is not visible):
            return False
        details = [
            (
                f"Profil prêteur {i}",
                "Prêt confirmé · copie locale présente ; envoi distinct requis"
                if row.status == "present" else
                "Prêt confirmé · livraison locale en attente",
            )
            for i, row in enumerate(rows, 1)
        ]
        panel._group("Prêts sur d’autres profils", details)
        return True
    except Exception:
        return False


__all__ = ["append_existing_lender_loan_summary"]
