"""P1 #3416: closed catalogue-search vs catalogue-sync dashboard statement.

Catalogue search is NOT catalog refreshing/synchronization. The currently
published capability contract declares 'unsupported' refresh. Future values
cannot silently mint a toggle, a network job or download permission.
Only counts are returned; no provider name, account ID or supplier text.
"""
from __future__ import annotations


def sync_catalogue_overview(raw: object) -> str:
    """Read-only display of a validated catalogue capability snapshot."""
    unavailable = "Catalogues : état non vérifié · aucune synchronisation d’index proposée"
    if type(raw) not in (tuple, list) or len(raw) > 128:
        return unavailable
    valid, available, unsupported, uncertain = 0, 0, 0, 0
    for item in raw:
        provider = getattr(item, "provider_id", None)
        if (type(provider) is not str or not 1 <= len(provider) <= 64
                or any(not char.isascii()
                       or not (char.isalnum() or char in "._-")
                       for char in provider)):
            return unavailable
        identity_scope = getattr(item, "identity_scope", None)
        searchable = getattr(item, "catalog_search_supported", None)
        visible = getattr(item, "catalog_search_available", None)
        if (identity_scope not in {"public", "authenticated"}
                or type(searchable) is not bool
                or type(visible) is not bool):
            return unavailable
        if searchable is not True:
            continue
        valid += 1
        available += int(visible)
        if getattr(item, "catalog_refresh", None) == "unsupported":
            unsupported += 1
        else:
            uncertain += 1
    if not valid:
        return "Catalogues : aucune recherche déclarée pour ce profil · aucun index à synchroniser"
    summary = (
        f"Catalogues : {available}/{valid} recherche(s) disponible(s) · "
        "recherche ≠ synchronisation de catalogue"
    )
    if unsupported == valid:
        return summary + " · aucune actualisation d’index proposée"
    # An unknown/changed producer contract never becomes implicit support.
    return summary + f" · {uncertain} capacité(s) d’index non qualifiée(s), aucune action proposée"


__all__ = ["sync_catalogue_overview"]
