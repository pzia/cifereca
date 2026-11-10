"""#4988: offer the *separate* local send after an exact lender loan.

The supplier loan remains owned by A. This optional GTK follow-up checks
whether an existing A-owned local placement has appeared, without provider
I/O or a profile switch; only a second explicit consent invokes native
'Envoyer à'. The Core revalidates loan, placement, origin and requester.
"""
from __future__ import annotations

from ..models import Account, SearchResult
from ..core.borrow_for_identity_placement_resolution import LenderLocalPlacement


def offer_cross_profile_local_send(
    window, *, borrowing_account: Account, exact_notice: SearchResult,
    expected_item: SearchResult, Adw, TaskPriority, control=None,
) -> bool:
    """Read-only asynchronous placement check, then explicit send dialog."""
    if (type(borrowing_account) is not Account
            or type(exact_notice) is not SearchResult
            or type(expected_item) is not SearchResult
            or getattr(window, "_closed", False)
            or getattr(window, "_cross_profile_send_pending", None) is not None):
        return False
    try:
        service = window.service
        search_page = window.search_page
        panel = search_page.detail
        visible = panel._rendered_search_detail
        identity = service.current_identity
        account_epoch = service._account_context_epoch
        source_epoch = service._source_context_epoch
        identity_epoch = window._identity_context_generation
        ui_account_epoch = window._account_context_generation
        selection = window._selected_search_id
        detail_epoch = window._search_detail_request_generation
        requester = identity.identity_id
        if (type(visible) is not tuple or len(visible) != 2
                or visible[0] is not expected_item
                or type(requester) is not str
                or requester == borrowing_account.identity_id
                or type(account_epoch) is not int
                or type(source_epoch) is not int
                or not callable(service.resolve_cross_profile_lender_placement)
                or not callable(service.send_confirmed_lender_local_copy)):
            return False
    except Exception:
        return False

    def current() -> bool:
        try:
            shown = window.search_page.detail._rendered_search_detail
            return bool(
                not getattr(window, "_closed", False)
                and window.service is service
                and window.search_page is search_page
                and window.search_page.detail is panel
                and service.current_identity is identity
                and identity.identity_id == requester
                and service._account_context_epoch == account_epoch
                and service._source_context_epoch == source_epoch
                and window._identity_context_generation == identity_epoch
                and window._account_context_generation == ui_account_epoch
                and window._selected_search_id == selection
                and window._search_detail_request_generation == detail_epoch
                and type(shown) is tuple and len(shown) == 2
                and shown[0] is expected_item
            )
        except Exception:
            return False

    if not current():
        return False
    token = object()
    window._cross_profile_send_pending = token
    state = {"stage": "checking"}

    def owns_token() -> bool:
        return getattr(window, "_cross_profile_send_pending", None) is token

    def release() -> None:
        state["stage"] = "done"
        if owns_token():
            window._cross_profile_send_pending = None

    def send() -> None:
        if state["stage"] != "confirm" or not owns_token():
            return
        state["stage"] = "submitted"
        if not current():
            release()
            return

        def work():
            if not owns_token() or not current() or state["stage"] != "submitted":
                return {"status": "blocked"}
            return service.send_confirmed_lender_local_copy(
                requesting_identity_id=requester,
                borrowing_account=borrowing_account,
                exact_notice=exact_notice, confirmed=True,
            )

        def completed(outcome):
            release()
            if not current():
                return
            if (type(outcome) is dict
                    and outcome.get("status") == "complete"
                    and outcome.get("loan") == "confirmed"
                    and outcome.get("delivery") == "present"
                    and outcome.get("transfer") == "completed"
                    and outcome.get("reason_code") ==
                        "independent_local_copy_completed"
                    and outcome.get("retry_borrow") is False):
                window._activity_result(
                    "Copie indépendante envoyée vers votre profil."
                )
            else:
                window.show_error(
                    "Le prêt reste sur le profil prêteur. "
                    "La copie locale n'a pas pu être confirmée ; "
                    "vérifiez le placement avant de retenter l'envoi.",
                    "Envoi entre profils",
                )

        def failed(_exc):
            release()
            if current():
                window.show_error(
                    "Prêt conservé sur le profil prêteur. "
                    "Envoi local non confirmé.",
                    "Envoi entre profils",
                )

        try:
            window.runner.submit(
                "Envoi du livre au profil demandeur…",
                work, completed, failed,
                control=control, priority=TaskPriority.USER_ACTION,
                kind="borrow",
            )
        except Exception:
            release()
            if current():
                window.show_error("Envoi local indisponible.", "Envoi entre profils")

    def ready(result):
        if not owns_token() or state["stage"] != "checking":
            return
        if not current():
            release()
            return
        if (type(result) is not LenderLocalPlacement
                or result.status != "present"
                or result.loan != "confirmed"
                or result.executable is not False
                or result.retry_borrow is not False
                or type(result._placement_id) is not str
                or not result._placement_id):
            # The loan may have completed before its physical delivery.
            # Never offer a copy from the supplier receipt alone.
            release()
            return
        state["stage"] = "confirm"
        try:
            dialog = Adw.MessageDialog.new(
                window, "Envoyer une copie vers votre profil ?",
                "Le livre est présent localement sur le profil prêteur. "
                "Créer une copie indépendante via « Envoyer à » ? "
                "Le prêt et les droits fournisseur restent chez le prêteur.",
            )
            dialog.add_response("cancel", "Pas maintenant")
            dialog.add_response("send", "Envoyer une copie")

            def answered(_dialog, response_id):
                if state["stage"] != "confirm":
                    return
                if response_id != "send":
                    release()
                else:
                    send()

            dialog.connect("response", answered)
            if not current() or not owns_token():
                release()
                return
            dialog.present()
        except Exception:
            release()

    def missing(_exc):
        release()

    def check():
        if state["stage"] != "checking" or not current() or not owns_token():
            return None
        return service.resolve_cross_profile_lender_placement(
            requesting_identity_id=requester,
            borrowing_account=borrowing_account,
            exact_notice=exact_notice,
        )

    try:
        window.runner.submit(
            "Vérification de la livraison locale…",
            check, ready, missing,
            control=control, priority=TaskPriority.USER_ACTION,
            kind="borrow",
        )
        return True
    except Exception:
        release()
        return False


def offer_selected_lender_local_send(
    window, *, captured_preview, selected_option_id: str,
    expected_edition_id: str, observations: object,
    expected_item: SearchResult, Adw, TaskPriority, control=None,
) -> bool:
    """Use the already-selected exact A source, without re-running loan POST."""
    from .cross_profile_local_send_scope import selected_lender_local_scope

    try:
        selected = selected_lender_local_scope(
            window.service, captured_preview=captured_preview,
            selected_option_id=selected_option_id,
            expected_edition_id=expected_edition_id,
            observations=observations,
        )
        if selected is None or selected.executable is not False:
            return False
        return offer_cross_profile_local_send(
            window, borrowing_account=selected.account,
            exact_notice=selected.notice,
            expected_item=expected_item,
            Adw=Adw, TaskPriority=TaskPriority, control=control,
        )
    except Exception:
        return False


__all__ = ["offer_cross_profile_local_send", "offer_selected_lender_local_send"]
