"""Headless regressions for confirmed BnF borrow action refresh (#4138)."""
import ast
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "src/cifereca/gui/detail_panel.py"
CONTROLLER = ROOT / "src/cifereca/gui/bnf_borrow_controller.py"


def _function(path, name, owner=None):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = tree.body
    if owner:
        nodes = next(n.body for n in nodes
                     if isinstance(n, ast.ClassDef) and n.name == owner)
    return next(n for n in nodes
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == name)


def test_reprojection_reads_only_exact_visible_detail():
    method = _function(PANEL, "refresh_confirmed_borrow_detail", "DetailPanel")
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                 str(PANEL), "exec"), namespace)
    refresh = namespace["refresh_confirmed_borrow_detail"]
    item = object()
    other = object()
    detail = object()
    rendered = []
    panel = SimpleNamespace(
        _rendered_search_detail=(item, detail),
        show_item_detail=lambda *args: rendered.append(args),
    )
    assert refresh(panel, other) is False
    assert rendered == []
    assert refresh(panel, item) is True
    assert rendered == [(item, detail)]
    panel._rendered_search_detail = None
    assert refresh(panel, item) is False
    assert rendered == [(item, detail)]


def test_presentations_invalidate_previous_detail_and_cache_after_full_render():
    show = _function(PANEL, "show_presentation", "DetailPanel")
    empty = _function(PANEL, "empty", "DetailPanel")
    detail = _function(PANEL, "show_item_detail", "DetailPanel")
    def _assignments(node):
        return [n for n in ast.walk(node) if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Attribute) and
                        t.attr == "_rendered_search_detail" for t in n.targets)]
    assert any(isinstance(n.value, ast.Constant) and n.value.value is None
               for n in _assignments(show))
    assert any(isinstance(n.value, ast.Constant) and n.value.value is None
               for n in _assignments(empty))
    assert any(isinstance(n.value, ast.Tuple) and len(n.value.elts) == 2
               for n in _assignments(detail))


def test_controller_refresh_requires_confirmed_bnf_and_current_scope():
    method = _function(CONTROLLER, "_present_manual_borrow")
    calls = [n for n in ast.walk(method) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "current"]
    assert calls, "Callback must retain context guards"
    text = ast.get_source_segment(CONTROLLER.read_text(encoding="utf-8"), method)
    assert 'result.get("status") == "confirmed"' in text
    assert 'result.get("provider_verified") is True' in text
    assert 'provider_id == "bnf-pret" and current()' in text
    assert 'refresh(item)' in text
    assert 'window.refresh_loans(True)' in text
