"""User-confirmed BnF ACSM retrieval and distinct, voluntary ADEPT import.

Both commands run through TaskRunner off GTK. The Core binds the same source,
identity and loan during the GET; this controller never sees cookies or XML.
"""
from __future__ import annotations
from pathlib import Path
from copy import copy
from ..core.diagnostic_bnf_loan_action import BnfLoanActionError, STAGE_CODES, IMPORT_CODES


def bnf_action_error_message(error, *, importing=False):
    """Only closed Core codes reach GTK; never format a provider exception."""
    code = error.code if type(error) is BnfLoanActionError else None
    allowed = IMPORT_CODES if importing else STAGE_CODES
    if type(code) is not str or code not in allowed:
        code = 'bnf_import_failed' if importing else 'bnf_action_failed'
    if code in {'bnf_loan_scope_unproven', 'bnf_loan_not_observed'}:
        message = "Actualisez les prêts du compte BnF de cette identité, puis sélectionnez de nouveau le prêt."
    elif code in {'bnf_auth_required', 'bnf_session_closed'}:
        message = "Reconnectez le compte BnF de cette identité puis actualisez ses prêts. Aucun import ADEPT n'a été lancé."
    elif code == 'bnf_download_redirect_refused':
        message = "Le téléchargement BnF a été redirigé. Vérifiez la session et l'accès à ce prêt sur le portail. Aucun import ADEPT n'a été lancé."
    elif code == 'bnf_access_denied':
        message = "BnF a refusé l'accès au jeton de ce prêt. Vérifiez son accès sur le portail BnF."
    elif code == 'bnf_rate_limited':
        message = "BnF limite temporairement les requêtes. Réessayez plus tard ; aucun import ADEPT n'a été lancé."
    elif code in {'bnf_invalid_loan_token', 'bnf_loan_token_invalid', 'bnf_token_limit', 'bnf_unexpected_content'}:
        message = "La réponse BnF n'est pas un jeton ACSM de prêt valide. Elle n'a pas été transmise à ADEPT."
    elif code in {'bnf_download_storage_failed', 'bnf_local_staging_failed'}:
        message = "Impossible d'enregistrer le jeton dans l'espace privé. Vérifiez l'espace disque et les permissions."
    elif code == 'bnf_acquisition_busy':
        message = "Une acquisition est déjà en cours. Attendez sa fin avant de récupérer le jeton."
    elif importing:
        message = "L'import ADEPT a échoué ; le jeton est conservé pour une reprise. Consultez le motif dans Diagnostics."
    else:
        message = "La récupération du jeton BnF a échoué avant l'import ADEPT. Consultez le motif dans Diagnostics."
    return message + '\nCode : ' + code


def prepare_bnf_acsm(window, loan, control, *, Adw, TaskPriority):
    if getattr(window, "_closed", False) or loan.source_provider != "bnf-pret":
        return
    identity = getattr(window.service, "current_identity", None)
    if identity is None or identity.identity_id != loan.identity_id:
        window.show_error("Choisissez l'identité du prêt avant le téléchargement", "Prêt BnF")
        return
    # Loan is a mutable Core model. Consent must stay attached to the exact
    # selection visible when the confirmation opened, even if the surrounding
    # GTK list refreshes/reuses its original Loan object meanwhile.
    loan = copy(loan)
    identity_id = identity.identity_id
    identity_generation = getattr(window, "_identity_context_generation", None)
    account_generation = getattr(window, "_account_context_generation", None)

    def current():
        return (not getattr(window, "_closed", False)
                and getattr(window, "_identity_context_generation", None) == identity_generation
                and getattr(window, "_account_context_generation", None) == account_generation
                and getattr(getattr(window.service, "current_identity", None),
                            "identity_id", None) == identity_id)

    confirm = Adw.MessageDialog.new(
        window, "Télécharger ce prêt BnF ?",
        "Cifereca vérifiera que ce prêt est toujours présent dans le compte BnF "
        "lié à cette identité, puis récupérera son jeton ACSM par HTTPX. "
        "Cela ne crée aucun nouvel emprunt. L'import ADEPT sera proposé séparément.",
    )
    confirm.add_response("cancel", "Annuler")
    confirm.add_response("get", "Récupérer le jeton")

    def ask_import(path):
        if not current():
            return
        dialog = Adw.MessageDialog.new(
            window, "Importer avec ADEPT ?",
            "Le jeton ACSM a été enregistré dans un espace privé. "
            "L'import utilise le profil ADEPT configuré pour cette identité. "
            "Sans import, aucun EPUB/PDF n'est créé. Le jeton reste disponible "
            "temporairement pour une reprise.",
        )
        dialog.add_response("cancel", "Plus tard")
        dialog.add_response("import", "Importer via ADEPT")
        submitted = False

        def chosen(_dialog, response):
            nonlocal submitted
            if response != "import" or submitted or not current():
                return
            submitted = True

            def import_work():
                if not current():
                    return {"status": "cancelled"}
                # Classify the resulting artifact as a deliberate *manual*
                # ADEPT import, NOT an automatically proven BnF acquisition.
                return window.service.import_bnf_loan_acsm(
                    Path(path), expected_identity_id=identity_id,
                    title=loan.title,
                )

            def imported(result):
                if not current() or result.get("status") == "cancelled":
                    return
                if result.get("status") == "attention_required":
                    candidate = result.get("code")
                    code = candidate if type(candidate) is str and candidate in {
                        "adept_profile_required", "adept_profile_ambiguous",
                    } else "adept_profile_required"
                    window.show_attention(
                        "Jeton conservé : vérifiez le profil ADEPT de cette identité.",
                        code=code,
                    )
                    return
                if result.get("status") not in {"complete", "unchanged"}:
                    window.show_error(
                        "Import non confirmé ; aucun fichier EPUB/PDF n'a été attesté.",
                        "Import BnF",
                    )
                    return
                window._activity_result("ACSM importé via ADEPT" if result.get("status") == "complete"
                                        else "ACSM déjà importé")
                window.refresh_library(False)

            window.runner.submit(
                "Import ADEPT du prêt BnF…", import_work, imported,
                lambda exc: current() and window.show_error(
                    bnf_action_error_message(exc, importing=True), "Import BnF"),
                priority=TaskPriority.USER_ACTION, kind="acsm-import",
            )

        dialog.connect("response", chosen)
        dialog.present()

    submitted = False

    def chosen(_dialog, response):
        nonlocal submitted
        if response != "get" or submitted or not current():
            return
        submitted = True

        def work():
            if not current():
                return {"status": "cancelled"}
            return window.service.stage_bnf_loan_acsm(
                loan, confirmed=True, expected_identity_id=identity_id,
            )

        def staged(result):
            if not current() or result.get("status") != "candidate_acsm":
                return
            ask_import(result["path"])

        window.runner.submit(
            "Récupération du jeton BnF…", work, staged,
            lambda exc: current() and window.show_error(
                bnf_action_error_message(exc), "Téléchargement BnF"),
            control=control, priority=TaskPriority.USER_ACTION, kind="loan-acquisition",
        )

    confirm.connect("response", chosen)
    confirm.present()
