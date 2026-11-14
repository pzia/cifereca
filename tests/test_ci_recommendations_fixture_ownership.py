"""#5376: Recommendations test fixtures are not exported by other test_ modules.

Source-only anti-regression. Runtime contracts remain in plugin pytest suites;
this test avoids importing plugin code under a Core-only worker.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "src/plugins/recommendations/tests"


def test_recommendations_test_fixtures_are_stable_uncollected_helpers():
    expected = {
        "recommendations_catalogue_test_fixtures.py": {
            "_view", "_variant", "ISBN_1", "ISBN_2",
        },
        "recommendations_detail_test_fixtures.py": {"_make_view"},
    }
    for file, exports in expected.items():
        path = TESTS / file
        assert path.is_file()
        module = ast.parse(path.read_text(encoding="utf-8"))
        defined = {
            node.name for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        }
        defined.update(
            target.id for node in module.body
            if isinstance(node, ast.Assign)
            for target in node.targets if isinstance(target, ast.Name)
        )
        assert exports <= defined
        assert not any(
            isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
            for node in module.body
        )


def test_recommendations_consumers_no_longer_import_other_test_modules():
    names = (
        "test_recommendation_catalogue_navigation_p1.py",
        "test_recommendation_detail_projection_contract_p1.py",
        "test_verified_catalogue_variants_p1.py",
        "test_recommendation_hidden_selection_p1.py",
        "test_recommendation_source_filter_exact_actions_p1.py",
        "test_attested_score_projection_p1.py",
        "test_recommendation_ranked_provenance_public_p1.py",
    )
    assert (TESTS / "__init__.py").is_file()
    for name in names:
        module = ast.parse((TESTS / name).read_text(encoding="utf-8"))
        local_fixture_imports = [
            node.module for node in ast.walk(module)
            if isinstance(node, ast.ImportFrom)
            and node.level == 1
            and node.module in {
                "recommendations_catalogue_test_fixtures",
                "recommendations_detail_test_fixtures",
            }
        ]
        assert local_fixture_imports, name
        assert not any(
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module
            and node.module.startswith("tests.test_recommendation")
            for node in ast.walk(module)
        ), name
