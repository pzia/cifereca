"""P1 #4082 — closed, inert BnF previous-borrow status in book detail.

A past checkpoint is not a current loan or authorization to submit a POST.
Unknown or malformed observations never expose arbitrary storage text.
"""
from __future__ import annotations

from ..core.bnf_borrow_claim_observation import BnfBorrowClaimObservation


_MESSAGES = {
    "request_sent": (
        "Une tentative antérieure est enregistrée, sans preuve de son envoi "
        "ou de son résultat. Aucun nouvel essai n’est autorisé."
    ),
    "submitted": (
        "Une demande antérieure a été transmise, sans confirmation de prêt "
        "actif. Vérifiez Mes emprunts ; aucun nouvel essai n’est autorisé."
    ),
    "verification_unavailable": (
        "Une demande antérieure n’a pas pu être vérifiée. Consultez "
        "Mes emprunts ; Cifereca ne répétera pas l’opération."
    ),
    "blocked": (
        "Une tentative précédente a été bloquée. La reprise nécessite "
        "un nouveau cycle explicitement autorisé, indisponible actuellement."
    ),
    "confirmed": (
        "Le prêt avait été confirmé par la BnF lors de cette tentative. "
        "Son état actuel reste à vérifier dans Mes emprunts."
    ),
    "opaque_consumed": (
        "Une tentative antérieure existe, mais son état n’est pas "
        "vérifiable. Aucun nouvel essai n’est autorisé."
    ),
}


def bnf_borrow_claim_message(observation: object) -> str | None:
    """Display previous attempt *only* under closed read-only evidence.

    An absent claim is not permission to borrow; unavailable scopes fall back
    to the existing generic action message, not to a positive capability.
    """
    if type(observation) is not BnfBorrowClaimObservation:
        return None
    if observation.retry_authorized is not False:
        return None
    state = observation.state
    if type(state) is not str:
        return None
    if state == "confirmed":
        if (observation.claim_consumed is not True
                or observation.provider_verified is not True):
            return None
    elif state in _MESSAGES:
        if (observation.claim_consumed is not True
                or observation.provider_verified is not False):
            return None
    else:
        return None
    return _MESSAGES[state]
