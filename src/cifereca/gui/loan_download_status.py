"""Closed, read-only reasons for loan download availability in CoreUI.

Never render a provider-controlled exception, account label, loan identifier,
URL or free-form code. The source of truth is the Core eligibility result.
"""
from __future__ import annotations

_DESCRIPTIONS = {
    "not_digital": "Ce prêt n’est pas numérique.",
    "provider_unavailable": (
        "La source du prêt n’est pas disponible pour cette identité. "
        "Vérifiez la connexion dans Synchronisation."
    ),
    "provider_disconnected": (
        "La source du prêt est déconnectée. "
        "Reconnectez-la dans Synchronisation avant de télécharger."
    ),
    "download_unsupported": (
        "La source est connectée, mais n’annonce pas de téléchargement "
        "numérique pour cette session."
    ),
}


def loan_download_unavailable_message(result: object) -> str | None:
    """Translate only a fixed Core reason code into a user-facing notice."""
    if type(result) is not dict or set(result) != {"code", "available"}:
        return None
    if result["available"] is not False:
        return None
    code = result["code"]
    if type(code) is not str:
        return None
    return _DESCRIPTIONS.get(code)
