"""P0 #1128: old online-search dedupe marker cannot block new generation."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.search_pending import pending_online_search_matches
from cifereca.gui.query_model import QueryState


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _host():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    window = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                  and n.name == "MainWindow")
    methods = [n for n in window.body
               if isinstance(n, ast.FunctionDef) and n.name in {
                   "run_search", "_search_enter",
               }]
    assert len(methods) == 2
    module = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[
            ast.alias(name="annotations")], level=0), *methods], type_ignores=[],
    ))
    env = {
        "pending_online_search_matches": pending_online_search_matches,
        "TaskPriority": NS(USER_ACTION=1),
        "progressive_online_search": lambda *args: None,
    }
    exec(compile(module, str(MAIN), "exec"), env)

    class Runner:
        def __init__(self):
            self.requests = []
        def submit(self, title, work, done, failed=None, **kwargs):
            self.requests.append(NS(title=title,work=work,done=done,failed=failed))

    class Host:
        run_search = env["run_search"]
        _search_enter = env["_search_enter"]
        def __init__(self):
            self.search_controls = NS(state=lambda: QueryState("Dumas"))
            self.service = NS(current_identity=NS(identity_id="alice"))
            self.runner = Runner()
            self._closed = False
            self._search_generation = 0
            self._last_search_query = ""
            self._remote_search_results = []
            self._search_local_objects = {}
            self.search_button = object()
            self.search_global_button = object()
            self._reset_search_enter = lambda: None
            self.toast = lambda *_: None
        def _populate_local_search(self, result):
            self.local = result
        def _populate_search(self, rows, **kwargs):
            self.remote = rows
        def show_error(self, *args):
            self.errors = getattr(self, "errors", []) + [args]
    return Host()


def test_same_generation_still_deduplicates_exact_online_request():
    host = _host()
    host.run_search(True)
    assert len(host.runner.requests) == 1
    host.run_search(True)
    assert len(host.runner.requests) == 1


def test_local_search_invalidates_pending_online_marker_without_waiting_for_old_callback():
    host = _host()
    host.run_search(True)
    old = host.runner.requests[0]
    old_token = host._online_pending_token
    host.run_search(False)
    host.run_search(True)
    assert len(host.runner.requests) == 3
    assert host._online_pending_token is not old_token
    assert host._online_pending_key is not None
    old.failed(RuntimeError("offline"))
    assert host._online_pending_key is not None
    assert host.errors == [] if hasattr(host, "errors") else True
    host.runner.requests[-1].failed(RuntimeError("currently offline"))
    assert host._online_pending_key is None
    assert host._online_pending_generation is None
    assert len(host.errors) == 1


def test_second_enter_is_not_blocked_by_stale_online_pending_generation():
    host = _host()
    host.run_search(True)
    host.run_search(False)
    actions = []
    host._search_enter_sequence = NS(press=lambda state: actions.append(state) or True)
    host._search_enter()
    assert len(actions) == 1
    assert len(host.runner.requests) == 3


def test_search_close_drops_stale_callbacks_without_presenting_errors():
    host = _host()
    host.run_search(True)
    remote = host.runner.requests[0]
    host._closed = True
    remote.failed(RuntimeError("secret"))
    assert not hasattr(host, "errors")
    assert host._online_pending_key is None


def test_pure_guard_rejects_prior_generation_and_bool_tokens():
    key = (QueryState("Dumas"), "alice")
    assert pending_online_search_matches(key,key,3,3)
    assert not pending_online_search_matches(key,key,3,4)
    assert not pending_online_search_matches(key,key,True,1)
    assert not pending_online_search_matches(key,(key[0],"bob"),3,3)


def test_window_captures_generation_and_clears_only_matching_pending_token():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    window = next(n for n in tree.body if isinstance(n,ast.ClassDef)
                  and n.name == "MainWindow")
    method = next(n for n in window.body if isinstance(n,ast.FunctionDef)
                  and n.name == "run_search")
    src = ast.unparse(method)
    assert "self._online_pending_generation = generation" in src
    assert "self._online_pending_token is pending_token" in src
    assert "self._online_pending_generation = None" in src
    assert "not getattr(self, '_closed', False)" in src
