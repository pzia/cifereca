"""#6168: Search holdings work/delivery must keep its Core session epochs.

Selection and same-book IDs alone do not prove the previous provider session
is still registered after an account/source epoch switch. Exercise the actual
GTK-free production Search detail controller functions via AST.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from tests.test_core_p1_3104_search_detail_cover_live_row import (
    detail, item,
)


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/search_details.py"


def controller(name):
    root = ast.parse(SOURCE.read_text(encoding="utf-8"))
    fn = next(node for node in root.body
              if isinstance(node, ast.FunctionDef) and node.name == name)
    namespace = {
        "__name__": "cifereca.gui.search_details",
        "__package__": "cifereca.gui",
        "TaskPriority": NS(METADATA=100, USER_ACTION=200),
        "result_row": lambda source, **kwargs: {"id": source.id},
    }
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")],
            level=0,
        ), fn], type_ignores=[],
    )), str(SOURCE), "exec"), namespace)
    return namespace[name]


def host():
    source = item()
    commands = []
    requests = []
    detail_render = []
    def submit(*args, **kw):
        requests.append((args, kw))
    service = NS(
        current_identity=NS(identity_id="alice"),
        _account_context_epoch=3,
        _source_context_epoch=7,
        holdings_for_scope=lambda scope, *args, **kw: commands.append(("GET", args)) or detail(source),
    )
    registered = service.current_identity
    service.identity_by_id = lambda key: registered if key == "alice" else None
    row = NS(key="provider:display-1")
    owner = NS(
        _closed=False,
        _search_local_objects={},
        search_objects={row.key: source},
        _search_detail_request_generation=1,
        _identity_context_generation=9,
        _search_generation=4,
        _last_search_query="Titre",
        _selected_search_id=None,
        _cancel_cached_book_details=lambda: None,
        _add_facets=lambda *_: None,
        _add_local_book_actions=lambda *_: None,
        _request_cached_book_details=lambda *_: None,
        search_controls=NS(),
        search_page=NS(detail=NS(
            show_search=lambda *_a, **_kw: None,
        )),
        service=service,
        runner=NS(submit=submit),
        _search_detail_request_context=lambda: (2, 9, 4, "alice"),
        _apply_search_detail=lambda *a, **kw: detail_render.append(("ok", a, kw)),
        _search_detail_error=lambda *a, **kw: detail_render.append(("error", a, kw)),
    )
    return owner, row, source, requests, commands, detail_render


def test_normal_remote_selection_still_uses_original_holdings_and_callback():
    owner, row, source, requests, commands, shown = host()
    controller("_search_selected")(owner, row)
    assert len(requests) == 1
    positional, options = requests[0]
    assert options["kind"] == "holdings"
    _message, work, done, error = positional
    returned = work()
    assert returned.record_id == source.record_id
    assert len(commands) == 1
    done(returned)
    assert len(shown) == 1 and shown[0][0] == "ok"
    assert shown[0][1][1] is source
    assert shown[0][2]["context"] == (2, 9, 4, "alice")


@pytest.mark.parametrize("changed", [
    "account", "source", "account_aba", "closed",
])
def test_stale_remote_worker_and_both_callbacks_never_act_on_old_session(changed):
    owner, row, source, requests, commands, shown = host()
    controller("_search_selected")(owner, row)
    _message, work, done, error = requests[0][0]
    if changed == "account":
        owner.service._account_context_epoch += 1
    elif changed == "source":
        owner.service._source_context_epoch += 1
    elif changed == "account_aba":
        owner.service._account_context_epoch += 2
        owner.service.current_identity.identity_id = "bob"
        owner.service.current_identity.identity_id = "alice"
    else:
        owner._closed = True
    # Navigation only fences delivery; real source revocation or close
    # still stops the queued work.
    if changed in {"account", "account_aba"}:
        assert work().record_id == source.record_id
        assert len(commands) == 1
    else:
        assert work() is None
        assert commands == []
    done(detail(source))
    error(RuntimeError("provider old session"))
    assert shown == []
    assert owner._selected_search_id == source.id



@pytest.mark.parametrize("method", ["_search_selected", "_load_known_detail"])
def test_queued_holdings_never_runs_on_replaced_core_even_with_same_epochs(method):
    owner, row, source, requests, commands, shown = host()
    if method == "_load_known_detail":
        owner._selected_search_id = source.id
        controller(method)(owner, source)
    else:
        controller(method)(owner, row)
    positional, _options = requests[0]
    work, done = positional[1:3]
    wrong_requests = []
    owner.service = NS(
        current_identity=NS(identity_id="alice"),
        _account_context_epoch=3,
        _source_context_epoch=7,
        holdings=lambda *a, **kw: wrong_requests.append((a, kw)),
    )
    assert work() is None
    done(detail(source))
    assert commands == [] and wrong_requests == [] and shown == []


@pytest.mark.parametrize("method", ["_search_selected", "_load_known_detail"])
def test_queued_holdings_uses_admitted_search_text_not_live_input(method):
    owner, row, source, requests, commands, shown = host()
    if method == "_load_known_detail":
        owner._selected_search_id = source.id
        controller(method)(owner, source)
    else:
        controller(method)(owner, row)
    work = requests[0][0][1]
    owner._last_search_query = "Autre livre"
    assert work().record_id == source.record_id
    assert commands == [("GET", (source, "Titre"))]



def test_prior_session_result_is_rejected_even_if_work_already_ran():
    owner, row, source, requests, commands, shown = host()
    controller("_search_selected")(owner, row)
    _message, work, done, error = requests[0][0]
    prior = work()
    assert len(commands) == 1
    # The worker's result is not a license to repaint after a session rebind.
    owner.service._source_context_epoch += 1
    done(prior)
    error(RuntimeError("late failure"))
    assert shown == []


def test_explicit_known_notice_reuses_exact_epoch_guard_at_worker_and_delivery():
    owner, row, source, requests, commands, shown = host()
    owner._selected_search_id = source.id
    controller("_load_known_detail")(owner, source)
    assert len(requests) == 1
    positional, options = requests[0]
    assert options["origin"] == "user"
    _label, work, done = positional
    assert work().record_id == source.record_id
    done(detail(source))
    assert [event[0] for event in shown] == ["ok"]

    owner2, _row2, source2, requests2, commands2, shown2 = host()
    owner2._selected_search_id = source2.id
    controller("_load_known_detail")(owner2, source2)
    _label, stale_work, stale_done = requests2[0][0]
    owner2.service._account_context_epoch += 1
    assert stale_work().record_id == source2.record_id
    assert len(commands2) == 1
    stale_done(detail(source2))
    assert shown2 == []


def test_epoch_guard_is_worker_only_not_authority_on_loan_or_entitlement():
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    funcs = {
        node.name: ast.unparse(node) for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in ("_search_selected", "_load_known_detail")
    }
    assert len(funcs) == 2
    for name in funcs:
        code = funcs[name]
        assert "getattr(self.service, '_account_context_epoch', None)" in code
        assert "getattr(self.service, '_source_context_epoch', None)" in code
        assert "if epochs_current() else None" in code
        assert "holdings_core.holdings_for_scope(request_scope, item, holdings_query, force=False)" in code
        assert "request_scope.current(holdings_core)" in code
        assert "self.service is holdings_core" in code
        assert "self._apply_search_detail(" in code
        assert "borrow(" not in code
        assert "acquire(" not in code
