"""#4664: show the SAME Core Search detail actions inside Recommendations.

Only an explicitly clicked, exact already-loaded Search notice is eligible.
The plugin supplies source coordinates and its own selection revocation
callback, never a SearchResult, ItemDetail, entitlement or a loan callback.
The Core reads exact account/source binding and uses its existing
DetailPanel.show_item_detail; that same panel retains the recommendation
context as supplementary sections. No provider request on render.
"""
from __future__ import annotations

from typing import Any, Callable

from ..models import SearchResult, ItemDetail
from .exact_search_notice_navigation import loaded_notice_key
from ..task_queue import TaskPriority


def _loaded_item(window: Any, coords: dict[str, str]) -> SearchResult | None:
    key = loaded_notice_key(window, **coords)
    if key is None:
        return None
    row = getattr(window, "search_objects", {}).get(key)
    return row if type(row) is SearchResult else None


def present_loaded_search_detail_in_place(
    window: Any, *, view: Any,
    context_sections: tuple, is_current: Callable[[], bool],
    provider: str, credential: str, record_id: str,
    docbase: str, source_collection: str, identity_id: str,
) -> bool:
    """Explicit click triggers ordinary Core holdings and in-panel actions.

    This is only a presentation route. The exact existing Search action
    handlers still own all mutation admission, confirmation and anti-replay.
    """
    coords = dict(provider=provider, credential=credential,
                  record_id=record_id, docbase=docbase,
                  source_collection=source_collection, identity_id=identity_id)
    if (getattr(window, "_closed", False)
            or getattr(view, "_window", None) is not window
            or not callable(getattr(view, "show_existing_search_detail", None))
            or type(context_sections) is not tuple
            or len(context_sections) > 16
            or not callable(is_current)):
        return False
    try:
        if not is_current():
            return False
        item = _loaded_item(window, coords)
        if item is None:
            return False
        service = window.service
        identity = service.current_identity
        if getattr(identity, "identity_id", None) != identity_id:
            return False
        generation = window._identity_context_generation
        search_generation = window._search_generation
        shell_token = getattr(window, "_shell_page_rebuild_token", None)
        session = service._session_for_source(provider, credential)
        if (session is None or getattr(session, "connected", False) is not True
                or service.storage.identity_for_connection(provider, credential)
                != identity_id):
            return False
    except Exception:
        return False

    def source_current() -> bool:
        """Worker-safe service and scalar checks, without GTK access."""
        if (getattr(window, "_closed", False)
                or window._identity_context_generation != generation
                or window._search_generation != search_generation
                or getattr(window, "_shell_page_rebuild_token", None) is not shell_token
                or getattr(service.current_identity, "identity_id", None) != identity_id
                or getattr(session, "connected", False) is not True):
            return False
        try:
            if (service._session_for_source(provider, credential) is not session
                    or service.storage.identity_for_connection(provider, credential)
                    != identity_id):
                return False
            bindings = service.storage.provider_connections(identity_id=identity_id)
            return (type(bindings) is list and len(bindings) <= 512
                    and sum(
                        type(row) is dict
                        and row.get("identity_id") == identity_id
                        and row.get("provider_id") == provider
                        and row.get("credential_id") == credential
                        and row.get("enabled") is True
                        for row in bindings
                    ) == 1)
        except Exception:
            return False

    def work() -> ItemDetail:
        if not source_current():
            from ..plugin_api import Cancelled
            raise Cancelled("Source de consultation périmée.")
        return service.holdings(item, getattr(window, "_last_search_query", "") or "",
                                force=False)

    def deliver(detail: object) -> None:
        if (type(detail) is not ItemDetail
                or not source_current()
                or not is_current()
                or _loaded_item(window, coords) is not item):
            return
        view.show_existing_search_detail(
            item, detail, context_sections=context_sections,
        )

    def failed(_error: BaseException) -> None:
        if source_current() and is_current():
            window.toast("Disponibilités indisponibles pour cette notice.", 5)

    try:
        window.runner.submit(
            "Consultation des disponibilités de cette notice…",
            work, deliver, failed,
            priority=TaskPriority.USER_ACTION, kind="holdings", origin="user",
        )
    except Exception:
        return False
    return True


__all__ = ["present_loaded_search_detail_in_place"]
