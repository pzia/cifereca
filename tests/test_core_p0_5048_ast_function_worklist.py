"""Functional AST grouping of provider signals without runtime imports."""
from scripts.ci_provider_ast_function_worklist import (
    canonical_provider, inventory, responsibility, scope_for_line,
)


def test_canonical_grouping_preserves_independent_providers():
    assert canonical_provider("provider-kobo") == "kobo"
    assert canonical_provider("provider.bnf-pret") == "bnf"
    assert canonical_provider("numilog") == "numilog"
    assert canonical_provider("provider.gallica") == "gallica"
    assert canonical_provider("provider-ebooksgratuits") == "ebooksgratuits"
    assert canonical_provider("provider.biblioaccess.la-rochelle") == (
        "provider.biblioaccess.la-rochelle"
    )


def test_worklist_unique_lines_and_function_provenance(tmp_path):
    root = tmp_path
    source = root / "src" / "cifereca" / "core" / "search_orchestration.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "def check(provider_id):\n"
        "    if provider_id == 'kobo':\n"
        "        return True\n"
        "    if provider_id in {'kobo', 'bnf'}:\n"
        "        return False\n"
        "    return None\n", encoding="utf-8",
    )
    report = inventory(root)
    assert report["parse_errors"] == []
    assert report["unique_lines"] == 2
    assert report["files"] == 1
    assert report["functions"] == 1
    assert report["overlapping_occurrences_by_provider"] == {
        "bnf": 1, "kobo": 2,
    }
    assert report["unique_lines_by_responsibility"] == {"search": 2}
    assert report["top_functions"][0]["function"] == "check"
    assert report["top_functions"][0]["review"] == "unclassified"
    assert all(entry["review"] == "unclassified" for entry in report["entries"])


def test_nested_function_is_smallest_scoped_owner():
    assert scope_for_line([(1, 12, "outer"), (3, 8, "outer.inner")], 4) == (
        "outer.inner"
    )
    assert scope_for_line([(1, 12, "outer")], 20) == "<module>"
    assert responsibility("src/cifereca/gui/detail_panel.py") == "core_ui"


def test_worklist_baseline_ignores_line_shifts_and_exposes_offsetting_changes(
    tmp_path, capsys,
):
    import json

    from scripts.ci_provider_ast_function_worklist import main

    source = tmp_path / "src/cifereca/services.py"
    source.parent.mkdir(parents=True)
    original = (
        "def unchanged(provider_id):\n"
        "    return provider_id == 'kobo'\n"
        "def removed(provider_id):\n"
        "    return provider_id == 'paris'\n"
    )
    source.write_text(original, encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(inventory(tmp_path)), encoding="utf-8")
    source.write_text("\n\n" + original.replace(
        "def removed", "def added"
    ), encoding="utf-8")

    assert main(["--root", str(tmp_path), "--baseline", str(baseline), "--json"]) == 0
    comparison = json.loads(capsys.readouterr().out)["comparison"]
    assert comparison["delta"] == 0
    assert [(row["function"], row["delta"]) for row in comparison["functions"]] == [
        ("added", 1), ("removed", -1),
    ]
