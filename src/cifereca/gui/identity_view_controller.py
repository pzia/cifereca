"""GTK-independent filtered identity navigation for the public UI host (#1130).

The window keeps ownership of the identity, controls and navigation. Validate
the entire provider request before switching identity or changing filters.
"""
from __future__ import annotations

from .query_model import QueryState


def open_identity_view(window, identity_id: str, *, route: str, providers=()) -> None:
    if getattr(window, "_closed", False):
        return
    if route not in {"search", "library"}:
        raise ValueError("Vue filtrée inconnue")
    if isinstance(providers, (str, bytes)):
        raise ValueError("Sources invalides")
    try:
        snapshot = tuple(providers)
    except TypeError as exc:
        raise ValueError("Sources invalides") from exc
    if any(not isinstance(provider, str) or not provider for provider in snapshot):
        raise ValueError("Sources invalides")
    # A one-shot iterable must never be consumed during validation and then
    # mistaken for an empty filter (which would widen the visible sources).
    selected = tuple(dict.fromkeys(snapshot))
    allowed = {descriptor.id for descriptor in window.service.provider_descriptors()} | {"local"}
    if not set(selected) <= allowed:
        raise ValueError("Source inconnue")

    window._activate_identity(identity_id)
    if getattr(window, "_closed", False):
        return
    controls = window.search_controls if route == "search" else window.library_controls
    controls.set_state(QueryState(scope="digital", providers=selected))
    window.navigate(route)
    if getattr(window, "_closed", False):
        return
    if route == "search":
        window.run_search(False)
    else:
        window._render_library()
