"""Human-readable, conservative return outcomes for Mes emprunts.

Pure CoreUI presentation contract: a successful worker task is not proof of
Adobe or Paris return. A returned license and a returned provider loan are
independent facts.
"""
from __future__ import annotations

from typing import Any


def loan_return_feedback(result: Any, *, channel: str) -> tuple[str, str]:
    """Return (confirmed|attention, message), never infer success from HTTP ACK."""
    if not isinstance(result, dict):
        return "attention", "Résultat de restitution incomplet : vérification nécessaire."

    status = str(result.get("status") or "")
    if channel == "adept":
        if (
            status == "paris_return_confirmed"
            and result.get("adobe_return_confirmed") is True
            and result.get("provider_return_confirmed") is True
            and result.get("paris_verification") == "provider_absent"
        ):
            return "confirmed", "Licence ADEPT restituée et prêt absent du compte Paris."
        if result.get("adobe_return_confirmed") is True:
            if result.get("paris_verification") == "still_present":
                return (
                    "attention",
                    "Licence ADEPT restituée, mais le prêt reste actif à Paris. "
                    "Aucun retour Paris automatique n’a été envoyé ; une action PNB distincte "
                    "est possible après vérification.",
                )
            return (
                "attention",
                "Licence ADEPT restituée, mais la disparition du prêt à Paris "
                "n’a pas pu être confirmée. Aucun retour Paris automatique n’a été envoyé.",
            )
        if status == "provider_action_available":
            return (
                "attention",
                "Aucune licence ADEPT restituable n’a été trouvée. "
                "Utilisez séparément « Restituer à Paris (PNB) » si nécessaire.",
            )
        if status in {"failed", "adobe_return_requested", "adobe_return_confirmed"}:
            return (
                "attention",
                "La restitution ADEPT n’est pas confirmée. "
                "Vérifiez la licence avant toute nouvelle action ; "
                "aucune restitution Adobe automatique ne sera rejouée.",
            )
        return (
            "attention",
            "Restitution ADEPT indisponible ou ambiguë "
            "(emprunteur, transaction ou profil d’origine à vérifier). "
            "Aucune licence n’a été restituée automatiquement.",
        )

    if channel == "pnb":
        if status == "provider_return_confirmed" and result.get("provider_inventory_verified") is True:
            return "confirmed", "Le prêt est absent de l’inventaire Paris vérifié."
        if status == "still_active":
            return (
                "attention",
                "Le prêt reste actif dans l’inventaire Paris. "
                "La demande de restitution ne constitue pas une preuve de retour.",
            )
        if status in {"pending_verification", "request_sent", "verification_unavailable"}:
            return (
                "attention",
                "Retour Paris non confirmé : la demande a pu être envoyée, "
                "mais l’inventaire du compte emprunteur n’a pas confirmé sa disparition. "
                "Évitez de renvoyer la demande.",
            )
        if result.get("reason") == "adobe_return_takes_precedence":
            return (
                "attention",
                "Une restitution ADEPT est déjà engagée. "
                "Vérifiez son résultat avant de demander un retour Paris distinct.",
            )
        if result.get("success") is True:
            return (
                "attention",
                "Le fournisseur a accepté la demande, mais l’absence du prêt "
                "dans son inventaire n’a pas été vérifiée.",
            )
        return (
            "attention",
            "Restitution fournisseur non confirmée : vérifiez l’état du prêt "
            "avant toute nouvelle demande.",
        )
    raise ValueError("unknown return channel")
