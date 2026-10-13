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


def _pending_local_search():
    requests = []
    rendered = []
    values = [NS(text="Loup", field="title", scope="all",
                 providers=(), types=(), languages=())]
    host = NS(
        _closed=False,
        search_controls=NS(state=lambda: values[0]),
        _reset_search_enter=lambda: None,
        service=NS(current_identity=NS(identity_id="camille")),
        _search_generation=1,
        runner=NS(submit=lambda *args, **kwargs: requests.append(args)),
        search_button=object(),
        _populate_local_search=lambda rows: rendered.append(rows),
    )
    _method("run_search")(host)
    assert len(requests) == 1
    return values, requests[0][2], rendered


def test_pending_local_result_is_discarded_when_query_edited_before_next_search():
    for attr, changed in (
        ("text", "Renard"),
        ("field", "author"),
        ("scope", "physical"),
    ):
        values, done, rendered = _pending_local_search()
        before = values[0]
        values[0] = NS(
            text=changed if attr == "text" else before.text,
            field=changed if attr == "field" else before.field,
            scope=changed if attr == "scope" else before.scope,
        )
        done([{"title": "stale"}])
        assert rendered == [], attr


def test_local_facets_do_not_discard_a_result_with_the_same_base_query():
    values, done, rendered = _pending_local_search()
    values[0] = NS(
        text="Loup", field="title", scope="all",
        providers=("milan",), types=("book",), languages=("fr",),
    )
    done([{"title": "Loup"}])
    assert rendered == [[{"title": "Loup"}]]


def test_online_failure_of_edited_query_does_not_override_new_gtk_state():
    errors = []
    requests = []
    values = [NS(text="Loup", field="title", scope="all", providers=(),
                 types=(), languages=())]
    host = NS(
        _closed=False,
        search_controls=NS(state=lambda: values[0]),
        _reset_search_enter=lambda: None,
        service=NS(current_identity=NS(identity_id="camille")),
        _search_generation=2,
        _last_search_query="Loup",
        _remote_query_key=None,
        _remote_search_results=[],
        _search_local_objects={},
        runner=NS(submit=lambda *args, **kwargs: requests.append(args)),
        search_global_button=object(),
        show_error=lambda *args: errors.append(args),
    )
    _method("run_search")(host, True)
    assert requests
    values[0] = NS(text="Renard", field="title", scope="all")
    requests[0][3](RuntimeError("network error"))
    assert errors == []
    assert host._online_pending_key is None
    assert host._online_pending_generation is None



def _pending_online_failure():
    requests = []
    errors = []
    state = NS(text="Loup", field="title", scope="all", providers=(),
               types=(), languages=())
    host = NS(
        _closed=False,
        search_controls=NS(state=lambda: state),
        _reset_search_enter=lambda: None,
        service=NS(current_identity=NS(identity_id="alice")),
        _search_generation=3,
        _last_search_query="Loup",
        _remote_query_key=None,
        _remote_search_results=[],
        _search_local_objects={},
        runner=NS(submit=lambda *args, **kwargs: requests.append(args)),
        search_global_button=object(),
        show_error=lambda *args: errors.append(args),
    )
    _method("run_search")(host, True)
    assert len(requests) == 1
    assert host._online_pending_key is not None
    return host, requests[0][3], errors


def test_online_provider_exception_never_exposes_private_url_or_token_in_dialog():
    host, failed, errors = _pending_online_failure()
    secret = ("403 https://provider.example/private?token=SECRET-ABC "
              "/home/alice/private-card alice@example.test")
    failed(RuntimeError(secret))
    assert errors == [(
        "Les catalogues en ligne ne répondent pas. Réessayez ultérieurement.",
        "Recherche en ligne impossible",
    )]
    assert not any(value in repr(errors) for value in (
        "SECRET-ABC", "alice@example.test", "/home/alice", "provider.example",
    ))
    assert host._online_pending_key is None
    assert host._online_pending_generation is None


def test_online_failure_after_close_clears_pending_without_showing_dialog():
    host, failed, errors = _pending_online_failure()
    host._closed = True
    failed(RuntimeError("SECRET-ABC"))
    assert errors == []
    assert host._online_pending_key is None
    assert host._online_pending_generation is None
