"""Real MainWindow callback methods under a small headless GTK-free host.

These test the callback guards themselves, not an independent approximation.
"""
from __future__ import annotations

from tests.gui_source import gui_source_text

import ast
from pathlib import Path
from types import SimpleNamespace as NS

MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _source(name: str) -> str:
    module = ast.parse(gui_source_text(MAIN))
    cls = next(x for x in module.body if isinstance(x, ast.ClassDef) and x.name == "MainWindow")
    method = next(x for x in cls.body if isinstance(x, ast.FunctionDef) and x.name == name)
    return ast.unparse(method)


def _host():
    module = ast.parse(gui_source_text(MAIN))
    cls = next(x for x in module.body if isinstance(x, ast.ClassDef) and x.name == "MainWindow")
    names = {
        "_search_detail_request_context", "_search_detail_context_current",
        "_apply_search_detail", "_search_detail_error", "_load_known_detail", "_search_selected",
    }
    methods = [x for x in cls.body if isinstance(x, ast.FunctionDef) and x.name in names]
    assert len(methods) == len(names)
    compiled = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
              *methods], type_ignores=[],
    ))
    from cifereca.gui.search_detail_context import detail_callback_is_current
    env = {"result_row": lambda item: {"item_id": item.id}, "TaskPriority": NS(USER_ACTION=1, METADATA=3), "detail_callback_is_current": detail_callback_is_current}
    exec(compile(compiled, str(MAIN), "exec"), env)

    class Runner:
        def __init__(self):
            self.tasks = []

        def submit(self, _title, work, done, error=None, **_kwargs):
            self.tasks.append(NS(work=work, done=done, error=error))

    class Host:
        _search_detail_request_context = env["_search_detail_request_context"]
        _search_detail_context_current = env["_search_detail_context_current"]
        _apply_search_detail = env["_apply_search_detail"]
        _search_detail_error = env["_search_detail_error"]
        _load_known_detail = env["_load_known_detail"]
        _search_selected = env["_search_selected"]

        def __init__(self):
            self.events = []
            self._closed = False
            self._selected_search_id = "same-id"
            self._search_detail_request_generation = 4
            self._identity_context_generation = 2
            self._search_generation = 8
            self._last_search_query = "mot"
            self._search_local_objects = {}
            self.search_objects = {}
            self.service = NS(
                current_identity=NS(identity_id="alice"),
                holdings=lambda *args, **kwargs: NS(title="book"),
            )
            self.search_controls = object()
            self.search_page = NS(detail=NS(
                show_item_detail=lambda *_: self.events.append("render"),
                show_search=lambda *_, **__: self.events.append("error-render"),
                empty=lambda *_: self.events.append("empty"),
            ))
            self.runner = Runner()

        def _add_facets(self, *_):
            self.events.append("facets")

        def _add_local_book_actions(self, *_):
            self.events.append("actions")

        def _request_cached_book_details(self, *_):
            self.events.append("cached")

        def _cancel_cached_book_details(self):
            self.events.append("cancel-cache")

        def toast(self, *_):
            self.events.append("toast")

    return Host()


def test_only_current_holdings_success_can_replace_book_detail():
    host = _host()
    item = NS(id="same-id")
    original = host._search_detail_request_context()
    host._apply_search_detail("same-id", item, object(), context=original)
    assert host.events == ["render", "facets", "actions", "cached"]
    host.events.clear()

    # Re-selecting the same record can reuse its ID, but is another request.
    host._search_detail_request_generation += 1
    host._apply_search_detail("same-id", item, object(), context=original)
    assert host.events == []


def test_identity_a_b_a_does_not_replay_old_holdings():
    host = _host()
    before = host._search_detail_request_context()
    host.service.current_identity = NS(identity_id="bob")
    host._identity_context_generation += 1
    host.service.current_identity = NS(identity_id="alice")
    host._identity_context_generation += 1
    host._apply_search_detail("same-id", NS(id="same-id"), object(), context=before)
    assert host.events == []


def test_new_query_discards_old_result_even_if_selection_key_unchanged():
    host = _host()
    before = host._search_detail_request_context()
    host._search_generation += 1
    host._apply_search_detail("same-id", NS(id="same-id"), object(), context=before)
    assert host.events == []


def test_stale_holdings_errors_do_not_toast_or_erase_other_book():
    host = _host()
    before = host._search_detail_request_context()
    host._selected_search_id = "other"
    host._search_detail_error("same-id", NS(id="same-id"), RuntimeError("late"), context=before)
    assert host.events == []
    host._selected_search_id = "same-id"
    host._closed = True
    host._search_detail_error("same-id", NS(id="same-id"), RuntimeError("late"), context=before)
    assert host.events == []


def test_current_holdings_error_is_reported_once():
    host = _host()
    now = host._search_detail_request_context()
    host._search_detail_error("same-id", NS(id="same-id"), RuntimeError("current"), context=now)
    assert host.events == ["error-render", "actions", "cached", "toast"]


def test_cached_notice_callback_checks_identity_before_holdings_io():
    # Execute the actual extracted SearchDetail owner, without importing gi.
    host = _host()
    generation = host._search_detail_request_generation
    foreign = NS(id="known-item", metadata={
        "catalogue_scope_version": 2,
        "known_catalog_identity_id": "bob",
    })
    host._load_known_detail(foreign)
    assert host._search_detail_request_generation == generation
    assert host.runner.tasks == []

    current = NS(id="known-item", metadata={
        "catalogue_scope_version": 2,
        "known_catalog_identity_id": "alice",
    })
    host._load_known_detail(current)
    assert host._search_detail_request_generation == generation + 1
    assert len(host.runner.tasks) == 1


def test_explicit_known_book_lookup_supersedes_previous_holdings():
    host = _host()
    before = host._search_detail_request_context()
    item = NS(id="known-item")
    host._load_known_detail(item)
    assert host._search_detail_request_generation == before[0] + 1
    task = host.runner.tasks[0]
    task.done(task.work())
    assert host.events[0] == "render"
    host.events.clear()
    host._apply_search_detail("same-id", item, object(), context=before)
    assert host.events == []


def test_runner_callbacks_capture_generation_and_identity():
    source = _source("_search_selected")
    assert "self._search_detail_request_generation += 1" in source
    assert "context = self._search_detail_request_context()" in source
    assert "self._apply_search_detail(selected_id, item, detail, context=context)" in source
    assert "self._search_detail_error(selected_id, item, exc, context=context)" in source
    controller = MAIN.with_name("identity_activation_controller.py").read_text(encoding="utf-8")
    assert "window._search_detail_request_generation += 1" in controller


def test_deselection_revokes_pending_detail_and_does_not_submit_a_new_task():
    host = _host()
    previous = host._search_detail_request_context()
    host._search_selected(None)
    assert host._selected_search_id is None
    assert host._search_detail_request_generation == previous[0] + 1
    assert host.events == ["cancel-cache", "empty"]
    assert host.runner.tasks == []
    host.events.clear()
    host._apply_search_detail("same-id", NS(id="same-id"), object(), context=previous)
    host._search_detail_error("same-id", NS(id="same-id"), RuntimeError("late"), context=previous)
    assert host.events == []


def test_selecting_another_provider_with_the_same_id_rejects_first_callback():
    host = _host()
    first = NS(id="same-id", source_provider="paris")
    second = NS(id="same-id", source_provider="gallica")
    host.search_objects = {"paris:same-id": first, "gallica:same-id": second}
    host._search_selected(NS(key="paris:same-id"))
    old = host.runner.tasks[-1]
    host._search_selected(NS(key="gallica:same-id"))
    current = host.runner.tasks[-1]
    assert len(host.runner.tasks) == 2
    host.events.clear()
    old.done(old.work())
    old.error(RuntimeError("old provider"))
    assert host.events == []
    current.done(current.work())
    assert host.events == ["render", "facets", "actions", "cached"]
