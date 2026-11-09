"""#3104: a reused GTK list cell must reject an old image metadata callback.

Exercise the actual nested callback extracted from BookListCell.bind without
requiring GTK on the CI host. No network, provider, or cache operations.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from tests.gui_source import gui_source_text


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/book_widgets.py"


def _bind_ast():
    tree = ast.parse(gui_source_text(SOURCE))
    owner = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "BookListCell")
    return next(node for node in owner.body
                if isinstance(node, ast.FunctionDef) and node.name == "bind")


def _real_callback():
    bind = _bind_ast()
    resolved = next(node for node in ast.walk(bind)
                    if isinstance(node, ast.FunctionDef) and node.name == "resolved")
    factory = ast.FunctionDef(
        name="callback_for", args=ast.arguments(
            posonlyargs=[], args=[ast.arg(arg=name)
                                   for name in ("self", "bound_key", "binding_epoch", "item")],
            vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[],
        ), body=[resolved, ast.Return(value=ast.Name(id="resolved", ctx=ast.Load()))],
        decorator_list=[],
    )
    scope = {}
    tree = ast.fix_missing_locations(ast.Module(body=[factory], type_ignores=[]))
    exec(compile(tree, str(SOURCE), "exec"), scope)
    return scope["callback_for"]


def test_rebound_same_key_cannot_accept_prior_cover_result():
    calls = []
    host = SimpleNamespace(
        _bound_key="edition-1", _bind_epoch=2, cover_loader=object(),
        cover=SimpleNamespace(
            bind_url=lambda *args, **kwargs: calls.append((args, kwargs))
        ),
    )
    item = SimpleNamespace(default_thumbnail_url=None)
    callback = _real_callback()
    stale = callback(host, "edition-1", 1, item)
    stale("https://private.invalid/old.png")
    assert calls == []

    fresh = callback(host, "edition-1", 2, item)
    fresh("https://public.example/cover.png")
    assert len(calls) == 1
    assert calls[0][0][1] == "https://public.example/cover.png"

    host._bind_epoch += 1
    fresh("https://public.example/late.png")
    assert len(calls) == 1


def test_bind_and_unbind_revoke_generation_even_when_key_reused():
    source = ast.unparse(_bind_ast())
    assert source.index("self._bind_epoch += 1") < source.index("self._bound_key = item.key")
    assert "self._bind_epoch != binding_epoch" in source

    tree = ast.parse(gui_source_text(SOURCE))
    owner = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "BookListCell")
    unbind = next(node for node in owner.body
                  if isinstance(node, ast.FunctionDef) and node.name == "unbind")
    text = ast.unparse(unbind)
    assert text.index("self._bind_epoch += 1") < text.index("self._bound_key = None")
