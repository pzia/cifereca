"""P1 #4277: route-driven reads are not explicit error-modal requests.

TaskRunner's tested origin contract distinguishes automatic callbacks from
user actions. This AST test checks the actual GTK submission sites without
initializing GTK or a provider session.
"""
from __future__ import annotations

import ast
from pathlib import Path


GUI = Path(__file__).resolve().parents[1] / "src/cifereca/gui"


def _submissions(path, kind):
    module = ast.parse((GUI / path).read_text(encoding="utf-8"))
    matches = []
    for node in ast.walk(module):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "submit":
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
        target = keywords.get("kind")
        if isinstance(target, ast.Constant) and target.value == kind:
            matches.append((node.lineno, keywords))
    return [keywords for _line, keywords in sorted(matches)]


def _string_keyword(submission, field):
    value = submission.get(field)
    assert isinstance(value, ast.Constant), "Task origin must be explicit"
    assert type(value.value) is str
    return value.value


def test_passive_search_detail_loading_is_not_user_modal_origin():
    submissions = list(_submissions("search_details.py", "holdings"))
    assert len(submissions) == 2
    assert _string_keyword(submissions[0], "origin") == "automatic"
    # The separate "Disponibilités / téléchargement" button is a user action.
    assert _string_keyword(submissions[1], "origin") == "user"


def test_local_catalogue_snapshot_invalidation_is_automatic():
    submissions = list(_submissions("search_snapshot_refresh.py", "search-known"))
    assert len(submissions) == 1
    assert _string_keyword(submissions[0], "origin") == "automatic"


def test_loan_refresh_origin_follows_presence_of_an_explicit_control():
    submissions = list(_submissions("loans_controller.py", "loans"))
    assert len(submissions) == 1
    origin = submissions[0].get("origin")
    assert isinstance(origin, ast.IfExp)
    assert isinstance(origin.test, ast.Compare)
    assert isinstance(origin.test.left, ast.Name)
    assert origin.test.left.id == "control"
    assert len(origin.test.ops) == 1 and isinstance(origin.test.ops[0], ast.IsNot)
    assert len(origin.test.comparators) == 1
    assert isinstance(origin.test.comparators[0], ast.Constant)
    assert origin.test.comparators[0].value is None
    assert origin.body.value == "user"
    assert origin.orelse.value == "automatic"
