"""P1 #4664: a detached or superseded exact-detail button cannot obtain bytes.

Use MainWindow's real obtain handler and DetailPanel's real action cleanup
without GTK/network. No fake rights, provider session or file writes.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import Cancelled


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"


def _method(path, name, *, cls=None, scope=None):
    source = ROOT / path
    tree = ast.parse(source.read_text(encoding="utf-8"))
    owner = next(
        (node for node in tree.body
         if isinstance(node, ast.ClassDef) and node.name == cls),
        None,
    ) if cls else None
    block = owner.body if owner else tree.body
    fn = next(node for node in block
              if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(
            module="__future__",
            names=[ast.alias(name="annotations")], level=0,
        ),
        fn,
    ], type_ignores=[]))
    namespace = {
        "__package__": "cifereca.gui",
        "TaskPriority": NS(USER_ACTION=7),
        **(scope or {}),
    }
    exec(compile(module, str(source), "exec"), namespace)
    return namespace[name]


def _fixture():
    jobs, obtained, activity = [], [], []
    service = NS(
        _source_context_epoch=5,
        current_identity=NS(identity_id="alice"),
        obtain_search_item=lambda item: (
            obtained.append(item) or NS(
                path=None, acquisition=NS(action="command"),
            )
        ),
    )
    window = NS(
        service=service, _closed=False,
        _identity_context_generation=8,
        runner=NS(submit=lambda *args, **kwargs: jobs.append((args, kwargs))),
        _activity_result=activity.append,
    )
    control = NS(get_root=lambda: object(), get_mapped=lambda: True)
    return window, control, jobs, obtained, activity


def test_real_handler_runs_only_after_explicit_action_and_live_lease():
    window, control, jobs, obtained, activity = _fixture()
    dispatch = _method("main_window.py", "download_direct_item", cls="MainWindow")
    item = NS(source_provider="fixture")
    assert dispatch(window, item, control) is None
    assert len(jobs) == 1 and jobs[0][1]["kind"] == "obtain"
    assert jobs[0][1]["priority"] == 7 and jobs[0][0][0].startswith("Obtention")
    assert obtained == [] and activity == []
    args, kwargs = jobs[0]
    result = args[1]()
    assert obtained == [item] and activity == []
    args[2](result)
    assert activity == ["Commande d’obtention terminée"]


@pytest.mark.parametrize("stale", ["revoked", "unmapped", "detached"])
def test_old_or_unmapped_button_cannot_queue_an_obtain(stale):
    window, control, jobs, obtained, _ = _fixture()
    if stale == "revoked":
        control._cifereca_action_context_revoked = True
    elif stale == "unmapped":
        control.get_mapped = lambda: False
    else:
        control.get_root = lambda: None
    dispatch = _method("main_window.py", "download_direct_item", cls="MainWindow")
    dispatch(window, NS(source_provider="fixture"), control)
    assert jobs == [] and obtained == []


def test_actual_detail_cleanup_revokes_queued_work_and_late_success():
    window, control, jobs, obtained, activity = _fixture()
    dispatch = _method("main_window.py", "download_direct_item", cls="MainWindow")
    clear = _method("detail_panel.py", "_clear_actions", cls="DetailPanel")
    dispatch(window, NS(source_provider="fixture"), control)
    assert len(jobs) == 1
    panel = NS(
        _action_buttons=[control],
        action_scroll=NS(clear=lambda: None, set_visible=lambda _: None),
    )
    clear(panel)
    assert control._cifereca_action_context_revoked is True
    args, _kwargs = jobs[0]
    with pytest.raises(Cancelled):
        args[1]()
    args[2](NS(path=None, acquisition=NS(action="command")))
    dispatch(window, NS(source_provider="fixture"), control)
    assert len(jobs) == 1 and obtained == [] and activity == []


def test_source_epoch_and_a_b_a_revoked_without_new_permission():
    window, control, jobs, obtained, activity = _fixture()
    dispatch = _method("main_window.py", "download_direct_item", cls="MainWindow")
    item = NS(source_provider="fixture")
    dispatch(window, item, control)
    window.service._source_context_epoch += 1
    with pytest.raises(Cancelled):
        jobs[0][0][1]()
    jobs[0][0][2](NS(path=None, acquisition=NS(action="command")))
    assert obtained == [] and activity == []
    dispatch(window, item, control)
    assert len(jobs) == 2
    window.service.current_identity = NS(identity_id="bob")
    window.service.current_identity = NS(identity_id="alice")
    window._identity_context_generation += 2
    with pytest.raises(Cancelled):
        jobs[1][0][1]()
    assert obtained == []


def test_cached_extension_refresh_retains_live_core_action_but_revokes_removed_one():
    clear = _method("detail_panel.py", "_clear_actions", cls="DetailPanel")
    update = _method(
        "detail_panel.py", "update_cached_book_details", cls="DetailPanel",
    )
    base = NS(_cifereca_action_id="download-core")
    removed = NS(_cifereca_action_id="book-cache:old:action")
    kept = []
    panel = NS(
        _cached_section_widgets=[],
        _cached_action_ids={"book-cache:old:action"},
        _action_buttons=[base, removed],
        _clear_actions=None,
        _actions=lambda buttons: kept.extend(buttons),
        action_scroll=NS(clear=lambda: None, set_visible=lambda _: None),
    )
    panel._clear_actions = lambda: clear(panel)
    update(panel, ())
    assert base._cifereca_action_context_revoked is False
    assert removed._cifereca_action_context_revoked is True
    assert kept == [base]


@pytest.mark.parametrize("mode", ["loaded", "known"])
def test_exact_in_panel_action_precedes_legacy_search_redirect_at_narrow_width(mode):
    priority = _method("detail_panel.py", "_detail_action_priority", cls="DetailPanel")
    inline = NS(
        _cifereca_action_id=f"recommendation.{mode}-in-place.17",
        _cifereca_action_label="Consulter disponibilités et actions ici",
    )
    redirect = NS(
        _cifereca_action_id=f"recommendation.{mode}-notice.17",
        _cifereca_action_label="Voir les actions dans Recherche",
    )
    assert priority(inline) == 9
    assert priority(redirect) > priority(inline)
