"""Shared, GTK-free local bibliographic section projection (#1598).

Only the attested common Search/Library facts belong here. Availability
policies, loan rights, local actions, links and metadata remain owned by
their respective callers and the existing canonical DetailPanel renderer.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


def possession_usage_label(possession, provider_name: str) -> str:
    """Describe one *attested* entitlement, never infer kind from provider.

    Provider ids and catalogue flags cannot establish an owned right or a
    loan. Archived/unknown records do not become active merely because they
    share a title, identity or physical local placement.
    """
    kind = possession.get("kind") if type(possession) is dict else None
    state = possession.get("state") if type(possession) is dict else None
    if kind == "owned" and state == "active":
        return f"Achat possédé en ligne : {provider_name}"
    if kind == "owned" and state == "archived":
        return f"Achat archivé : {provider_name}"
    if kind == "loan" and state == "active":
        return f"Prêt de bibliothèque actif : {provider_name}"
    if kind == "loan" and state in ("expired", "returned", "inactive"):
        return f"Prêt de bibliothèque non actif : {provider_name}"
    # A missing or contradictory state is not a positive purchase/loan.
    return f"Droit distant non qualifié : {provider_name}" if kind in ("owned", "loan") else f"Droit distant : {provider_name}"


def _library_access_rows(item, *, provider_display_name, identity_by_id, sync_label):
    profiles = {}

    def add(identity_id, line):
        lines = profiles.setdefault(str(identity_id or ""), [])
        if line not in lines:
            lines.append(line)

    placements = item.get("placements") or ()
    media = str(item.get("media_format") or "").upper()
    for placement in placements:
        if placement.get("state", "present") != "present":
            continue
        filename = Path(placement.get("filename") or "").name
        storage = placement.get("storage_target_id")
        details = " · ".join(str(value) for value in (filename, media, storage) if value)
        add(placement.get("identity_id"), f"Fichier local : {details}" if details else "Fichier local")
    if not placements and item.get("local_path"):
        filename = Path(item["local_path"]).name
        add(item.get("identity_id"), "Fichier local : " + " · ".join(filter(None, (filename, media))))
    for source in item.get("source_refs") or ():
        provider = source.get("provider_id")
        if provider and provider not in {"local", "manual", "other"}:
            add(source.get("identity_id"), "Source : " + provider_display_name(provider))
    for possession in item.get("possessions") or ():
        provider = provider_display_name(str(possession.get("provider_id") or ""))
        add(possession.get("identity_id"), possession_usage_label(possession, provider))
    if sync_label:
        add(item.get("identity_id"), "État acquisition : " + sync_label)

    rows = []
    for identity_id, lines in profiles.items():
        identity = identity_by_id(identity_id) if identity_id else None
        title = identity.label if identity else identity_id or "Profil non déterminé"
        rows.append((title, "\n".join(lines)))
    return rows


def local_book_detail_sections(
    item: Mapping[str, Any],
    *,
    provider_display_name: Callable[[str], str],
    availability_label: Callable[[Mapping[str, Any]], str],
    identity_by_id: Callable[[str], Any],
    sync_status_label: Callable[[str], str],
    language_label: Callable[[object], str],
    offline_if_local: bool = False,
) -> list[tuple[str, list[tuple[str, str]]]]:
    """Return the same ordered, source-attested facts for Search and Library.

    The Search local-result adapter alone enables the historical offline
    fallback. This helper neither reads a service nor initiates network
    requests; dependencies are supplied by the owning window/port.
    """
    provider = str(item.get("provider_id") or "local")
    availability = availability_label(item)
    if offline_if_local and not availability and str(item.get("state") or "") == "local":
        availability = "Disponible hors ligne"

    sync_status = item.get("sync_status")
    sync_label = sync_status_label(str(sync_status or "")) if sync_status else ""
    if sync_label == "À jour":
        sync_label = ""

    return [
        ("Essentiel", [
            ("Source", provider_display_name(provider)),
            ("Format", str(item.get("media_format") or "").upper()),
            ("Langue", language_label(item.get("metadata"))),
            ("Disponibilité", availability),
        ]),
        ("Bibliothèques et accès", _library_access_rows(
            item, provider_display_name=provider_display_name,
            identity_by_id=identity_by_id, sync_label=sync_label,
        )),
    ]
