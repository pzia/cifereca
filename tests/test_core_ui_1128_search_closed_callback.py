"""P0 #1128: callbacks from a detached plugin/search UI are inert."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/search_controller.py"


def _method(name):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    func = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.fix_missing_locations(ast.Module(
        body=[
            ast.ImportFrom(module="__future__",
                           names=[ast.alias(name="annotations")], level=0),
            func,
        ], type_ignores=[],
    ))
    namespace = {
        "__package__": "cifereca.gui",
        "TaskPriority": NS(USER_ACTION=0),
    }
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace[name]


def test_closed_plugin_book_search_does_not_touch_stale_gtk_controls():
    search = _method("search_books")
    assert search(NS(_closed=True), "Livre A", identity_id="camille") is False


def test_closed_search_callbacks_do_not_mutate_ui_or_submit_work():
    for name in ("_search_controls_changed", "_search_enter", "run_search"):
        assert _method(name)(NS(_closed=True)) is None


def test_open_plugin_book_search_still_navigates_then_runs_local_search():
    calls = []
    host = NS(
        _closed=False,
        service=NS(current_identity=NS(identity_id="camille")),
        search_controls=NS(set_state=lambda state, notify: calls.append((
            "filters", state.text, state.field, state.providers, notify,
        ))),
        navigate=lambda route: calls.append(("navigate", route)),
        run_search=lambda online: calls.append(("search", online)),
    )
    assert _method("search_books")(
        host, "  Livre A  ", field="title", online=False, identity_id="camille",
    ) is True
    assert calls == [
        ("filters", "Livre A", "title", (), False),
        ("navigate", "search"),
        ("search", False),
    ]
    assert _method("search_books")(host, "Livre A", identity_id="bob") is False
    assert len(calls) == 3


def test_open_local_search_still_submits_and_ignores_completion_after_close():
    calls = []
    queued = []
    state = NS(text="Livre A", field="title", scope="all")
    host = NS(
        _closed=False,
        search_controls=NS(state=lambda: state),
        _reset_search_enter=lambda: calls.append("reset"),
        service=NS(current_identity=NS(identity_id="camille")),
        _search_generation=3,
        runner=NS(submit=lambda *args, **kwargs: queued.append((args, kwargs))),
        search_button=object(),
        toast=lambda message: calls.append(("toast", message)),
        _populate_local_search=lambda rows: calls.append(("render", rows)),
    )
    run = _method("run_search")
    assert run(host) is None
    assert host._search_generation == 4
    assert calls == ["reset"]
    assert len(queued) == 1
    args, kwargs = queued[0]
    assert kwargs["kind"] == "search-known"
    assert kwargs["control"] is host.search_button
    assert args[0] == "Recherche dans le connu…"
    finished = args[2]
    host._closed = True
    finished(["stale"])
    assert calls == ["reset"]
    host._closed = False
    finished(["current"])
    assert calls == ["reset", ("render", ["current"])]


def test_open_filters_still_reset_enter_sequence_and_rerender():
    calls = []
    host = NS(
        _closed=False,
        _reset_search_enter=lambda: calls.append("reset"),
        _render_search_results=lambda: calls.append("render"),
    )
    _method("_search_controls_changed")(host)
    assert calls == ["reset", "render"]
