"""Run actual search controller bodies with actual MainWindow state descriptors."""
from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.query_model import QueryState, merge_results
from cifereca.gui.search_state import SearchStateField


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"
OWNER = MAIN.with_name("search_controller.py")


def _host():
    window = next(n for n in ast.parse(MAIN.read_text()).body
                  if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    descriptors = [n for n in window.body if isinstance(n, ast.Assign)
                   and isinstance(n.value, ast.Call)
                   and isinstance(n.value.func, ast.Name) and n.value.func.id == "SearchStateField"]
    assert len(descriptors) == 13
    methods = [n for n in ast.parse(OWNER.read_text()).body if isinstance(n, ast.FunctionDef)]
    scheduled = []
    env = {"__package__": "cifereca.gui", "SearchStateField": SearchStateField,
           "TaskPriority": NS(USER_ACTION=1), "merge_results": merge_results,
           "GLib": NS(idle_add=lambda fn, *args: scheduled.append((fn, args))),
           "progressive_online_search": Mock(return_value=NS(warnings=[]))}
    cls = ast.ClassDef(name="Host", bases=[], keywords=[], body=[*descriptors, *methods], decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0), cls], type_ignores=[]))
    exec(compile(module, str(OWNER), "exec"), env)
    host = env["Host"]()
    host.service = NS(current_identity=NS(identity_id="alice"),
                      search_known_catalog=Mock(return_value=[{"key": "local"}]),
                      last_search=[], last_search_warnings=[])
    host.search_controls = NS(state=lambda: QueryState("Dumas"))
    host.search_button, host.search_global_button = object(), object()
    host._search_generation = 0
    host._last_search_query = ""
    host._search_mode = "local"
    host._search_local_objects = {}
    host._remote_search_results = []
    host._closed = False
    host.requests = []
    def submit(title, work, done, failed=None, **options):
        host.requests.append(NS(work=work, done=done, failed=failed, options=options))
    host.runner = NS(submit=submit)
    host._populate_search, host._populate_local_search = Mock(), Mock()
    host.show_error, host.toast = Mock(), Mock()
    host.scheduled = scheduled
    host.online = env["progressive_online_search"]
    return host


def test_each_window_has_its_own_state_and_lazy_pending_attributes():
    first, second = _host(), _host()
    assert first._search_controller_state is not second._search_controller_state
    assert not hasattr(first, "_online_pending_token")
    assert not hasattr(first, "_search_enter_sequence")
    assert getattr(first, "_online_pending_generation", None) is None
    first._remote_search_results.append(NS(id="only-first"))
    assert second._remote_search_results == []
    first._search_generation += 1
    assert first._search_controller_state.generation == 1
    assert second._search_generation == 0
    assert "_search_generation" not in vars(first)


def test_local_search_uses_requested_identity_and_rejects_old_query_callback():
    host = _host()
    host.run_search(False)
    old = host.requests[-1]
    old.work()
    host.service.search_known_catalog.assert_called_once_with(
        "Dumas", "all", field="all", identity_id="alice")
    host.run_search(False)
    old.done([{"key": "stale"}])
    host._populate_local_search.assert_not_called()
    host.requests[-1].done([{"key": "current"}])
    host._populate_local_search.assert_called_once_with([{"key": "current"}])
    host.online.assert_not_called()


def test_remote_batches_wait_for_ui_dispatch_and_stale_batch_is_discarded():
    host = _host()
    host.run_search(True)
    request = host.requests[-1]
    request.work()
    on_batch = host.online.call_args.args[2]
    item = NS(id="same", source_provider="paris")
    on_batch(NS(results=[item]))
    host._populate_search.assert_not_called()
    assert host._remote_search_results == []
    dispatch, args = host.scheduled.pop()
    assert dispatch(*args) is False
    host._populate_search.assert_called_once_with([item], announce=False)
    host._populate_search.reset_mock()
    on_batch(NS(results=[NS(id="late", source_provider="paris")]))
    host.run_search(False)
    dispatch, args = host.scheduled.pop()
    assert dispatch(*args) is False
    request.done(NS(warnings=["old warning"]))
    host._populate_search.assert_not_called()
    assert host.service.last_search_warnings == []


def test_old_error_cannot_clear_new_pending_request_or_display_error():
    host = _host()
    host.run_search(True)
    old = host.requests[-1]
    host.run_search(False)
    host.run_search(True)
    token = host._online_pending_token
    generation = host._online_pending_generation
    old.failed(RuntimeError("old"))
    assert host._online_pending_token is token
    assert host._online_pending_generation == generation
    host.show_error.assert_not_called()
    host.requests[-1].failed(RuntimeError("current"))
    assert host._online_pending_key is None and host._online_pending_generation is None
    host.show_error.assert_called_once()


def test_second_enter_extends_online_once_and_pending_enter_preserves_generation():
    host = _host()
    host._search_enter()
    assert host.requests[-1].options["kind"] == "search-known"
    host._search_enter()
    assert host.requests[-1].options["kind"] == "search-online"
    generation = host._search_generation
    host._search_enter()
    assert len(host.requests) == 2 and host._search_generation == generation
    assert host._search_controller_state.enter_sequence is host._search_enter_sequence


@pytest.mark.parametrize("closed,identity", [(True, "alice"), (False, "bob")])
def test_late_local_success_after_close_or_identity_switch_is_ignored(closed, identity):
    host = _host()
    host.run_search(False)
    host._closed = closed
    host.service.current_identity = NS(identity_id=identity)
    host.requests[-1].done([{"key": "wrong context"}])
    host._populate_local_search.assert_not_called()


def test_state_descriptor_deletion_preserves_legacy_getattr_default():
    host = _host()
    host._online_pending_key = (QueryState("Dumas"), "alice")
    del host._online_pending_key
    assert not hasattr(host, "_online_pending_key")
    assert getattr(host, "_online_pending_key", None) is None
    assert host._search_generation == 0


def test_state_import_is_headless_and_does_not_load_window_or_controller():
    script = """
import importlib.abc, sys
class NoGtk(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'gi' or fullname.startswith('gi.'):
            raise AssertionError('state must not import GTK')
sys.meta_path.insert(0, NoGtk())
from cifereca.gui.search_state import SearchControllerState
state = SearchControllerState()
assert not hasattr(state, 'generation')
assert 'cifereca.gui.main_window' not in sys.modules
assert 'cifereca.gui.search_controller' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
