"""Closed, non-authorizing ADEPT guidance on the Diagnostics page (#979).

Use only Core-published structured machine codes, not the event message,
technical details, vendor XML, stderr, exit status, paths or titles. The
runtime's upstream attestation is independent: these labels do not prove a
license state, recommend retrying an ACSM or trigger any operation.
"""
from __future__ import annotations

# The mapping is deliberately independent of the vendor/runtime's error
# message; never interpolate event fields into any value below.
_ADVICE = {
    "invalid_acsm": (
        "Jeton ACSM signalé invalide. Vérifiez le prêt et le jeton enregistré "
        "avant toute nouvelle importation."
    ),
    "profile_not_found": (
        "Profil ADEPT introuvable. Vérifiez le profil associé à cette identité "
        "dans Paramètres."
    ),
    "adept_profile_required": (
        "Profil ADEPT requis. Associez explicitement le profil d'origine "
        "à cette identité avant toute opération."
    ),
    "adept_profile_error": (
        "Erreur de profil ADEPT. Vérifiez la configuration du profil "
        "d'origine ; aucune autre identité n'est sélectionnée automatiquement."
    ),
    "adept_profile_incompatible": (
        "Profil ADEPT incompatible pour cette opération. Vérifiez la liaison "
        "et le profil ayant initialement activé la licence."
    ),
    "adept_license_already_fulfilled_by_another_user": (
        "ADEPT signale une licence déjà activée par une autre identité Adobe. "
        "Vérifiez l'identité d'origine ; ne réessayez pas automatiquement "
        "l'activation du même jeton."
    ),
    "adept_error": (
        "Échec ADEPT générique. La cause Adobe précise n'est pas attestée. "
        "Consultez l'opération et le profil configuré avant toute nouvelle démarche."
    ),
    "kobo_adept_timeout": (
        "Délai ADEPT dépassé. Une activation ACSM peut avoir consommé le jeton ; "
        "vérifiez les prêts et fichiers locaux avant toute nouvelle tentative."
    ),
    "kobo_adept_missing": (
        "Exécutable Kobo-ADEPT introuvable. Vérifiez son installation "
        "et sa configuration avant toute opération."
    ),
    "kobo_adept_invalid_response": (
        "Réponse Kobo-ADEPT incohérente. Aucun succès d'import n'est "
        "attesté ; consultez les diagnostics de l'exécution."
    ),
}


def adept_guidance_for_event(row: object) -> str:
    """Render fixed advice ONLY for a bounded Core ADEPT v1 failure envelope.

    The event detail is already published through Core's diagnostics broker.
    Recheck scope here because legacy/third-party journal records are also
    accepted by the GUI. Never infer cause from exit=10 or raw strings.
    """
    if type(row) is not dict or row.get("source") not in {
        "core_events", "app_log", "journal",
    }:
        return ""
    detail = row.get("detail")
    if type(detail) is not dict or len(detail) > 20:
        return ""
    allowed = {
        "schema_version", "plugin_id", "operation_kind", "operation", "phase",
        "status", "code", "retryable", "task_ref", "validation_reason",
        "duration_ms", "stdout_bytes", "stderr_bytes", "returncode",
        "caller_category", "read_scope_ref",
    }
    if set(detail) - allowed:
        return ""
    if (type(detail.get("schema_version")) is not int
            or detail["schema_version"] != 1
            or any(type(detail.get(key)) is not str
                   for key in ("plugin_id", "operation_kind", "phase", "status"))
            or detail["plugin_id"] != "runtime.kobo-adept"
            or detail["operation_kind"] != "kobo_adept"
            or detail["phase"] != "completed"
            or detail["status"] != "failed"
            or ("retryable" in detail and type(detail["retryable"]) is not bool)):
        return ""
    operation = detail.get("operation")
    if type(operation) is not str or operation not in {
        "download request", "profile list", "profile show", "loans list",
        "loans expired", "contract", "capabilities", "status", "other",
        "unknown",
    }:
        # 'acsm return' is retired (#4048), never suggest restoring it.
        return ""
    code = detail.get("code")
    if type(code) is not str:
        return ""
    # The license/ACSM-specific guidance applies only to the runtime's
    # request family. An unrelated profile/loan probe's failure is not
    # evidence that an ACSM activation or another Adobe user was involved.
    if code in {
        "invalid_acsm", "adept_license_already_fulfilled_by_another_user",
        "kobo_adept_timeout",
    } and operation != "download request":
        return ""
    advice = _ADVICE.get(code)
    if advice is None:
        return ""
    # Retryability is intentionally NOT used in the action text. Even a
    # native True flag does not attest that a one-use ACSM is safe to replay.
    return "ADEPT · " + advice + " Code : " + code
