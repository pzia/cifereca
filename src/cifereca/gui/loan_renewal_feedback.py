"""Read-only GTK projection of a provider loan-renewal attempt.

RenewLoans request acceptance is never evidence of a changed due date.
No dynamic provider/loan data or exception body reaches GTK.
"""
from __future__ import annotations
from typing import Any

_KNOWN_CODES = frozenset({
    "renewal_confirmation_required", "loan_scope_unverified",
    "loan_scope_changed", "loan_permission_unverified",
    "loan_read_unavailable", "renewal_checkpoint_unavailable",
    "renewal_previous_submission", "renewal_previous_attempt_unverified",
    "renewal_provider_response_unverified", "renewal_provider_submitted",
    "loan_action_busy",
})


def loan_renewal_feedback(outcome: Any) -> tuple[str, str, str]:
    """Return (kind, text, closed code), never arbitrary source strings."""
    if type(outcome) is not dict:
        return (
            "attention", "Le résultat de la demande de prolongation est inconnu. "
            "Actualisez les prêts avant toute nouvelle action.",
            "renewal_provider_response_unverified",
        )
    status = outcome.get("status")
    code = outcome.get("code")
    if type(code) is not str or code not in _KNOWN_CODES:
        code = "renewal_provider_response_unverified"
    if (status == "submitted"
            and outcome.get("provider_request_accepted") is True
            and outcome.get("provider_verified") is False):
        return (
            "submitted",
            "Demande de prolongation transmise. La nouvelle échéance "
            "n'est pas encore confirmée par le fournisseur.",
            "renewal_provider_submitted",
        )
    if status == "verification_unavailable":
        return (
            "attention",
            "La réponse de prolongation n'a pas pu être vérifiée. "
            "Actualisez les prêts ; ne répétez pas la demande.",
            code,
        )
    if status == "blocked" and code == "renewal_confirmation_required":
        return (
            "attention",
            "La prolongation exige une confirmation explicite.",
            code,
        )
    if status == "blocked" and code == "loan_action_busy":
        return (
            "attention",
            "Une action sur ce prêt est déjà en cours. "
            "Actualisez les prêts avant une nouvelle demande.",
            code,
        )
    return (
        "attention",
        "La prolongation n'a pas été engagée : vérifiez le compte "
        "emprunteur et actualisez les prêts.",
        code,
    )


__all__ = ["loan_renewal_feedback"]
