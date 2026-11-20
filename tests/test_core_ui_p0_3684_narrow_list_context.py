"""Exercise list-context transitions without a display (#3684).

The actual MasterDetailPage methods run against minimal widget doubles; the
GTK geometry/reception gates remain separate.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

SOURCE = (Path(__file__).resolve().parents[1]
          / "src/cifereca/gui/master_detail.py")


def _methods():
    source = ast.parse(SOURCE.read_text(encoding="utf-8"))
    owner = next(node for node in source.body
                 if isinstance(node, ast.ClassDef) and node.name == "MasterDetailPage")
    names = {"_set_narrow_detail_context", "set_documentary_summary"}
    module = ast.Module(body=[node for node in owner.body
                              if isinstance(node, ast.FunctionDef)
                              and node.name in names], type_ignores=[])
    namespace = {}
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return (namespace["_set_narrow_detail_context"],
            namespace["set_documentary_summary"])


class Widget:
    def __init__(self, visible=True, parent=None):
        self.visible = visible
        self.parent = parent
        self.text = ""

    def set_visible(self, value):
        self.visible = value

    def get_visible(self):
        return self.visible

    def get_parent(self):
        return self.parent

    def set_label(self, text):
        self.text = text

    def get_text(self):
        return self.text


def _page(*, detached_actions=True, sidebar_visible=True):
    page = SimpleNamespace(
        page_header=Widget(), toolbar=Widget(),
        _documentary_summary=Widget(False),
        window=SimpleNamespace(sidebar=Widget(sidebar_visible)),
        _narrow_sidebar_restore_visible=None,
    )
    page.header_actions = Widget(
        parent=page if detached_actions else page.page_header,
    )
    return page


def test_detached_actions_and_summary_stay_hidden_during_refresh():
    transition, summary = _methods()
    page = _page()
    bar = page.header_actions
    summary(page, "2 notices")
    assert page._documentary_summary.visible

    transition(page, True)
    assert not page.page_header.visible
    assert not page.toolbar.visible
    assert not bar.visible
    assert not page._documentary_summary.visible
    assert not page.window.sidebar.visible

    summary(page, "3 notices")
    assert page._documentary_summary.text == "3 notices"
    assert not page._documentary_summary.visible

    transition(page, False)
    assert page.page_header.visible and page.toolbar.visible
    assert page.header_actions is bar and bar.visible
    assert page._documentary_summary.visible
    assert page._documentary_summary.text == "3 notices"
    assert page.window.sidebar.visible


def test_inline_actions_keep_own_visibility_and_empty_summary_stays_hidden():
    transition, summary = _methods()
    page = _page(detached_actions=False, sidebar_visible=False)
    page.header_actions.set_visible(False)
    summary(page, "1 notice")
    transition(page, True)
    summary(page, "")
    transition(page, True)
    transition(page, False)
    assert not page.header_actions.visible
    assert not page._documentary_summary.visible
    assert not page.window.sidebar.visible
    assert page._narrow_sidebar_restore_visible is None


def test_unmap_map_restores_list_context_then_hides_it_again():
    transition, summary = _methods()
    page = _page()
    summary(page, "4 notices")
    transition(page, True)
    transition(page, False)  # unmap: release the shared sidebar
    assert page.window.sidebar.visible
    transition(page, True)   # map: restore the active narrow detail
    assert not page.header_actions.visible
    assert not page._documentary_summary.visible
    assert not page.window.sidebar.visible
    transition(page, False)
    assert page._documentary_summary.visible
    assert page.window.sidebar.visible
