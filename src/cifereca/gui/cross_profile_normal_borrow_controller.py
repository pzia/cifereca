"""#4988: explicit 1A lender choice and normal supplier borrowing in GTK.

Local discovery is not an eligibility/transfer right. Only a separate human
confirmation may queue the Core's one-shot manual attempt on lender A.
No provider access in GTK, no implicit profile switch or copy to B.
"""
from __future__ import annotations

from ..models import SearchResult
from ..core.borrow_for_identity_discovery import PossibleBorrowLender


def present_normal_cross_profile_lender_choice(
    window, *, expected_item: SearchResult,
    Adw, TaskPriority, control=None,
) -> bool:
    """Choose A explicitly, confirm B→A intent, then queue one guarded POST."""
    if (type(expected_item) is not SearchResult
            or getattr(window, "_closed", False)
            or getattr(window, "_cross_profile_borrow_pending", None) is not None):
        return False
    try:
        service = window.service
        page = window.search_page
        panel = page.detail
        rendered = panel._rendered_search_detail
        identity = service.current_identity
        account_epoch = service._account_context_epoch
        source_epoch = service._source_context_epoch
        ui_identity_epoch = window._identity_context_generation
        ui_account_epoch = window._account_context_generation
        selection_id = window._selected_search_id
        detail_epoch = window._search_detail_request_generation
        reader = service.possible_borrow_lenders_for_current_identity
        execute = service.execute_normal_lender_borrow_for_current_identity
        if (type(rendered) is not tuple or len(rendered) != 2
                or rendered[0] is not expected_item
                or identity is None
                or type(identity.identity_id) is not str
                or not identity.identity_id
                or type(account_epoch) is not int or account_epoch < 0
                or type(source_epoch) is not int or source_epoch < 0
                or not callable(reader) or not callable(execute)):
            return False
        candidates = reader(exact_notice=expected_item)
        if (type(candidates) is not tuple
                or not 0 < len(candidates) <= 64
                or any(type(row) is not PossibleBorrowLender
                       or row.executable is not False
                       or row.supplier_eligibility != "not_verified"
                       or row._notice is not expected_item
                       or row.lender_identity_id == identity.identity_id
                       for row in candidates)):
            return False
    except Exception:
        return False

    def worker_current() -> bool:
        """Only scalar/MainWindow state; never dereference a GTK detail widget."""
        try:
            return bool(
                not getattr(window, "_closed", False)
                and window.service is service
                and window.search_page is page
                and service.current_identity is identity
                and service._account_context_epoch == account_epoch
                and service._source_context_epoch == source_epoch
                and window._identity_context_generation == ui_identity_epoch
                and window._account_context_generation == ui_account_epoch
                and window._selected_search_id == selection_id
                and window._search_detail_request_generation == detail_epoch
            )
        except Exception:
            return False

    def ui_current() -> bool:
        if not worker_current():
            return False
        try:
            visible = page.detail._rendered_search_detail
            return (
                page.detail is panel
                and type(visible) is tuple
                and len(visible) == 2
                and visible[0] is expected_item
            )
        except Exception:
            return False

    def selection_current(index: int, *, worker: bool = False) -> bool:
        if (type(index) is not int or not 0 <= index < len(candidates)
                or not (worker_current() if worker else ui_current())):
            return False
        try:
            # Re-discover local A on every choice. No previous candidate is
            # an eligibility attestation or a durable authorization.
            current = reader(exact_notice=expected_item)
            if (type(current) is not tuple or len(current) != len(candidates)):
                return False
            return bool(
                all(
                    type(new) is PossibleBorrowLender
                    and new._account is old._account
                    and new._notice is expected_item
                    and new._captured_fields == old._captured_fields
                    and new.lender_identity_id == old.lender_identity_id
                    and new.label == old.label
                    and new.executable is False
                    and new.supplier_eligibility == "not_verified"
                    for old, new in zip(candidates, current)
                ) and (worker_current() if worker else ui_current())
            )
        except Exception:
            return False

    if not ui_current():
        return False
    token = object()
    window._cross_profile_borrow_pending = token
    stage = {"value": "choose"}

    def release() -> None:
        stage["value"] = "done"
        if getattr(window, "_cross_profile_borrow_pending", None) is token:
            window._cross_profile_borrow_pending = None

    def submit(index: int) -> None:
        if (stage["value"] != "confirm"
                or getattr(window, "_cross_profile_borrow_pending", None)
                    is not token):
            return
        stage["value"] = "submitted"
        if not selection_current(index):
            release()
            return

        def work():
            if (stage["value"] != "submitted"
                    or getattr(window, "_cross_profile_borrow_pending", None)
                        is not token
                    or not selection_current(index, worker=True)):
                return {"status": "blocked", "reason_code": "lender_scope_revoked"}
            return execute(
                captured_candidate=candidates[index],
                exact_notice=expected_item,
                expected_requesting_identity_id=identity.identity_id,
                expected_requesting_identity=identity,
                expected_context_epoch=account_epoch,
                expected_source_epoch=source_epoch,
                confirmed=True,
            )

        def completed(result):
            release()
            if not ui_current():
                return
            if (type(result) is dict
                    and result.get("status") == "confirmed"
                    and result.get("reason_code") ==
                        "lender_loan_confirmed_transfer_unverified"
                    and result.get("provider_verified") is True
                    and result.get("delivery") == "unavailable"
                    and result.get("transfer") == "blocked"
                    and result.get("retry_borrow") is False):
                window._activity_result(
                    "Prêt confirmé sur le profil prêteur. "
                    "Livraison non vérifiée ; transfert et copie séparés, "
                    "sans nouvel emprunt automatique."
                )
                try:
                    from ..core.borrow_for_identity_progress import (
                        CrossProfileBorrowProgress,
                    )
                    render = getattr(
                        panel, "show_cross_profile_borrow_progress_detail", None,
                    )
                    if callable(render) and ui_current():
                        render(
                            CrossProfileBorrowProgress(
                                "confirmed", "unavailable", "blocked",
                                "lender_loan_confirmed_transfer_unverified",
                            ),
                            expected_item=expected_item,
                            requesting_identity_id=identity.identity_id,
                            expected_context_epoch=account_epoch,
                            expected_source_epoch=source_epoch,
                        )
                except Exception:
                    pass
            elif type(result) is dict and result.get("status") == "blocked":
                window.show_error(
                    "Le choix de prêteur n'est plus admissible. "
                    "Actualisez les offres avant toute nouvelle tentative.",
                    "Emprunt avec un autre profil",
                )
            else:
                window.show_error(
                    "Résultat du prêt incertain. Vérifiez les emprunts du "
                    "profil prêteur ; aucun nouvel essai automatique.",
                    "Emprunt avec un autre profil",
                )

        def failed(_error):
            release()
            if ui_current():
                window.show_error(
                    "Résultat du prêt non vérifié. Consultez les emprunts "
                    "du prêteur avant toute nouvelle décision.",
                    "Emprunt avec un autre profil",
                )

        try:
            window.runner.submit(
                "Emprunt sur le profil prêteur…", work, completed, failed,
                control=control, priority=TaskPriority.USER_ACTION,
                kind="borrow",
            )
        except Exception:
            release()
            if ui_current():
                window.show_error(
                    "La demande n'a pas pu être préparée.",
                    "Emprunt avec un autre profil",
                )

    def confirm(index: int) -> None:
        if (stage["value"] != "choose"
                or getattr(window, "_cross_profile_borrow_pending", None)
                    is not token):
            return
        stage["value"] = "confirm"
        if not selection_current(index):
            release()
            return
        dialog = Adw.MessageDialog.new(
            window, "Confirmer le prêt sur le profil prêteur ?",
            "L'emprunt sera tenté normalement sur le profil choisi. "
            "Le fournisseur peut le refuser. Le livre restera chez ce profil. "
            "Une éventuelle copie vers votre profil exigera une livraison "
            "locale autorisée et une confirmation distincte.",
        )
        dialog.add_response("cancel", "Annuler")
        dialog.add_response("borrow", "Tenter l'emprunt sur ce profil")

        def answered(_dialog, response_id):
            if stage["value"] != "confirm":
                return
            if response_id != "borrow":
                release()
                return
            submit(index)

        dialog.connect("response", answered)
        dialog.present()

    def selected(_dialog, response_id):
        if stage["value"] != "choose":
            return
        if response_id == "cancel":
            release()
            return
        if (not ui_current() or type(response_id) is not str
                or not response_id.startswith("choice-")):
            release()
            return
        suffix = response_id[len("choice-"):]
        if (not 0 < len(suffix) <= 3
                or not suffix.isascii() or not suffix.isdigit()):
            release()
            return
        index = int(suffix)
        if not 0 <= index < len(candidates):
            release()
            return
        confirm(index)

    try:
        choose = Adw.MessageDialog.new(
            window, "Choisir le profil prêteur",
            "Profils localement compatibles avec cette notice. "
            "Offre et quota fournisseur non vérifiés ; aucun prêt encore lancé.",
        )
        choose.add_response("cancel", "Annuler")
        for index, row in enumerate(candidates):
            choose.add_response(f"choice-{index}", row.label)
        choose.connect("response", selected)
        if not ui_current():
            release()
            return False
        choose.present()
        return True
    except Exception:
        release()
        return False


__all__ = ["present_normal_cross_profile_lender_choice"]
