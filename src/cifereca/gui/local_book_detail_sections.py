"""Shared, GTK-free local bibliographic section projection (#1598).

Only the attested common Search/Library facts belong here. Availability
policies, loan rights, local actions, links and metadata remain owned by
their respective callers and the existing canonical DetailPanel renderer.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


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

    identity_id = str(item.get("identity_id") or "")
    identity = identity_by_id(identity_id)
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
        ("Bibliothèque", [
            ("Identité", identity.label if identity else identity_id),
            ("Fichier local", str(item.get("local_path") or "")),
            ("État acquisition", sync_label),
        ]),
    ]
