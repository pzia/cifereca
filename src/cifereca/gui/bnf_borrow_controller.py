"""Explicit BnF new-loan consent (#2055): GTK orchestration, no provider calls.

Only the Core dispatcher may mutate a BnF loan. The legacy borrow() handler
must never be used for this source. This controller has no provider import
and does not handle cookies, CSRF, receipt HTML, or ADEPT material.
"""
from __future__ import annotations

from copy import copy
from ..models import Account, SearchResult


def present_bnf_borrow(window, item, control, *, Adw, TaskPriority):
    """Offer consent only for a published, Core-admissible BnF action.

    A confirmed borrow request represents the reader's site-checked
    intention to attempt a transaction; the lender decides quota and rights.
    """
    if (getattr(window, "_closed", False)
            or type(item) is not SearchResult
            or item.source_provider != "bnf-pret"
            or not window.service.can_borrow_bnf(item)):
        return
    try:
        session, candidates = window._eligible_borrow_accounts(item)
        if len(candidates) != 1:
            return
        account = candidates[0]
        if type(account) is not Account or account.is_primary is not True:
            return
        if sum(candidate is account for candidate in window.service.accounts) != 1:
            return
    except Exception:
        return
    identity_id = account.identity_id
    identity_gen = getattr(window, "_identity_context_generation", None)
    account_gen = getattr(window, "_account_context_generation", None)
    core_epoch = getattr(window.service, "_account_context_epoch", None)
    selection_id = getattr(window, "_selected_search_id", None)
    detail_gen = getattr(window, "_search_detail_request_generation", None)
    selected = copy(item)
    notice_scope = (
        item.id, item.record_id, item.docbase, item.medium,
        item.source_provider, item.source_credential, item.source_collection,
        item.title,
    )
    account_scope = (
        account.identity_id, account.provider_id, account.credential_id,
        account.unique_id, account.is_primary,
    )
    source_epoch = getattr(session, "_epoch", None)
    if type(core_epoch) is not int or not identity_id:
        return

    def current():
        try:
            return (
                not getattr(window, "_closed", False)
                and window._identity_context_generation == identity_gen
                and window._account_context_generation == account_gen
                and window._selected_search_id == selection_id
                and window._search_detail_request_generation == detail_gen
                and getattr(window.service.current_identity,
                            "identity_id", None) == identity_id
                and window.service._account_context_epoch == core_epoch
                and session.connected is True
                and session._epoch == source_epoch
                and window.service._session_for_source(
                    "bnf-pret", account.credential_id) is session
                and sum(row is account for row in window.service.accounts) == 1
                and (account.identity_id, account.provider_id,
                     account.credential_id, account.unique_id,
                     account.is_primary) == account_scope
                and (item.id, item.record_id, item.docbase, item.medium,
                     item.source_provider, item.source_credential,
                     item.source_collection, item.title) == notice_scope
            )
        except Exception:
            return False

    # Show the selected account alias in the local dialog, never in logs.
    # Collapse control/newline whitespace to avoid breaking native GTK labels.
    account_name = (
        " ".join(account.display_name.split())[:80]
        if type(account.display_name) is str else ""
    ) or "Compte BnF connecté"
    dialog = Adw.MessageDialog.new(
        window, "Emprunter ce livre à la BnF ?",
        f"Compte : {account_name}. "
        f"Livre : {' '.join(item.title.split())[:140] if type(item.title) is str else 'notice sélectionnée'}. "
        "Vérifiez sur pret.bnf.fr que ce livre est indiqué comme empruntable "
        "pour votre compte avant de confirmer. La BnF peut refuser "
        "la transaction selon le quota ou les droits. "
        "Un seul envoi sera effectué. "
        "En cas de résultat incertain, consultez Mes emprunts avant toute "
        "nouvelle démarche. Le téléchargement ACSM reste une action distincte.",
    )
    dialog.add_response("cancel", "Annuler")
    dialog.add_response("borrow", "J’ai vérifié sur BnF et je confirme")
    submitted = False

    def on_response(_dialog, response):
        nonlocal submitted
        if response != "borrow" or submitted:
            return
        if not current() or not window.service.can_borrow_bnf(selected):
            window.show_error(
                "Le compte ou le livre a changé. Sélectionnez de nouveau "
                "la notice avant toute demande.", "Emprunt BnF",
            )
            return
        submitted = True

        def work():
            if not current() or not window.service.can_borrow_bnf(selected):
                return {"status": "blocked", "reason_code": "borrow_scope_changed"}
            return window.service.borrow_bnf_manual(
                selected, expected_identity_id=identity_id,
                confirmed=True, site_checked=True,
                expected_context_epoch=core_epoch,
                expected_account=account,
            )

        def done(result):
            if not current():
                return
            if (type(result) is dict and result.get("status") == "confirmed"
                    and result.get("provider_verified") is True):
                window._activity_result("Prêt BnF actif vérifié dans Mes emprunts")
                window.refresh_loans(True)
            elif type(result) is dict and result.get("status") == "unsupported":
                window.show_error(
                    "L’emprunt BnF n’est pas disponible pour ce compte.",
                    "Emprunt BnF",
                )
            elif type(result) is dict and result.get("status") == "blocked":
                window.show_error(
                    "La demande BnF n’a pas été admise dans ce contexte. "
                    "Aucun nouvel emprunt n’est confirmé.",
                    "Emprunt BnF",
                )
            else:
                window.show_error(
                    "Le résultat de la demande BnF est incertain. "
                    "Vérifiez Mes emprunts ou le portail BnF. "
                    "Cifereca ne répétera pas automatiquement la demande.",
                    "Emprunt BnF",
                )

        def failed(_error):
            if current():
                # Never print arbitrary provider exceptions containing URLs
                # or account-specific server data in the GTK modal.
                window.show_error(
                    "La demande BnF n’a pas pu être vérifiée. Consultez "
                    "Mes emprunts avant de réessayer sur le portail.",
                    "Emprunt BnF",
                )

        window.runner.submit(
            "Demande de prêt BnF…", work, done, failed,
            control=control, priority=TaskPriority.USER_ACTION,
            kind="borrow",
        )

    dialog.connect("response", on_response)
    dialog.present()


__all__ = ["present_bnf_borrow"]
