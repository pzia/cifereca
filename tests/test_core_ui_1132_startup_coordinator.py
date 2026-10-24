"""P0 #1132: extracted startup uses only the injected GTK dispatcher."""
from __future__ import annotations

import ast
from functools import partial
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui import startup_coordinator
from cifereca.task_queue import TaskPriority


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
OWNER = ROOT / "startup_coordinator.py"
MAIN = ROOT / "main_window.py"


def test_owner_imports_are_headless_and_facade_signatures_unchanged():
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(n) for n in ast.walk(tree)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any(x == "import gi" or x.startswith("from gi ") or "main_window" in x or "task_runner" in x for x in imports)
    window = next(n for n in ast.parse(MAIN.read_text(encoding="utf-8")).body
                  if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    functions = {n.name: n for n in window.body if isinstance(n, ast.FunctionDef)}
    for name in ("activate_startup_plugins", "connect_configured_sources"):
        facade = functions[name]
        assert [arg.arg for arg in facade.args.args] == ["self"]
        assert isinstance(facade.body[-1], ast.Return)
        assert ast.unparse(facade.body[-1].value.func) == "startup_coordinator." + name
        assert "idle_add=GLib.idle_add" in ast.unparse(facade)


def test_connection_submit_uses_existing_provider_priority_and_no_implicit_sync():
    submissions = []
    posted = []
    window = NS(
        _closed=False,
        service=NS(connect_configured=lambda: (), last_loans=[]),
        runner=NS(submit=lambda *a, **k: submissions.append((a, k))),
        run_opening_sync=lambda: None,
    )
    assert startup_coordinator.connect_configured_sources(
        window, idle_add=posted.append,
    ) is False
    assert len(submissions) == 1
    args, kw = submissions[0]
    assert isinstance(args[1], partial)
    assert args[1].func == window.service.connect_configured
    assert args[1].keywords == {"caller_category": "startup/connect_configured"}
    assert kw["priority"] == TaskPriority.PROVIDER_SYNC
    assert kw["kind"] == "provider-connect"
    assert posted == []


def test_without_plugins_connection_is_queued_once_not_invoked_eagerly():
    posted, calls = [], []
    window = NS(
        _closed=False,
        plugin_manager=None,
        _connect_sources_if_open=lambda: calls.append("connected"),
    )
    assert startup_coordinator.activate_startup_plugins(
        window, idle_add=posted.append,
    ) is False
    assert len(posted) == 1
    window._closed = True
    # The actual MainWindow callback is guarded by once_while_open.
    # Verify the dispatcher retains that callback rather than invoking it eagerly.
    assert calls == []
    assert posted[0] == window._connect_sources_if_open
