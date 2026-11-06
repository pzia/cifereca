"""#4664: show the SAME Core Search detail actions inside Recommendations.

Only an explicitly clicked, exact already-loaded Search notice is eligible.
The plugin supplies source coordinates and its own selection revocation
callback, never a SearchResult, ItemDetail, entitlement or a loan callback.
The Core reads exact account/source binding and uses its existing
DetailPanel.show_item_detail; that same panel retains the recommendation
context as supplementary sections. No provider request on render.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from ..models import SearchResult, ItemDetail
from .exact_search_notice_navigation import loaded_notice_key
from ..task_queue import TaskPriority
from ..core.source_context import source_context_epoch


def _loaded_item(window: Any, coords: dict[str, str]) -> SearchResult | None:
    key = loaded_notice_key(window, **coords)
    if key is None:
        return None
    row = getattr(window, "search_objects", {}).get(key)
    return row if type(row) is SearchResult else None


def _known_item(
    window: Any, coords: dict[str, str],
) -> tuple[SearchResult, dict] | None:
    """Require a live, exact v2 SQLite mirror and Core session binding."""
    from .known_search_notice_navigation import _exact_known_row
    from ..core.known_catalog_provenance import known_catalog_search_result

    row = _exact_known_row(window, **coords)
    if type(row) is not dict:
        return None
    item = known_catalog_search_result(row, identity_id=coords["identity_id"])
    return (item, row) if type(item) is SearchResult else None


def _view_mapped(view: Any) -> bool:
    """A detached plugin pane may retain an old clickable GTK button."""
    try:
        widget = view.widget
        return callable(getattr(widget, "get_mapped", None)) and widget.get_mapped() is True
    except Exception:
        return False


def present_loaded_search_detail_in_place(
    window: Any, *, view: Any,
    context_sections: tuple, is_current: Callable[[], bool],
    provider: str, credential: str, record_id: str,
    docbase: str, source_collection: str, identity_id: str,
    known_source: bool = False,
) -> bool:
    """Explicit click triggers ordinary Core holdings and in-panel actions.

    This is only a presentation route. The exact existing Search action
    handlers still own all mutation admission, confirmation and anti-replay.
    """
    coords = dict(provider=provider, credential=credential,
                  record_id=record_id, docbase=docbase,
                  source_collection=source_collection, identity_id=identity_id)
    if (type(known_source) is not bool
            or getattr(window, "_closed", False)
            or getattr(view, "_window", None) is not window
            or not _view_mapped(view)
            or not callable(getattr(view, "show_existing_search_detail", None))
            or type(context_sections) is not tuple
            or len(context_sections) > 16
            or not callable(is_current)):
        return False
    try:
        if not is_current():
            return False
        known = _known_item(window, coords) if known_source else None
        item = known[0] if known_source and known is not None else (
            None if known_source else _loaded_item(window, coords)
        )
        if item is None:
            return False
        # Pin nested metadata separately from the storage adapter. Some
        # adapters may reuse mutable dictionaries across SQLite projections;
        # aliasing would hide a changed edition or offer at worker/delivery.
        known_snapshot = deepcopy(known[1]) if known is not None else None
        query = item.title if known_source else (
            getattr(window, "_last_search_query", "") or ""
        )
        service = window.service
        # The Core source generation changes on disconnect, rebind and
        # session replacement even if GTK still shows the same identity.
        source_epoch = source_context_epoch(service)
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

    # Only the latest explicit click in this SAME plugin detail may render
    # Search actions. Two holdings tasks can complete out of order without a
    # profile, search or recommendation generation change. This lease is a
    # plain Python token; worker checks do not inspect GTK widget state.
    request_token = object()
    view._cifereca_exact_detail_request_token = request_token

    def source_current() -> bool:
        """Worker-safe service and scalar checks, without GTK access."""
        if (getattr(view, "_cifereca_exact_detail_request_token", None)
                is not request_token
                or getattr(window, "_closed", False)
                or window._identity_context_generation != generation
                or window._search_generation != search_generation
                or getattr(window, "_shell_page_rebuild_token", None) is not shell_token
                or getattr(service.current_identity, "identity_id", None) != identity_id
                or getattr(session, "connected", False) is not True):
            return False
        try:
            if (source_context_epoch(service) != source_epoch
                    or service._session_for_source(provider, credential) is not session
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
        if known_source:
            current = _known_item(window, coords)
            if current is None or current[1] != known_snapshot:
                from ..plugin_api import Cancelled
                raise Cancelled("Notice catalogue modifiée.")
        return service.holdings(item, query, force=False)

    def deliver(detail: object) -> None:
        # Exact source admission is necessary even if the context/lease is
        # still live: a provider response for another notice or credential
        # must never expose its holdings/actions in Recommendations.
        if (type(detail) is not ItemDetail
                or detail.record_id != record_id
                or detail.docbase != docbase
                or detail.source_provider != provider
                or detail.source_credential != credential
                or not source_current()
                or not _view_mapped(view)
                or not is_current()):
            return
        if known_source:
            current = _known_item(window, coords)
            if current is None or current[1] != known_snapshot:
                return
        elif _loaded_item(window, coords) is not item:
            return
        view.show_existing_search_detail(
            item, detail, context_sections=context_sections,
        )

    def failed(_error: BaseException) -> None:
        if (source_current() and is_current() and _view_mapped(view)):
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
