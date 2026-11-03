"""#4277: passive GTK reads must not acquire user-modal task origin."""
from __future__ import annotations

import ast
from pathlib import Path


MAIN_WINDOW = (
    Path(__file__).resolve().parents[1]
    / "src/cifereca/gui/main_window.py"
)


def _submit(method_name, kind):
    module = ast.parse(MAIN_WINDOW.read_text(encoding="utf-8"))
    methods = [
        node for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    ]
    assert len(methods) == 1
    submissions = []
    for node in ast.walk(methods[0]):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "submit":
            continue
        fields = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        tagged = fields.get("kind")
        if isinstance(tagged, ast.Constant) and tagged.value == kind:
            submissions.append(fields)
    assert len(submissions) == 1
    return submissions[0]


def test_cache_only_detail_enrichment_has_automatic_origin():
    submission = _submit("_request_cached_book_details", "detail-cache")
    origin = submission["origin"]
    assert isinstance(origin, ast.Constant)
    assert origin.value == "automatic"


def test_history_origin_distinguishes_navigation_from_explicit_refresh():
    submission = _submit("refresh_history", "history")
    origin = submission["origin"]
    assert isinstance(origin, ast.IfExp)
    assert isinstance(origin.test, ast.Compare)
    assert isinstance(origin.test.left, ast.Name)
    assert origin.test.left.id == "control"
    assert len(origin.test.ops) == 1
    assert isinstance(origin.test.ops[0], ast.IsNot)
    assert len(origin.test.comparators) == 1
    assert isinstance(origin.test.comparators[0], ast.Constant)
    assert origin.test.comparators[0].value is None
    assert isinstance(origin.body, ast.Constant)
    assert origin.body.value == "user"
    assert isinstance(origin.orelse, ast.Constant)
    assert origin.orelse.value == "automatic"
