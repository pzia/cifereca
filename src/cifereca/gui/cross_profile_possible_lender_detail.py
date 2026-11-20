"""#4988: read-only possible-lender summary in shared Search/Recommendations.

A known local account may attempt the ordinary supplier borrow only after a
separate user confirmation. This presenter never invents eligibility or
performs provider IO; the optional consent entry is delegated.
"""
from __future__ import annotations

from ..models import SearchResult
from ..core.borrow_for_identity_discovery import PossibleBorrowLender


def append_possible_lender_summary(panel, item: SearchResult) -> bool:
    """Render neutral candidates only for the exact, still-visible notice."""
    if type(item) is not SearchResult:
        return False
    try:
        visible = panel._rendered_search_detail
        if (type(visible) is not tuple or len(visible) != 2
                or visible[0] is not item):
            return False
        service = panel.window.service
        identity = service.current_identity
        identity_id = getattr(identity, "identity_id", None)
        account_epoch = service._account_context_epoch
        source_epoch = service._source_context_epoch
        reader = service.possible_borrow_lenders_for_current_identity
        if (type(identity_id) is not str or not identity_id
                or type(account_epoch) is not int or account_epoch < 0
                or type(source_epoch) is not int or source_epoch < 0
                or not callable(reader)):
            return False
        rows = reader(exact_notice=item)
        if (type(rows) is not tuple or len(rows) > 64
                or any(type(row) is not PossibleBorrowLender
                       or row.executable is not False
                       or row.supplier_eligibility != "not_verified"
                       or row.lender_identity_id == identity_id
                       or row._notice is not item
                       for row in rows)):
            return False
        if not rows:
            return False
        if (service.current_identity is not identity
                or service._account_context_epoch != account_epoch
                or service._source_context_epoch != source_epoch
                or panel._rendered_search_detail is not visible):
            return False
        # No raw source/credential/account IDs, quota, callback or GTK button.
        details = [
            (
                f"Profil prêteur {i}",
                f"{row.label} · compte local prêt pour une tentative · "
                "offre et quota fournisseur non vérifiés · "
                "aucun emprunt lancé",
            )
            for i, row in enumerate(rows, 1)
        ]
        panel._group("Emprunt avec un autre profil · information", details)
        from .cross_profile_normal_lender_dialog import append_normal_lender_action
        append_normal_lender_action(
            panel, item=item, rows=rows, visible=visible, service=service,
            identity=identity, identity_id=identity_id, account_epoch=account_epoch,
            source_epoch=source_epoch,
        )
        return True
    except Exception:
        return False


__all__ = ["append_possible_lender_summary"]
