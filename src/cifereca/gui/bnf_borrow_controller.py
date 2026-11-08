"""Single-click BnF/Paris manual borrowing; all admission belongs to Core.

The historical BnF module name is kept for existing callers. Neither source
may use the legacy borrow() path. No provider protocol or ADEPT runs here.
"""
from __future__ import annotations

from copy import copy
from ..models import Account, SearchResult


def _present_manual_borrow(window, item, control, *, TaskPriority,
                           provider_id, label, available, dispatch, expected_account=None):
    """Submit only a user-clicked, published and Core-admissible loan action.

    No second dialog or separate website attestation is required (#3121).
    The provider still decides quota and rights; Core guards one exact POST.
    """
    if (getattr(window, "_closed", False)
            or type(item) is not SearchResult
            or item.source_provider != provider_id
            or not available(item)):
        return
    try:
        session, candidates = window._eligible_borrow_accounts(item)
        if len(candidates) != 1:
            return
        account = candidates[0]
        if (type(account) is not Account or account.is_primary is not True
                or (expected_account is not None and account is not expected_account)):
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
                and getattr(session, "_epoch", None) == source_epoch
                and window.service._session_for_source(
                    provider_id, account.credential_id) is session
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

    # The explicit click on "Emprunter" is the sole user gesture. Avoid a
    # second confirmation dialog and prevent repeated queued requests even if
    # a click arrives before TaskRunner can disable the control.
    if getattr(window, "_manual_borrow_pending", None) is not None:
        return
    token = object()
    window._manual_borrow_pending = token

    def release_pending():
        if getattr(window, "_manual_borrow_pending", None) is token:
            window._manual_borrow_pending = None

    def submit_once():
        if not current() or not available(selected):
            release_pending()
            window.show_error(
                "Le compte ou le livre a changé. Sélectionnez de nouveau "
                "la notice avant toute demande.", f"Emprunt {label}",
            )
            return

        def work():
            if not current() or not available(selected):
                return {"status": "blocked", "reason_code": "borrow_scope_changed"}
            return dispatch(
                selected, expected_identity_id=identity_id,
                confirmed=True,
                expected_context_epoch=core_epoch,
                expected_account=account,
            )

        def done(result):
            release_pending()
            if not current():
                return
            if (type(result) is dict and result.get("status") == "confirmed"
                    and result.get("provider_verified") is True):
                window._activity_result(f"Prêt {label} actif vérifié dans Mes emprunts")
                window.refresh_loans(True)
                if provider_id == "bnf-pret" and current():
                    # Keep holdings and avoid a fresh provider GET: reproject
                    # the exact visible notice using current Core admission.
                    panel = getattr(getattr(window, "search_page", None), "detail", None)
                    refresh = getattr(panel, "refresh_confirmed_borrow_detail", None)
                    if callable(refresh):
                        refresh(item)
            elif type(result) is dict and result.get("status") == "unsupported":
                window.show_error(
                    f"L’emprunt {label} n’est pas disponible pour ce compte.",
                    f"Emprunt {label}",
                )
            elif (type(result) is dict and result.get("status") == "blocked"
                    and result.get("reason_code") == "borrow_not_submitted"):
                window.show_error(
                    f"Aucune demande de prêt n’a été envoyée à {label} : "
                    "la fiche ou l'action n'est plus exploitable. "
                    "Vérifiez le livre sur le portail avant une nouvelle "
                    "demande volontaire.",
                    f"Emprunt {label}",
                )
            elif type(result) is dict and result.get("status") == "blocked":
                window.show_error(
                    f"La demande {label} n’a pas été admise dans ce contexte. "
                    "Aucun nouvel emprunt n’est confirmé.",
                    f"Emprunt {label}",
                )
            else:
                window.show_error(
                    f"Le résultat de la demande {label} est incertain. "
                    f"Vérifiez Mes emprunts ou le portail {label}. "
                    "Cifereca ne répétera pas automatiquement la demande.",
                    f"Emprunt {label}",
                )

        def failed(_error):
            release_pending()
            if current():
                # Never print arbitrary provider exceptions containing URLs
                # or account-specific server data in the GTK modal.
                window.show_error(
                    f"La demande {label} n’a pas pu être vérifiée. Consultez "
                    "Mes emprunts avant de réessayer sur le portail.",
                    f"Emprunt {label}",
                )

        try:
            window.runner.submit(
                f"Demande de prêt {label}…", work, done, failed,
                control=control, priority=TaskPriority.USER_ACTION,
                kind="borrow",
            )
        except Exception:
            release_pending()
            raise

    submit_once()


def present_bnf_borrow(window, item, control, *, Adw, TaskPriority):
    _present_manual_borrow(
        window, item, control, TaskPriority=TaskPriority,
        provider_id="bnf-pret", label="BnF",
        available=window.service.can_borrow_bnf,
        dispatch=window.service.borrow_bnf_manual,
    )


def present_paris_borrow(window, item, control, *, Adw, TaskPriority, account=None):
    _present_manual_borrow(
        window, item, control, TaskPriority=TaskPriority,
        provider_id="paris", label="Paris",
        available=window.service.can_borrow_paris,
        dispatch=window.service.borrow_paris_manual, expected_account=account,
    )


def present_paris_borrow_verification(window, item, control, *, TaskPriority):
    """Explicit read-only follow-up: no borrow dispatch or claim mutation.

    The controller never sees or displays the persisted operation_id. It
    preserves the same exact notice/account/UI generation constraints as the
    original borrower action, including across queued GTK callbacks.
    """
    if (getattr(window, "_closed", False)
            or type(item) is not SearchResult
            or item.source_provider != "paris"
            or item.docbase != "DILICOM"
            or item.medium != "digital"
            or not window.service.paris_borrow_followup_available(item)
            or getattr(window, "_manual_borrow_pending", None) is not None
            or getattr(window, "_paris_borrow_followup_pending", None) is not None):
        return
    try:
        session, candidates = window._eligible_borrow_accounts(item)
        if len(candidates) != 1:
            return
        account = candidates[0]
        if (type(account) is not Account or account.is_primary is not True
                or account.unique_id != ""
                or sum(a is account for a in window.service.accounts) != 1):
            return
    except Exception:
        return
    identity_id = account.identity_id
    identity_gen = getattr(window, "_identity_context_generation", None)
    account_gen = getattr(window, "_account_context_generation", None)
    core_epoch = getattr(window.service, "_account_context_epoch", None)
    selection = getattr(window, "_selected_search_id", None)
    detail_gen = getattr(window, "_search_detail_request_generation", None)
    source_epoch = getattr(session, "_epoch", None)
    original_scope = (
        item.id, item.record_id, item.docbase, item.medium,
        item.source_provider, item.source_credential,
        item.source_collection, item.title,
    )
    account_scope = (
        account.identity_id, account.provider_id, account.credential_id,
        account.unique_id, account.is_primary,
    )
    if type(core_epoch) is not int or not identity_id:
        return
    selected = copy(item)

    def current() -> bool:
        try:
            return (
                not getattr(window, "_closed", False)
                and window._identity_context_generation == identity_gen
                and window._account_context_generation == account_gen
                and window.service._account_context_epoch == core_epoch
                and window._selected_search_id == selection
                and window._search_detail_request_generation == detail_gen
                and getattr(window.service.current_identity, "identity_id", None) == identity_id
                and session.connected is True
                and getattr(session, "_epoch", None) == source_epoch
                and window.service._session_for_source(
                    "paris", account.credential_id,
                ) is session
                and sum(a is account for a in window.service.accounts) == 1
                and (account.identity_id, account.provider_id,
                     account.credential_id, account.unique_id,
                     account.is_primary) == account_scope
                and (item.id, item.record_id, item.docbase, item.medium,
                     item.source_provider, item.source_credential,
                     item.source_collection, item.title) == original_scope
                and window.service.paris_borrow_followup_available(selected)
            )
        except Exception:
            return False

    if not current():
        return
    token = object()
    window._paris_borrow_followup_pending = token

    def release():
        if getattr(window, "_paris_borrow_followup_pending", None) is token:
            window._paris_borrow_followup_pending = None

    def work():
        if not current():
            return {"status": "blocked", "retry_borrow": False, "mutated": False}
        return window.service.verify_paris_borrow_followup(selected)

    def done(result):
        release()
        if not current():
            return
        if (type(result) is dict and result.get("status") == "observed_current_loan"
                and result.get("retry_borrow") is False
                and result.get("mutated") is False):
            window._activity_result(
                "Prêt Paris désormais observé ; demande initiale non rejouée"
            )
            window.refresh_loans(True)
        else:
            window.show_error(
                "Le prêt Paris n’est pas encore confirmé par une observation "
                "complète de ce compte. Ne réempruntez pas : actualisez "
                "ultérieurement vos prêts.",
                "Vérification de prêt Paris",
            )

    def failed(_error):
        release()
        if current():
            window.show_error(
                "La vérification du prêt Paris est indisponible. "
                "Aucune nouvelle demande n’a été envoyée ; ne réempruntez pas.",
                "Vérification de prêt Paris",
            )

    try:
        window.runner.submit(
            "Vérification du prêt Paris…", work, done, failed,
            control=control, priority=TaskPriority.USER_ACTION,
            kind="borrow-verification", origin="user",
        )
    except Exception:
        release()
        raise


__all__ = ["present_bnf_borrow", "present_paris_borrow",
           "present_paris_borrow_verification"]
