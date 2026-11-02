"""#4048: no voluntary loan return can be initiated from book/loan GTK cards.

Static inspection is deliberate: tests catch a newly introduced callback even
when no supplier is connected, without executing ADEPT or provider HTTP.
Read-only historical and provenance projections are explicitly preserved.
"""
from __future__ import annotations

import ast
from pathlib import Path

GUI = Path(__file__).resolve().parents[1] / "src/cifereca/gui"


def _method(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == name)


def test_all_loan_detail_paths_absent_from_provider_and_adept_return_actions():
    owner = GUI / "detail_panel.py"
    show = _method(owner, "show_loan")
    body = ast.unparse(show)
    assert "confirm_return" not in body
    assert "return_adept_from_detail" not in body
    assert "Rendre maintenant" not in body
    assert "Restituer le prêt ADEPT" not in body
    assert "experimental_actions" not in body
    assert "adept_return_status_for_loan" in body  # read-only provenance
    assert "reconcile_adept_from_detail" in body  # read-only check
    assert "État du prêt ADEPT (lecture seule)" in body


def test_facet_buttons_are_filters_only_no_adept_return():
    owner = GUI / "detail_facets_controller.py"
    add_facets = _method(owner, "add_facets")
    body = ast.unparse(add_facets)
    assert "return_adept_acquisition" not in body
    assert "adept_return_for_artifact" not in body
    assert "runner.submit" not in body
    assert "window._facet_values" in body
    assert "window._facet_value_group" in body


def test_legacy_gtk_mutation_entrypoints_are_absent_from_class_and_module():
    main = ast.parse((GUI / "main_window.py").read_text(encoding="utf-8"))
    host = next(n for n in main.body if isinstance(n, ast.ClassDef)
                and n.name == "MainWindow")
    forbidden = {
        "confirm_return", "_return_loan_result", "_return_loan",
        "_return_loan_pnb", "return_adept_from_detail",
    }
    methods = {n.name for n in host.body if isinstance(n, ast.FunctionDef)}
    assert not forbidden.intersection(methods)
    assert {"confirm_renew", "_renew", "reconcile_adept_from_detail"} <= methods

    controller = ast.parse((GUI / "loans_controller.py").read_text(encoding="utf-8"))
    functions = {n.name for n in controller.body if isinstance(n, ast.FunctionDef)}
    assert not forbidden.intersection(functions)
    assert {"confirm_renew", "_renew", "_renew_result"} <= functions

    for tree in (main, controller):
        calls = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert "return_adept_acquisition" not in calls
        assert "return_loan_prefer_adept" not in calls
        assert "return_digital" not in calls
    # No disabled/hidden trigger: direct invocation must also be impossible.
    for text in ((GUI / "main_window.py").read_text(encoding="utf-8"),
                 (GUI / "loans_controller.py").read_text(encoding="utf-8")):
        assert 'kind="loan-return"' not in text
        assert 'kind="adept-return"' not in text
