"""#4987: explicit cross-profile lender UI admission; never infer transfer rights.

Opt-in surface only: a caller must already possess fresh, independently
observed, source-scoped borrower AND exact edition evidence. This controller
does not scrape, refresh, invent a borrower, switch identity or download.
The Core executor remains the sole authority to claim/submit a supplier loan.
"""
from __future__ import annotations

from ..models import SearchResult
from ..core.borrow_for_identity_live import ObservedBorrowOffer
from ..core.borrow_for_identity_preview import BorrowForIdentityPreview


def present_cross_profile_lender_choice(
    window, *, expected_item: SearchResult, expected_edition_id: str,
    observations: object, Adw, TaskPriority, control=None,
) -> bool:
    """Present 0/1/N lender choices and separate, explicit loan confirmation.

    Never call provider methods on GTK's main thread. The callback checks
    the whole menu again both before scheduling and inside the worker;
    Core owns the atomic claim, post-POST verification and at-most-once rule.
    """
    if (type(expected_item) is not SearchResult
            or type(expected_edition_id) is not str
            or not 0 < len(expected_edition_id) <= 160
            or type(observations) not in (tuple, list)
            or not 0 < len(observations) <= 64
            or any(type(row) is not ObservedBorrowOffer for row in observations)
            or getattr(window, "_closed", False)
            or getattr(window, "_cross_profile_borrow_pending", None) is not None):
        return False
    service = window.service
    try:
        search_page = window.search_page
        panel = search_page.detail
        rendered = panel._rendered_search_detail
        identity = service.current_identity
        context_epoch = service._account_context_epoch
        source_epoch = service._source_context_epoch
        ui_identity_epoch = window._identity_context_generation
        ui_account_epoch = window._account_context_generation
        selection_id = window._selected_search_id
        detail_epoch = window._search_detail_request_generation
        if (type(rendered) is not tuple or len(rendered) != 2
                or rendered[0] is not expected_item
                or identity is None
                or type(context_epoch) is not int
                or type(source_epoch) is not int
                or context_epoch < 0 or source_epoch < 0):
            return False
        snapshot = tuple(observations)
        preview = service.preview_borrow_for_current_identity(
            expected_edition_id=expected_edition_id, observations=snapshot,
        )
        if (type(preview) is not BorrowForIdentityPreview
                or preview.executable is not False
                or preview.requesting_identity_id != identity.identity_id
                or preview.status not in ("external", "choose")
                or not preview.options
                or len(preview.options) > 64
                or any(option.borrowing_identity_id == identity.identity_id
                       or option.executable is not False
                       or option.edition_id != expected_edition_id
                       for option in preview.options)):
            return False
        execute = service.execute_cross_profile_borrow
        if not callable(execute):
            return False
    except Exception:
        # No inferred right from an incomplete screen or absent Core mutator.
        return False

    def current() -> bool:
        try:
            visible = window.search_page.detail._rendered_search_detail
            return bool(
                not getattr(window, "_closed", False)
                and window.service is service
                and window.search_page is search_page
                and service.current_identity is identity
                and identity.identity_id == preview.requesting_identity_id
                and service._account_context_epoch == context_epoch
                and service._source_context_epoch == source_epoch
                and window._identity_context_generation == ui_identity_epoch
                and window._account_context_generation == ui_account_epoch
                and window._selected_search_id == selection_id
                and window._search_detail_request_generation == detail_epoch
                and window.search_page.detail is panel
                and type(visible) is tuple and len(visible) == 2
                and visible[0] is expected_item
            )
        except Exception:
            return False

    def selection_current(option_id: str) -> bool:
        if not current():
            return False
        try:
            selection = service.revalidate_borrow_lender_choice(
                captured_preview=preview, selected_option_id=option_id,
                expected_edition_id=expected_edition_id,
                observations=snapshot,
            )
            # Read-only. This selection is not consent and is not an
            # executable supplier ticket.
            return bool(
                selection.status == "current"
                and selection.executable is False and current()
            )
        except Exception:
            return False

    if not current():
        return False
    token = object()
    window._cross_profile_borrow_pending = token
    stage = {"value": "choose"}

    def release() -> None:
        stage["value"] = "done"
        if getattr(window, "_cross_profile_borrow_pending", None) is token:
            window._cross_profile_borrow_pending = None

    def submit(option_id: str) -> None:
        if (stage["value"] != "confirm"
                or getattr(window, "_cross_profile_borrow_pending", None)
                    is not token):
            return
        # Mark submitted *before* scheduling: duplicate GTK response
        # callbacks cannot schedule two workers from the same consent.
        stage["value"] = "submitted"
        if not selection_current(option_id):
            release()
            return

        def work():
            if (stage["value"] != "submitted"
                    or getattr(window, "_cross_profile_borrow_pending", None)
                        is not token
                    or not selection_current(option_id)):
                return {"status": "blocked", "reason_code": "lender_scope_revoked"}
            return execute(
                captured_preview=preview, selected_option_id=option_id,
                expected_edition_id=expected_edition_id,
                observations=snapshot, confirmed=True,
            )

        def completed(result):
            release()
            if not current():
                return
            if (type(result) is dict and result.get("status") == "confirmed"
                    and result.get("reason_code") ==
                        "lender_loan_confirmed_transfer_unverified"
                    and result.get("provider_verified") is True
                    and result.get("delivery") == "unavailable"
                    and result.get("transfer") == "blocked"
                    and result.get("retry_borrow") is False):
                window._activity_result(
                    "Prêt confirmé sur le profil prêteur. "
                    "Livraison locale non vérifiée ; envoi possible après placement chez le prêteur et confirmation distincte."
                )
                # #5124: the optional renderer is inert, and accepts only
                # the same visible Search detail plus pinned context epochs.
                # A positive supplier loan never licenses a file transfer.
                try:
                    from ..core.borrow_for_identity_progress import (
                        CrossProfileBorrowProgress,
                    )
                    render = getattr(
                        panel, "show_cross_profile_borrow_progress_detail",
                        None,
                    )
                    if callable(render) and current():
                        render(
                            CrossProfileBorrowProgress(
                                "confirmed", "unavailable", "blocked",
                                "lender_loan_confirmed_transfer_unverified",
                            ),
                            expected_item=expected_item,
                            requesting_identity_id=preview.requesting_identity_id,
                            expected_context_epoch=context_epoch,
                            expected_source_epoch=source_epoch,
                        )
                except Exception:
                    pass
            elif type(result) is dict and result.get("status") == "blocked":
                window.show_error(
                    "Ce choix de prêteur n'est plus admissible. "
                    "Actualisez les offres avant une nouvelle action.",
                    "Emprunt entre profils",
                )
            else:
                window.show_error(
                    "Résultat d'emprunt incertain : vérifiez les prêts du "
                    "profil prêteur. Aucun nouvel essai automatique.",
                    "Emprunt entre profils",
                )

        def failed(_exc):
            release()
            if current():
                window.show_error(
                    "Résultat d'emprunt non vérifié. Consultez les prêts du "
                    "profil prêteur avant toute nouvelle décision.",
                    "Emprunt entre profils",
                )

        try:
            window.runner.submit(
                "Emprunt sur le profil prêteur…", work, completed, failed,
                control=control, priority=TaskPriority.USER_ACTION,
                kind="borrow",
            )
        except Exception:
            release()
            # No exception/credential from the provider enters GTK.
            if current():
                window.show_error(
                    "La demande n'a pas pu être préparée.",
                    "Emprunt entre profils",
                )

    def confirm(option_id: str) -> None:
        if (stage["value"] != "choose"
                or getattr(window, "_cross_profile_borrow_pending", None)
                    is not token):
            return
        stage["value"] = "confirm"
        if not selection_current(option_id):
            release()
            return
        dialog = Adw.MessageDialog.new(
            window, "Confirmer l'emprunt sur le profil prêteur ?",
            "Le prêt sera enregistré sur le compte prêteur choisi. "
            "La copie vers votre profil sera proposée séparément "
            "si le livre est présent localement chez le prêteur.",
        )
        dialog.add_response("cancel", "Annuler")
        dialog.add_response("borrow", "Emprunter sur ce profil")

        def answer(_dialog, response_id):
            if stage["value"] != "confirm":
                return
            if response_id != "borrow":
                release()
                return
            submit(option_id)

        dialog.connect("response", answer)
        dialog.present()

    def selected(_dialog, response_id):
        if stage["value"] != "choose":
            return
        if response_id == "cancel":
            release()
            return
        if (not current() or type(response_id) is not str
                or not response_id.startswith("choice-")):
            release()
            return
        suffix = response_id[len("choice-"):]
        if (not 0 < len(suffix) <= 3
                or not suffix.isascii() or not suffix.isdigit()):
            release()
            return
        index = int(suffix)
        if not 0 <= index < len(preview.options):
            release()
            return
        confirm(preview.options[index].option_id)

    try:
        choose = Adw.MessageDialog.new(
            window, "Choisir le profil prêteur",
            "Sélectionnez explicitement le prêteur de cette édition. "
            "Ce choix ne transfère aucun livre.",
        )
        choose.add_response("cancel", "Annuler")
        for index, option in enumerate(preview.options):
            # Core has already sanitized lender/account labels. Never
            # display private source coordinates or hashed option IDs.
            choose.add_response(f"choice-{index}", option.label)
        choose.connect("response", selected)
        if not current():
            release()
            return False
        choose.present()
        return True
    except Exception:
        release()
        return False


__all__ = ["present_cross_profile_lender_choice"]
