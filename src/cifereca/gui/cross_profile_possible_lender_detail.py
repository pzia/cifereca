"""#4988: read-only possible-lender summary in shared Search/Recommendations.

The existing worker reads local accounts/SQLite. Only the original exact
detail can consume its result; no eligibility, loan or copy is inferred.
"""
from __future__ import annotations

from ..models import SearchResult
from ..core.borrow_for_identity_discovery import possible_lender_matches
from ..task_queue import TaskPriority


def append_possible_lender_summary(panel, item: SearchResult) -> bool:
    """Schedule a neutral discovery for the exact visible detail."""
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
        identity_id = getattr(identity, "identity_id", None)
        account_epoch = service._account_context_epoch
        source_epoch = service._source_context_epoch
        route = getattr(window, "_route_display_token", None)
        ui_identity = getattr(window, "_identity_context_generation", None)
        ui_account = getattr(window, "_account_context_generation", None)
        reader = service.possible_borrow_lenders_for_current_identity
        submit = window.runner.submit
        if (type(identity_id) is not str or not identity_id
                or type(account_epoch) is not int or account_epoch < 0
                or type(source_epoch) is not int or source_epoch < 0
                or not callable(reader) or not callable(submit)):
            return False

        def scope_current():
            # Plain owner/generation checks only, safe on the worker.
            return (not getattr(window, "_closed", False)
                    and window.service is service
                    and service.current_identity is identity
                    and identity.identity_id == identity_id
                    and service._account_context_epoch == account_epoch
                    and service._source_context_epoch == source_epoch
                    and getattr(window, "_route_display_token", None) is route
                    and getattr(window, "_identity_context_generation", None) == ui_identity
                    and getattr(window, "_account_context_generation", None) == ui_account)

        def work():
            if not scope_current():
                return ()
            return reader(exact_notice=item) if scope_current() else ()

        pending = True

        def consume():
            nonlocal pending
            if not pending:
                return False
            pending = False
            return True

        def delivered(rows):
            if not consume():
                return
            try:
                mapped = getattr(panel, "get_mapped", None)
                if (not scope_current() or panel._rendered_search_detail is not visible
                        or (callable(mapped) and not mapped())
                        or type(rows) is not tuple or not 1 <= len(rows) <= 64
                        or any(not possible_lender_matches(
                                   row, item, requesting_identity_id=identity_id,
                               ) for row in rows)):
                    return
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
                # GTK may synchronously notify and navigate during the group
                # append. Never install a choice under a new route.
                if (not scope_current() or panel._rendered_search_detail is not visible
                        or (callable(mapped) and not mapped())
                        or any(not possible_lender_matches(
                            row, item, requesting_identity_id=identity_id,
                        ) for row in rows)):
                    # The group call may synchronously change the native
                    # source, account or edition without rotating GUI epochs.
                    # Never add a stale lender action after that reentrancy.
                    return
                from .cross_profile_normal_lender_dialog import append_normal_lender_action
                append_normal_lender_action(
                    panel, item=item, rows=rows, visible=visible, service=service,
                    identity=identity, identity_id=identity_id, account_epoch=account_epoch,
                    source_epoch=source_epoch, route=route,
                )
            except Exception:
                return

        def failed(_error):
            consume()

        try:
            submit("Lecture des profils prêteurs…", work, delivered, failed,
                   priority=TaskPriority.METADATA, kind="detail-cache", origin="automatic")
        except Exception:
            consume()
            return False
        return True
    except Exception:
        return False


__all__ = ["append_possible_lender_summary"]
