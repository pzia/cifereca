"""P0 #1128: no search-detail widget action or private error after close."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS


SEARCH_DETAILS = Path(__file__).resolve().parents[1] / "src/cifereca/gui/search_details.py"


def _method(name):
    tree = ast.parse(SEARCH_DETAILS.read_text(encoding="utf-8"))
    fn = next(item for item in tree.body
              if isinstance(item, ast.FunctionDef) and item.name == name)
    scope = {
        "TaskPriority": NS(USER_ACTION=10),
        "result_row": lambda item: {"id": item.id},
    }
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(module="__future__",
                             names=[ast.alias(name="annotations")], level=0),
              fn], type_ignores=[],
    )), str(SEARCH_DETAILS), "exec"), scope)
    return scope[name]


def test_closed_window_drops_late_selection_before_touching_cache_or_widgets():
    select = _method("_search_selected")
    assert select(NS(_closed=True), NS(key="book")) is None
    assert select(NS(_closed=True), None) is None


def test_closed_window_drops_old_known_detail_action_before_submitting_task():
    request = _method("_load_known_detail")
    assert request(NS(_closed=True), NS(id="book")) is None


def test_live_deselection_still_clears_detail_and_cached_book_request():
    effects = []
    host = NS(
        _closed=False,
        _search_detail_request_generation=1,
        _cancel_cached_book_details=lambda: effects.append("cancel-cache"),
        search_page=NS(detail=NS(
            empty=lambda *args: effects.append(("empty", args)),
        )),
    )
    _method("_search_selected")(host, None)
    assert host._search_detail_request_generation == 2
    assert host._selected_search_id is None
    assert [action if isinstance(action, str) else action[0]
            for action in effects] == ["cancel-cache", "empty"]


def test_live_known_detail_still_queues_one_explicit_holdings_request():
    calls = []
    host = NS(
        _closed=False,
        _search_detail_request_generation=2,
        _selected_search_id="book",
        _search_detail_request_context=lambda: (3, 1, 9, "camille"),
        _last_search_query="Loup",
        runner=NS(submit=lambda *args, **kwargs: calls.append((args, kwargs))),
    )
    _method("_load_known_detail")(host, NS(id="book"))
    assert host._search_detail_request_generation == 3
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "Disponibilités…"
    assert kwargs["kind"] == "holdings"
    assert kwargs["priority"] == 10


def test_provider_error_keeps_details_but_does_not_expose_signed_urls_or_paths():
    calls = []
    host = NS(
        _closed=False,
        _search_detail_context_current=lambda *_: True,
        search_page=NS(detail=NS(
            show_search=lambda *args, **kwargs: calls.append(("show", kwargs)),
        )),
        search_controls=NS(),
        _add_local_book_actions=lambda *args: calls.append(("actions",)),
        _request_cached_book_details=lambda *args: calls.append(("cache",)),
        toast=lambda message, timeout: calls.append(("toast", message, timeout)),
    )
    item = NS(id="book")
    exception = RuntimeError("Authorization=secret https://host/path?token=personal /home/alice/private")
    _method("_search_detail_error")(host, "book", item, exception)
    assert [entry[0] for entry in calls] == ["show", "actions", "cache", "toast"]
    assert calls[-1] == (
        "toast", "Disponibilités indisponibles. Réessayez ultérieurement.", 5,
    )
    assert "personal" not in str(calls)
    assert "alice" not in str(calls)


def test_stale_provider_failure_is_ignored_before_touching_detail():
    host = NS(
        _closed=True,
        _search_detail_context_current=lambda *_: False,
    )
    _method("_search_detail_error")(host, "book", NS(id="book"), RuntimeError("secret"))
