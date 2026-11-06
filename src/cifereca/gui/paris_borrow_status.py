"""Read-only Paris account eligibility wording for Search details (#4673).

Only closed Core reason codes are consumed; never render provider secrets.
Catalogue availability is documentary, not a lender decision.
"""
from __future__ import annotations

_MESSAGES = {
    "identity_not_selected":
        "Sélectionnez le profil lecteur pour vérifier l’admission locale.",
    "edition_or_source_unqualified":
        "Cette édition ou source Paris n’est pas qualifiée pour cette action.",
    "primary_paris_connection_mismatch_or_inactive":
        "La connexion Paris principale de ce profil ne correspond pas à cette notice, "
        "ou n’est pas active.",
    "local_admission_unavailable":
        "L’admission locale n’est pas satisfaite (compte, session, capacité ou tentative précédente).",
    "local_action_available_provider_unknown":
        "L’action locale est proposée ; le fournisseur décide lors de la demande explicite.",
}

ACCOUNT_UNKNOWN = "Éligibilité du compte Paris non vérifiée auprès du fournisseur."


def paris_borrow_status_message(diagnostic: object) -> str:
    """Convert a single closed reason into safe and qualified UI copy."""
    if type(diagnostic) is not dict:
        return ACCOUNT_UNKNOWN
    reason = diagnostic.get("reason_code")
    if type(reason) is not str:
        return ACCOUNT_UNKNOWN
    explanation = _MESSAGES.get(reason)
    if explanation is None:
        return ACCOUNT_UNKNOWN
    # Never trust the diagnostic to assert a remote eligibility claim.
    return explanation + " " + ACCOUNT_UNKNOWN
