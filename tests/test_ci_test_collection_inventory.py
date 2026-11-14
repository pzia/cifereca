"""#5376: actual collected pytest items, explicit classifications, safe unknowns."""
from __future__ import annotations

import json

import pytest

from scripts.ci_test_collection_inventory import (
    CollectionCapture, classify, load_classifications, main, make_report,
    validate_manifest, write_verified_collection,
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

def test_independent_manifests_merge_and_report_overlapping_claims(tmp_path):
    base = tmp_path / "manifest.json"
    fragments = tmp_path / "manifest.d"
    fragments.mkdir()
    core = fragments / "core.json"
    ui = fragments / "coreui.json"
    base.write_text(json.dumps(_manifest(_row("tests/test_ci.py"))), encoding="utf-8")
    core.write_text(json.dumps(_manifest(_row("tests/test_core.py"))), encoding="utf-8")
    ui.write_text(json.dumps(_manifest(
        _row("tests/test_ui.py", owner="coreui", business="ui"),
    )), encoding="utf-8")
    assert set(load_classifications(base)) == {"tests/test_ci.py"}
    assert set(load_classifications(base, fragments=fragments)) == {
        "tests/test_ci.py", "tests/test_core.py", "tests/test_ui.py",
    }

    ui.write_text(json.dumps(_manifest(_row("tests/test_core.py"))), encoding="utf-8")
    with pytest.raises(ValueError, match=r"tests/test_core.py: .*core.json and .*coreui.json"):
        load_classifications(base, fragments=fragments)


def test_invalid_fragment_reports_its_path(tmp_path):
    base = tmp_path / "manifest.json"
    base.write_text(json.dumps(_manifest()), encoding="utf-8")
    fragments = tmp_path / "manifest.d"
    fragments.mkdir()
    (fragments / "broken.json").write_text("invalid", encoding="utf-8")
    with pytest.raises(ValueError, match="broken.json"):
        load_classifications(base, fragments=fragments)


@pytest.mark.parametrize("exit_code,sha,items", [
    (2, "a" * 40, [{"nodeid": "tests/test_a.py::test_a"}]),
    (0, None, [{"nodeid": "tests/test_a.py::test_a"}]),
    (0, "a" * 40, []),
])
def test_incomplete_collection_never_overwrites_successful_report(
    tmp_path, exit_code, sha, items,
):
    output = tmp_path / "inventory.json"
    output.write_text('{"prior":"successful"}\n', encoding="utf-8")
    report = make_report(items, {}, suite="tests", source_sha=sha)
    with pytest.raises(ValueError):
        write_verified_collection(report, output, collection_exit_code=exit_code)
    assert output.read_text(encoding="utf-8") == '{"prior":"successful"}\n'


def test_verified_report_replaces_previous_json_atomically(tmp_path):
    output = tmp_path / "inventory.json"
    output.write_text("prior", encoding="utf-8")
    report = make_report(
        [{"nodeid": "tests/test_a.py::test_a[1]"}], {},
        suite="tests", source_sha="a" * 40,
    )
    write_verified_collection(report, output, collection_exit_code=0)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["count_collected"] == 1
    assert payload["source_sha"] == "a" * 40
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize("suite", [
    "tests/subdir", "src/plugins/provider-x/tests/nested",
    "src/plugins/../tests", "/tmp/tests", "tests/../../other",
])
def test_cli_rejects_noncanonical_collection_suite(monkeypatch, tmp_path, suite):
    import scripts.ci_test_collection_inventory as module
    monkeypatch.setattr(module, "ROOT", tmp_path)
    with pytest.raises(SystemExit, match="2"):
        main(["--suite", suite, "--output", str(tmp_path / "inventory.json")])


def test_cli_rejects_missing_suite_directory(monkeypatch, tmp_path):
    import scripts.ci_test_collection_inventory as module
    monkeypatch.setattr(module, "ROOT", tmp_path)
    with pytest.raises(SystemExit, match="2"):
        main(["--suite", "tests", "--output", str(tmp_path / "inventory.json")])


def test_failed_collection_preserves_last_good_output(monkeypatch, tmp_path):
    import scripts.ci_test_collection_inventory as module
    suite = tmp_path / "tests"
    suite.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    output = tmp_path / "inventory.json"
    output.write_text("prior good report", encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path)

    def collection_error(argv, *, plugins):
        assert argv == ["--collect-only", "-q", "tests"]
        plugins[0].items = [{"nodeid": "tests/test_a.py::test_a"}]
        return 2

    monkeypatch.setattr(pytest, "main", collection_error)
    assert main([
        "--suite", "tests", "--manifest", str(manifest),
        "--output", str(output),
    ]) == 1
    assert output.read_text(encoding="utf-8") == "prior good report"


def test_explicit_audited_accounting_and_security_classifications(pytestconfig):
    from scripts.ci_test_collection_inventory import MANIFEST

    root = pytestconfig.rootpath
    main = root / "docs/test-classification-manifest.json"
    mapping = load_classifications(main, fragments=main.with_suffix(".d"))
    expected = {
        "tests/test_ci_test_collection_inventory.py": ("core", "ci_quality", "contract"),
        "tests/test_ci_test_execution_inventory.py": ("core", "ci_quality", "integration"),
        "tests/test_ci_test_execution_aggregate.py": ("core", "ci_quality", "contract"),
        "tests/test_ci_security_mutation_campaign.py": ("core", "ci_quality", "integration"),
        "tests/test_public_httpx_transport.py": ("core", "plugin_contracts", "integration"),
        "tests/test_core_p0_3867_attested_https_delivery.py": ("core", "acquisition", "integration"),
    }
    for path, expected_values in expected.items():
        assert (root / path).is_file()
        record = mapping[path]
        assert tuple(record[key] for key in (
            "owner_architecture", "business_object", "test_level"
        )) == expected_values
