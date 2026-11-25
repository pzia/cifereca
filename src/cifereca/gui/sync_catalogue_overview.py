"""P1 #3416 / #2385: closed catalogue-search vs catalogue-sync dashboard.

One defensive snapshot drives both counts and read-only catalogue cards.
No capability observation grants refresh, a network job or a right.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CatalogueSearchRow:
    provider_id: str
    identity_scope: str
    catalog_search_supported: bool
    catalog_search_available: bool
    refresh_unsupported: bool


def validated_catalogue_rows(raw: object) -> tuple[CatalogueSearchRow, ...] | None:
    """Copy a bounded, coherent snapshot; ambiguous evidence is unverified."""
    if type(raw) not in (tuple, list) or len(raw) > 128:
        return None
    seen = set()
    rows = []
    for item in raw:
        provider = getattr(item, "provider_id", None)
        scope = getattr(item, "identity_scope", None)
        supported = getattr(item, "catalog_search_supported", None)
        available = getattr(item, "catalog_search_available", None)
        refresh = getattr(item, "catalog_refresh", None)
        if (type(provider) is not str or not 1 <= len(provider) <= 64
                or any(not c.isascii() or not (c.isalnum() or c in "._-")
                       for c in provider)
                or provider in seen
                or type(scope) is not str or scope not in ("public", "authenticated")
                or type(supported) is not bool or type(available) is not bool
                or (available and not supported)
                or type(refresh) is not str):
            return None
        seen.add(provider)
        rows.append(CatalogueSearchRow(
            provider, scope, supported, available, refresh == "unsupported",
        ))
    return tuple(rows)


def format_catalogue_overview(rows: tuple[CatalogueSearchRow, ...] | None) -> str:
    if rows is None:
        return "Catalogues : état non vérifié · aucune synchronisation d’index proposée"
    searchable = tuple(row for row in rows if row.catalog_search_supported)
    if not searchable:
        return "Catalogues : aucune recherche déclarée pour ce profil · aucun index à synchroniser"
    available = sum(row.catalog_search_available for row in searchable)
    uncertain = sum(not row.refresh_unsupported for row in searchable)
    summary = (
        f"Catalogues : {available}/{len(searchable)} recherche(s) disponible(s) · "
        "recherche ≠ synchronisation de catalogue"
    )
    if not uncertain:
        return summary + " · aucune actualisation d’index proposée"
    return summary + f" · {uncertain} capacité(s) d’index non qualifiée(s), aucune action proposée"


def sync_catalogue_overview(raw: object) -> str:
    """Read-only display of a validated catalogue capability snapshot."""
    return format_catalogue_overview(validated_catalogue_rows(raw))


__all__ = ["sync_catalogue_overview"]
