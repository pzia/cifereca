"""#3104: display a missing series using the exact scoped, retained C evidence.

Only the existing Core reader accesses SQLite. The GTK completion is guarded
by the same selection, source coordinates, route and render incarnation.
"""
from __future__ import annotations

from ..book_detail_metadata import detail_metadata_fields
from ..core.notice_c_private_series import _key, read_series_for_search
from ..core.public_search_projection import PublicSeriesRelation
from ..core.search_orchestration import capture_search_read_scope
from ..models import SearchResult
from ..task_queue import TaskPriority


def _has_series(row):
    return any(field.label == "Série" or field.label.endswith(" · Série")
               for field in detail_metadata_fields(row.metadata))


def request_selected_retained_series(host, row, *, selected_id, context):
    """Read locally, at most once per render; never modify the selected DTO."""
    if (type(row) is not SearchResult or type(context) is not tuple or len(context) != 4
            or type(selected_id) is not str or type(row.id) is not str
            or row.id != selected_id or _has_series(row)):
        return False
    core = getattr(host, "service", None)
    page = host.search_page
    panel = page.detail
    runner = getattr(host, "runner", None)
    render = getattr(panel, "add_retained_search_series", None)
    selected = getattr(getattr(page, "selection", None), "get_selected_item", None)
    if (not callable(render) or not callable(selected)
            or not callable(getattr(runner, "submit", None))
            or getattr(panel, "_private_series_request_token", None) is not None):
        return False
    cell = selected()
    cell_key = getattr(cell, "key", None)
    objects = getattr(host, "search_objects", None)
    if (type(cell_key) is not str or type(objects) is not dict
            or objects.get(cell_key) is not row
            or not host._search_detail_context_current(selected_id, context)):
        return False
    try:
        scope = capture_search_read_scope(core, context[3])
        source = _key(row, scope.identity_id)
        if source is None:
            return False
    except (AttributeError, TypeError, ValueError):
        return False
    scenario = row.scenario
    storage = core.storage
    account_epoch = getattr(core, "_account_context_epoch", None)
    route = getattr(host, "_route_display_token", None)
    token = object()
    panel._private_series_request_token = token
    pending = True

    def worker_current():
        # No GTK queries or SQLite here; the Core reader owns policy and DB.
        return (not getattr(host, "_closed", False)
                and host.service is core and core.storage is storage
                and getattr(core, "_account_context_epoch", None) == account_epoch
                and getattr(host, "_route_display_token", None) is route
                and scope.current(core)
                and type(row.id) is str and row.id == selected_id
                and _key(row, scope.identity_id) == source
                and row.scenario == scenario)

    def work():
        if not pending or not worker_current():
            return None
        return read_series_for_search(core, row, reader_scope=scope)

    def failed(_error):
        nonlocal pending
        pending = False

    def done(relation):
        nonlocal pending
        if not pending:
            return
        pending = False
        current_objects = getattr(host, "search_objects", None)
        mapped = getattr(panel, "get_mapped", None)
        if (type(relation) is not PublicSeriesRelation or not worker_current()
                or host.search_page is not page or page.detail is not panel
                or getattr(panel, "_private_series_request_token", None) is not token
                or not callable(getattr(getattr(page, "selection", None), "get_selected_item", None))
                or page.selection.get_selected_item() is not cell
                or getattr(cell, "key", None) != cell_key
                or type(current_objects) is not dict or current_objects.get(cell_key) is not row
                or not host._search_detail_context_current(selected_id, context)
                or (callable(mapped) and not mapped()) or _has_series(row)):
            return
        render(relation)

    try:
        runner.submit(
            "Lecture de la série conservée…", work, done, failed,
            priority=TaskPriority.METADATA, kind="detail-cache", origin="automatic",
        )
    except Exception:
        failed(None)
        return False
    return True
