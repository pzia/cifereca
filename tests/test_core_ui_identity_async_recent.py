from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui import library_refresh


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _harness():
    """Exercise actual MainWindow callbacks without requiring a GTK display."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "MainWindow")
    methods = [
        x for x in cls.body
        if isinstance(x, ast.FunctionDef)
        and x.name in {"_identity_request_current", "refresh_recent", "refresh_library"}
    ]
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *methods], type_ignores=[]))
    ns = {"library_refresh": library_refresh}
    exec(compile(module, str(SOURCE), "exec"), ns)
    class Runner:
        def __init__(self):
            self.requests = []

        def submit(self, title, work, ok, err=None, **kwargs):
            self.requests.append(NS(work=work, ok=ok, err=err, kwargs=kwargs))
    Host = type("Host", (), {name: ns[name] for name in (
        "_identity_request_current", "refresh_recent", "refresh_library",
    )})
    service = NS(
        current_identity=NS(identity_id="alice"),
        recent_acquisitions=lambda *, identity_id: [{"identity_id": identity_id}],
        library_entries=lambda *, identity_id, scan: [{"identity_id": identity_id}],
    )
    host = Host()
    host.service = service
    host.runner = Runner()
    host._identity_context_generation = 1
    host._recent_refresh_pending = False
    host._library_refresh_pending = False
    host.diagnostics = NS(add=lambda *a, **kw: pytest.fail("stale diagnostic"))
    host.show_error = lambda *a: pytest.fail("stale error toast")
    host.recent_shown = []
    host.library_shown = []
    host._show_recent_entries = lambda items: host.recent_shown.append(items)
    host._show_library_entries = lambda items: host.library_shown.append(items)
    return host


def _change_identity(host, identity_id):
    host.service.current_identity = NS(identity_id=identity_id)
    host._identity_context_generation += 1


def test_recent_refresh_scopes_work_and_restarts_after_identity_change():
    host = _harness()
    host.refresh_recent()
    alice = host.runner.requests[-1]
    _change_identity(host, "bob")
    assert alice.work() == [{"identity_id": "alice"}]
    alice.ok(alice.work())
    assert host.recent_shown == []
    assert len(host.runner.requests) == 2

    bob = host.runner.requests[-1]
    assert bob.work() == [{"identity_id": "bob"}]
    bob.ok(bob.work())
    assert host.recent_shown == [[{"identity_id": "bob"}]]
    assert host._recent_refresh_pending is False


def test_recent_error_from_previous_identity_is_neither_reported_nor_displayed():
    host = _harness()
    host.refresh_recent(control=object())
    alice = host.runner.requests[-1]
    _change_identity(host, "bob")
    alice.err(RuntimeError("private old identity failure"))
    assert host.recent_shown == []
    assert len(host.runner.requests) == 2
    assert host.runner.requests[-1].work() == [{"identity_id": "bob"}]


@pytest.mark.parametrize("method, shown", [
    ("refresh_recent", "recent_shown"),
    ("refresh_library", "library_shown"),
])
def test_identity_a_b_a_cannot_render_results_from_previous_generation(method, shown):
    host = _harness()
    getattr(host, method)()
    alice = host.runner.requests[-1]
    _change_identity(host, "bob")
    _change_identity(host, "alice")
    alice.ok([{"identity_id": "alice", "old": True}])
    assert getattr(host, shown) == []
    assert len(host.runner.requests) == 2
    fresh = host.runner.requests[-1]
    fresh.ok([{"identity_id": "alice", "new": True}])
    assert getattr(host, shown) == [[{"identity_id": "alice", "new": True}]]


@pytest.mark.parametrize("callback", ["ok", "err"])
def test_library_stale_result_or_error_reschedules_active_identity(callback):
    host = _harness()
    host.refresh_library()
    old = host.runner.requests[-1]
    _change_identity(host, "bob")
    if callback == "ok":
        old.ok([{"identity_id": "alice"}])
    else:
        old.err(RuntimeError("private failure"))
    assert host.library_shown == []
    assert len(host.runner.requests) == 2
    new = host.runner.requests[-1]
    assert new.work() == [{"identity_id": "bob"}]
    new.ok(new.work())
    assert host.library_shown == [[{"identity_id": "bob"}]]


def test_identity_change_clears_recent_list_and_selected_detail_immediately():
    tree = ast.parse(SOURCE.with_name("identity_activation_controller.py").read_text(encoding="utf-8"))
    method = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == "activate_identity")
    body = ast.unparse(method)
    assert "window._recent_entries_cache = []" in body
    assert "window._recent_objects = {}" in body
    assert "window.recent_page.clear_rows()" in body
    assert 'self.recent_page.detail.empty(' in body
