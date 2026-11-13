"""#5376: actual collected pytest items, explicit classifications, safe unknowns."""
from __future__ import annotations

import json

import pytest

from scripts.ci_test_collection_inventory import (
    CollectionCapture, classify, make_report, validate_manifest,
)


def _manifest(*rows):
    return {"schema": "cifereca.test-classification/1", "files": list(rows)}


def _row(path, owner="core", business="ci_quality", level="contract"):
    return {"path": path, "owner_architecture": owner,
            "business_object": business, "test_level": level}


def test_explicit_root_classification_preserves_parametrized_case_identity():
    mapping = validate_manifest(_manifest(_row("tests/test_fixture.py")))
    report = make_report([
        {"nodeid": "tests/test_fixture.py::test_case[0]", "skip_marked": False},
        {"nodeid": "tests/test_fixture.py::test_case[1]", "xfail_marked": True},
    ], mapping, suite="tests", source_sha="a" * 40)
    assert report["count_collected"] == 2
    assert report["count_xfail_marked"] == 1
    assert report["executed"] is None
    assert report["passed"] is None
    assert report["source_sha"] == "a" * 40
    assert report["matrix"] == [{
        "owner_architecture": "core", "business_object": "ci_quality",
        "test_level": "contract", "count": 2,
    }]
    assert report["items"][0]["nodeid"].endswith("[0]")


def test_plugin_package_is_objective_but_business_and_level_are_unclassified():
    result = classify("src/plugins/provider-foo/tests/test_source.py::test_foo", {})
    assert result == {
        "owner_architecture": "plugin:provider-foo",
        "business_object": "unclassified",
        "test_level": "unclassified",
    }
    assert classify("tests/test_unmapped.py::test_foo", {}) == {
        "owner_architecture": "unclassified",
        "business_object": "unclassified",
        "test_level": "unclassified",
    }


@pytest.mark.parametrize("rows", [
    [_row("tests/test_a.py"), _row("tests/test_a.py")],
    [_row("tests/test_a.py", owner="plugin:provider-x")],
    [_row("src/plugins/provider-x/tests/test_a.py", owner="plugin:provider-y")],
    [_row("tests/../test_a.py")],
    [_row("tests/test_a.py", business="guessed")],
    [_row("tests/test_a.py", level="end-to-end")],
])
def test_manifest_rejects_unsupported_or_unowned_rules(rows):
    with pytest.raises(ValueError):
        validate_manifest(_manifest(*rows))


def test_duplicate_nodeids_fail_instead_of_counting_same_case_twice():
    with pytest.raises(ValueError, match="duplicate"):
        make_report([
            {"nodeid": "tests/test_a.py::test_case"},
            {"nodeid": "tests/test_a.py::test_case"},
        ], {}, suite="tests")


def test_matrix_does_not_confuse_markers_with_executed_outcomes():
    report = make_report([
        {"nodeid": "tests/test_a.py::test_case", "skip_marked": True,
         "xfail_marked": True},
    ], {}, suite="tests")
    assert report["count_skip_marked"] == 1
    assert report["count_xfail_marked"] == 1
    assert report["skipped_at_runtime"] is None
    assert report["xfailed_at_runtime"] is None
    assert report["count_collected"] == 1


def test_manifest_is_versioned_and_can_be_read_without_collecting(pytestconfig):
    root = pytestconfig.rootpath
    path = root / "docs/test-classification-manifest.json"
    mapping = validate_manifest(json.loads(path.read_text(encoding="utf-8")))
    assert "tests/test_ci_test_dependency_guard.py" in mapping
    assert all(value["business_object"] == "ci_quality" for value in mapping.values())


def test_capture_reads_real_pytest_item_objects_without_running_them():
    class FakeItem:
        def __init__(self, nodeid, keywords):
            self.nodeid = nodeid
            self.keywords = keywords

    class FakeSession:
        items = [FakeItem("tests/test_a.py::test_a[1]", {"skip": True})]

    plugin = CollectionCapture()
    plugin.pytest_collection_finish(FakeSession())
    assert plugin.items == [{
        "nodeid": "tests/test_a.py::test_a[1]",
        "skip_marked": True, "xfail_marked": False,
    }]
