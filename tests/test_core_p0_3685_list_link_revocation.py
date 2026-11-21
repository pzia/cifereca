"""#3685: detached facet and author links cannot activate after a cell rebind."""
from __future__ import annotations

import ast
from types import SimpleNamespace as NS

import pytest

from tests.test_p1_3104_aba_cover_cell import _bind_ast


def _production_guards():
    bind = _bind_ast()
    guard = next(node for node in bind.body
                 if isinstance(node, ast.FunctionDef) and node.name == "guard_link")
    author = next(node for node in ast.walk(bind)
                  if isinstance(node, ast.FunctionDef) and node.name == "activate"
                  and len(node.args.args) == 3)
    factory = ast.FunctionDef(
        name="for_binding",
        args=ast.arguments(
            posonlyargs=[], args=[ast.arg(arg=name) for name in (
                "self", "row_key", "binding_epoch", "callback",
            )], vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[],
        ),
        body=[guard, author, ast.Return(value=ast.Tuple(
            elts=[ast.Name(id="guard_link", ctx=ast.Load()),
                  ast.Name(id="activate", ctx=ast.Load())],
            ctx=ast.Load(),
        ))],
        decorator_list=[],
    )
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[factory], type_ignores=[])), "<book-row-links>", "exec"), namespace)
    return namespace["for_binding"]


@pytest.mark.parametrize("surface", ["typed-facet", "legacy-facet", "author"])
@pytest.mark.parametrize("transition", ["different", "same", "aba", "unbind"])
def test_old_child_link_is_inert_after_rebinding(surface, transition):
    cell = NS(_bound_key="A", _bind_epoch=1)
    calls = []
    factory = _production_guards()

    def link(key, epoch, callback):
        guard, author = factory(cell, key, epoch, callback)
        if surface == "author":
            def invoke():
                assert author(None, "author") is True
            return invoke
        return lambda: guard(callback)(None)

    old = link("A", 1, lambda _button: calls.append("old"))
    old()
    assert calls == ["old"]
    cell._bind_epoch += 1
    cell._bound_key = {"different": "B", "same": "A", "aba": "B", "unbind": None}[transition]
    if transition == "aba":
        cell._bind_epoch += 1
        cell._bound_key = "A"
    old()
    assert calls == ["old"]
    if cell._bound_key is not None:
        fresh = link(cell._bound_key, cell._bind_epoch, lambda _button: calls.append("fresh"))
        fresh()
        old()
        assert calls == ["old", "fresh"]


def test_invalid_callback_is_passive_without_coercion():
    class Invalid:
        def __bool__(self):
            raise AssertionError("must not coerce callback")
    obj = Invalid()
    guard, author = _production_guards()(NS(_bound_key="A", _bind_epoch=1), "A", 1, obj)
    assert guard(obj) is None
    assert author(None, "author") is True


def test_all_three_bind_surfaces_use_shared_guard():
    source = ast.unparse(_bind_ast())
    assert "guarded = guard_link(callback)" in source
    assert "attribute_tag(label, guard_link(callback))" in source
    assert "def activate(_label, _uri, cb=guard_link(callback))" in source
