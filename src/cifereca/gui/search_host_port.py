"""Internal, GTK-free interface required by the Search adapters (#1128).

This is intentionally NOT a plugin-facing API.  MainWindow remains the GTK
owner; adapters can only depend on explicitly named host capabilities.
"""
from __future__ import annotations

from typing import Any, Protocol


class SearchControllerHost(Protocol):
    service: Any
    runner: Any
    search_controls: Any
    _search_generation: int
    _online_pending_key: Any
    _online_pending_generation: Any
    _online_pending_token: Any
    _last_search_query: str
    _search_local_objects: Any
    _remote_search_results: Any
    _online_source_context: Any
    _online_source_statuses: Any
    _search_mode: Any

    def _reset_search_enter(self) -> None: ...
    def _render_search_results(self) -> Any: ...
    def run_search(self, online: bool = False, **kwargs: Any) -> Any: ...
    def navigate(self, route: str) -> Any: ...
    def toast(self, message: str, *args: Any) -> Any: ...
    def show_error(self, message: str, *args: Any) -> Any: ...
    def _populate_local_search(self, *args: Any) -> Any: ...
    def _populate_search(self, *args: Any) -> Any: ...


class SearchDetailHost(Protocol):
    service: Any
    runner: Any
    search_page: Any
    search_controls: Any
    search_objects: Any
    _search_local_objects: Any
    _selected_search_id: str | None
    _search_detail_request_generation: int
    _identity_context_generation: int
    _last_search_query: str
    _closed: bool

    def _search_detail_request_context(self) -> tuple[int, int, int, str | None]: ...
    def _search_detail_context_current(self, selected_id: str, context: Any) -> bool: ...
    def _apply_search_detail(self, selected_id: str, item: Any, detail: Any, **kwargs: Any) -> None: ...
    def _search_detail_error(self, selected_id: str, item: Any, exc: BaseException, **kwargs: Any) -> None: ...
    def _cancel_cached_book_details(self) -> Any: ...
    def _request_cached_book_details(self, *args: Any) -> Any: ...
    def _add_facets(self, *args: Any) -> Any: ...
    def _add_local_book_actions(self, *args: Any) -> Any: ...
    def _add_plugin_book_actions(self, *args: Any) -> Any: ...
    def _add_book_notice(self, *args: Any, **kwargs: Any) -> Any: ...
    def _open_library_item(self, key: str) -> Any: ...
    def _load_known_detail(self, item: Any) -> Any: ...
    def _known_search_result(self, item: Any) -> Any: ...
    def _library_availability_label(self, item: Any) -> Any: ...
    def _kobo_backup_status_label(self, status: str) -> Any: ...
    def _book_description(self, item: Any) -> Any: ...
    def run_search(self, online: bool = False, **kwargs: Any) -> Any: ...
    def toast(self, message: str, *args: Any) -> Any: ...


# Only capabilities actually read/written by the async search orchestration.
SEARCH_RUN_CAPABILITIES = frozenset({
    "_closed", "_reset_search_enter", "search_controls", "service",
    "_search_generation", "_loaded_search_context", "toast", "_last_search_query", "_search_mode",
    "_populate_local_search", "runner", "search_button", "search_global_button", "_remote_query_key",
    "_remote_search_results", "_search_local_objects", "_online_pending_key",
    "_online_pending_token", "_online_pending_generation",
    "_online_source_context", "_online_source_statuses", "_populate_search",
    "show_error",
})


class SearchRunPort:
    """Bounded adapter: search orchestration cannot access arbitrary window UI."""

    __slots__ = ("_window",)

    def __init__(self, window: Any) -> None:
        object.__setattr__(self, "_window", window)

    def __getattr__(self, name: str) -> Any:
        if name not in SEARCH_RUN_CAPABILITIES:
            raise AttributeError(name)
        return getattr(self._window, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name not in SEARCH_RUN_CAPABILITIES:
            raise AttributeError(name)
        setattr(self._window, name, value)


SEARCH_DETAIL_CAPABILITIES = frozenset({
    "_closed", "_search_detail_request_generation", "_identity_context_generation",
    "_search_generation", "_selected_search_id", "_search_local_objects",
    "search_objects", "search_page", "search_controls", "service", "runner",
    "_last_search_query", "_cancel_cached_book_details", "_library_availability_label",
    "_kobo_backup_status_label", "_book_description", "_known_search_result",
    "_open_library_item", "_load_known_detail", "run_search", "_add_book_notice",
    "_add_local_book_actions", "_add_plugin_book_actions", "_add_facets", "_request_cached_book_details",
    "_search_detail_request_context", "_search_detail_context_current",
    "_apply_search_detail", "_search_detail_error", "toast",
})


class SearchDetailPort:
    """Only search/detail capabilities are available to the view adapter."""

    __slots__ = ("_window",)

    def __init__(self, window: Any) -> None:
        object.__setattr__(self, "_window", window)

    def __getattr__(self, name: str) -> Any:
        if name not in SEARCH_DETAIL_CAPABILITIES:
            raise AttributeError(name)
        return getattr(self._window, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name not in SEARCH_DETAIL_CAPABILITIES:
            raise AttributeError(name)
        setattr(self._window, name, value)
