"""Legacy plugin-manager help text, separate from search route labels.

P0 #5083: selectable source names are supplied by plugin descriptors.
These compatibility hints never discover or authorize a provider.
"""
from __future__ import annotations


_PLUGIN_USAGE_HINTS = {
    "provider.kobo": (
        "Kobo : Mes achats proviennent du compte connecté. "
        "Le catalogue utilise le service technique Kobo existant ; "
        "il ne crée aucun second compte ni ne prouve un achat."
    ),
    "provider.kobo.catalog": (
        "Service technique du catalogue Kobo : pas de deuxième compte "
        "utilisateur, aucun droit d’achat ou téléchargement déduit."
    ),
    "provider.numilog": (
        "Numilog — Mes achats : compte privé avec droits éventuels. "
        "Le catalogue commercial public est une source optionnelle distincte."
    ),
    "provider.numilog.catalog": (
        "Numilog — Catalogue public : recherche anonyme optionnelle, sans compte, "
        "sans achat attesté et sans téléchargement. L’activation par identité est "
        "indépendante du compte Numilog — Mes achats."
    ),
}


def store_plugin_usage_hint(plugin_id: str) -> str:
    """The known package role, not a capability or readiness assessment."""
    return _PLUGIN_USAGE_HINTS.get(plugin_id, "")
