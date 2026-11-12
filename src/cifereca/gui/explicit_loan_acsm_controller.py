"""Two independent explicit actions: loan ACSM staging and voluntary ADEPT import.

GTK owns only consent UI and cancellation. Core owns exact loan/session
admission and private staging; the installed provider owns signed token GET.
No provider name, protocol, HTTP response or private exception is interpreted.
"""
from __future__ import annotations

from copy import copy
from pathlib import Path
from re import fullmatch


# Finite UI categories, not a supplier response or a new entitlement.
_SAFE_REASONS = frozenset({
    "activity_auth_required", "activity_url_refused", "activity_rate_limited",
    "activity_operation_timeout", "activity_unavailable",
    "loan_scope_unproven", "loan_not_observed", "auth_required",
    "session_closed", "download_redirect_refused", "access_denied",
    "rate_limited", "invalid_loan_token", "loan_token_invalid",
    "token_limit", "unexpected_content", "download_storage_failed",
    "local_staging_failed", "acquisition_busy", "import_failed",
})


def action_error_message(error, *, importing=False):
    """Render only a closed reason category, never provider exception text."""
    try:
        code = vars(error).get("code") if isinstance(error, Exception) else None
    except (TypeError, AttributeError):
        code = None
    # Prefix is an opaque provider vocabulary, not a Core routing decision.
    reason = code.split("_", 1)[1] if type(code) is str and "_" in code else ""
    if (type(code) is not str or len(code) > 64
            or fullmatch(r"[a-z][a-z0-9_]{1,63}", code) is None
            or reason not in _SAFE_REASONS):
        code = "loan_acsm_import_failed" if importing else "loan_acsm_action_failed"
        reason = ""
    if reason.startswith("activity_"):
        message = ("La relecture du compte avant le téléchargement a échoué. "
                   "Aucun GET ACSM ni import ADEPT n'a été lancé.")
    elif reason in {"loan_scope_unproven", "loan_not_observed"}:
        message = "Actualisez les prêts de ce compte et sélectionnez de nouveau le prêt."
    elif reason in {"auth_required", "session_closed"}:
        message = "Reconnectez ce compte et actualisez les prêts avant de réessayer."
    elif reason in {"invalid_loan_token", "loan_token_invalid", "unexpected_content", "token_limit"}:
        message = "La réponse n'est pas un jeton ACSM valide et n'a pas été transmise à ADEPT."
    elif reason in {"access_denied", "download_redirect_refused"}:
        message = "L'accès au jeton est refusé ou sa destination est invalide."
    elif reason == "rate_limited":
        message = "La récupération du jeton est temporairement indisponible."
    elif reason in {"download_storage_failed", "local_staging_failed"}:
        message = "Impossible d'enregistrer le jeton dans l'espace privé."
    elif reason == "acquisition_busy":
        message = "Une acquisition est déjà en cours."
    elif importing:
        message = "L'import ADEPT a échoué ; le jeton est conservé pour une reprise."
    else:
        message = "La récupération du jeton ACSM n'a pas été confirmée."
    return message + " Code : " + code


def prepare_explicit_loan_acsm(
    window, loan, control, *, Adw, TaskPriority, stage, importer,
):
    """Confirm GET and ADEPT separately; both remain Core-owned operations."""
    if (getattr(window, "_closed", False)
            or not callable(stage) or not callable(importer)):
        return
    service = window.service
    identity = getattr(service, "current_identity", None)
    if (identity is None or identity.identity_id != loan.identity_id
            or not isinstance(getattr(loan, "source_provider", None), str)
            or not loan.source_provider):
        window.show_error("Choisissez l'identité du prêt avant le téléchargement",
                          "Prêt numérique")
        return
    selected = copy(loan)
    identity_id = identity.identity_id
    identity_generation = getattr(window, "_identity_context_generation", None)
    account_generation = getattr(window, "_account_context_generation", None)
    loan_scope = (
        selected.identity_id, selected.source_provider,
        selected.source_credential, selected.source_account_id,
        selected.id, selected.record_id, selected.state, selected.medium,
    )

    def current():
        return (
            not getattr(window, "_closed", False)
            and window.service is service
            and getattr(window, "_identity_context_generation", None) == identity_generation
            and getattr(window, "_account_context_generation", None) == account_generation
            and getattr(getattr(service, "current_identity", None), "identity_id", None) == identity_id
            and (selected.identity_id, selected.source_provider,
                 selected.source_credential, selected.source_account_id,
                 selected.id, selected.record_id, selected.state, selected.medium) == loan_scope
        )

    first = Adw.MessageDialog.new(
        window, "Télécharger ce jeton ACSM ?",
        "Le prêt sera revérifié sur le compte exact avant de récupérer son "
        "jeton. Aucun nouvel emprunt ne sera créé. L'import ADEPT sera "
        "proposé séparément.",
    )
    first.add_response("cancel", "Annuler")
    first.add_response("get", "Récupérer le jeton")

    def ask_import(path):
        if not current():
            return
        dialog = Adw.MessageDialog.new(
            window, "Importer avec ADEPT ?",
            "Le jeton est conservé dans un espace privé. L'import utilise "
            "le profil ADEPT configuré pour cette identité. Sans accord, "
            "aucun EPUB/PDF n'est créé.",
        )
        dialog.add_response("cancel", "Plus tard")
        dialog.add_response("import", "Importer via ADEPT")
        submitted = False

        def chosen(_dialog, response):
            nonlocal submitted
            if response != "import" or submitted or not current():
                return
            submitted = True

            def work():
                if not current():
                    return {"status": "cancelled"}
                return importer(
                    Path(path), expected_identity_id=identity_id,
                    loan=selected, title=selected.title,
                )

            def done(result):
                if not current() or result.get("status") == "cancelled":
                    return
                if result.get("status") == "attention_required":
                    candidate = result.get("code")
                    code = candidate if type(candidate) is str and candidate in {
                        "adept_profile_required", "adept_profile_ambiguous",
                    } else "adept_profile_required"
                    window.show_attention(
                        "Jeton conservé : vérifiez le profil ADEPT.",
                        code=code,
                    )
                elif result.get("status") not in {"complete", "unchanged"}:
                    window.show_error(
                        "Import non confirmé ; aucun fichier EPUB/PDF n'a été attesté.",
                        "Import ADEPT",
                    )
                else:
                    window._activity_result(
                        "Fichier importé via ADEPT (droit non attesté)"
                        if result["status"] == "complete" else "ACSM déjà importé",
                    )
                    window.refresh_library(False)

            window.runner.submit(
                "Import ADEPT du prêt…", work, done,
                lambda error: current() and window.show_error(
                    action_error_message(error, importing=True), "Import ADEPT",
                ),
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
            return stage(selected, confirmed=True, expected_identity_id=identity_id)

        def done(result):
            if current() and type(result) is dict and result.get("status") == "candidate_acsm":
                ask_import(result["path"])

        window.runner.submit(
            "Récupération du jeton ACSM…", work, done,
            lambda error: current() and window.show_error(
                action_error_message(error), "Téléchargement ACSM",
            ),
            control=control, priority=TaskPriority.USER_ACTION,
            kind="loan-acquisition",
        )

    first.connect("response", chosen)
    first.present()
