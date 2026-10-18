"""P0 #1143: GTK task outcome/log boundary never repeats raw provider errors."""
from __future__ import annotations

import ast
import itertools
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.plugin_api import AttentionRequired, UserFacingError

SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/task_runner.py"
TRACE = logging.getLogger("cifereca-task-error-privacy-tests")
SECRET = "SECRET_TOKEN_https://private.invalid?bearer=abc"


def _method(name, *, glib=None):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(item for item in tree.body
               if isinstance(item, ast.ClassDef) and item.name == "TaskRunner")
    fn = next(item for item in cls.body
              if isinstance(item, ast.FunctionDef) and item.name == name)
    mod = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        fn,
    ], type_ignores=[]))
    globals_ = {
        "_TRACE_LOG": TRACE,
        "GLib": glib, "TaskPriority": NS(NORMAL=40),
        "time": time, "UserFacingError": UserFacingError,
        "AttentionRequired": AttentionRequired,
    }
    exec(compile(mod, str(SOURCE), "exec"), globals_)
    return globals_[name]


def test_present_error_never_logs_code_or_private_technical_detail(caplog):
    present_error = _method("present_error")
    events = []
    runner = NS(window=NS(
        show_attention=lambda *a, **kw: events.append(("attention", a, kw)),
        show_error=lambda *a, **kw: events.append(("error", a, kw)),
    ))
    with caplog.at_level(logging.WARNING, logger=TRACE.name):
        present_error(runner, AttentionRequired(
            "Authentification à corriger", code="CODE_PRIVATE_" + SECRET,
            technical_detail=SECRET,
        ))
        present_error(runner, UserFacingError(
            "Service indisponible", code="CODE_PRIVATE_" + SECRET,
            technical_detail=SECRET,
        ))
        present_error(runner, RuntimeError("untrusted provider error " + SECRET))
    assert [event[0] for event in events] == ["attention", "error", "error"]
    assert events[0][1] == ("Authentification à corriger",)
    assert events[1][1] == ("Service indisponible",)
    assert events[2][1] == ("Traitement indisponible",)
    assert SECRET not in caplog.text
    assert "CODE_PRIVATE" not in caplog.text
    assert "detail_present=True" in caplog.text


def test_task_completion_never_passes_raw_exception_to_activity_ui():
    idle = []
    glib = NS(idle_add=lambda fn, *a, **kw: idle.append((fn, a)),
              PRIORITY_DEFAULT=None)
    submit = _method("submit", glib=glib)
    calls = []
    window = NS(
        _closed=False,
        _task_started=lambda *a: calls.append(("start", a)),
        _task_finished=lambda *a: calls.append(("end", a)),
    )

    class ImmediateFuture:
        def result(self):
            raise RuntimeError("https://private.invalid/?token=" + SECRET)
        def add_done_callback(self, callback):
            callback(self)

    runner = NS(window=window, pool=NS(submit=lambda *_args: ImmediateFuture()),
                _count=0, _lock=threading.Lock(), _task_ids=itertools.count(1),
                present_error=lambda *_args: None)
    submit(runner, "an operation", lambda: None)
    assert len(idle) == 2
    for fn, args in idle:
        fn(*args)
    end = next(args for kind, args in calls if kind == "end")
    assert end[3] is False
    assert end[4] == "Traitement indisponible"
    assert SECRET not in repr(calls)


def test_task_completion_preserves_only_typed_user_message():
    idle = []
    glib = NS(idle_add=lambda fn, *a, **kw: idle.append((fn, a)),
              PRIORITY_DEFAULT=None)
    submit = _method("submit", glib=glib)
    calls = []
    window = NS(_closed=False,
                _task_started=lambda *a: None,
                _task_finished=lambda *a: calls.append(a))

    class ImmediateFuture:
        def result(self):
            raise UserFacingError(
                "Connexion à rétablir", code="auth_required",
                technical_detail=SECRET,
            )
        def add_done_callback(self, callback):
            callback(self)

    runner = NS(window=window, pool=NS(submit=lambda *_args: ImmediateFuture()),
                _count=0, _lock=threading.Lock(), _task_ids=itertools.count(1),
                present_error=lambda *_args: None)
    submit(runner, "an operation", lambda: None)
    for fn, args in idle:
        fn(*args)
    assert calls[0][4] == "Connexion à rétablir"
    assert SECRET not in repr(calls)
