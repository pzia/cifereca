"""Pure CoreUI labels for the *normal* Paris/ADEPT configuration (#1063).

No GTK imports or direct profile/identity selection. Does not convert local
configuration evidence into Adobe/Paris connectivity or license proof.
"""
from __future__ import annotations

from typing import Any, Mapping


_REASONS = {
    "unknown_identity": "identité non enregistrée",
    "missing_paris_connection": "connexion Paris principale absente ou désactivée",
    "multiple_paris_connections": "plusieurs connexions Paris : choix à vérifier",
    "missing_adept_profile": "profil ADEPT de cette identité non associé",
    "missing_primary_account": "compte Paris principal non vérifié localement",
    "multiple_primary_accounts": "plusieurs comptes principaux : attribution ambiguë",
}


def normal_paris_adept_summary(state: Mapping[str, Any]) -> str:
    """One accessible subtitle, without exposing credentials or secrets."""
    status = str(state.get("status") or "to_verify")
    if status == "ready":
        return (
            "Prêt (liaisons locales) · compte Paris principal et profil ADEPT "
            "associés. Autorisation Adobe non testée."
        )
    reason = _REASONS.get(str(state.get("reason") or ""), "liaisons non vérifiées")
    if status == "to_configure":
        return f"À configurer · {reason}."
    return f"À vérifier · {reason}. Aucun profil ADEPT choisi automatiquement."
