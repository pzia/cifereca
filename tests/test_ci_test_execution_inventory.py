"""#5376: actual pytest outcomes are not collection markers or case counts."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from scripts.ci_test_execution_inventory import (
    ExecutionCapture, make_execution_report, main, outcome_for,
    write_verified_execution,
)


def _item(nodeid, **markers):
    return {"nodeid": nodeid, "skip_marked": bool(markers.get("skip")),
            "xfail_marked": bool(markers.get("xfail"))}


def _phase(outcome, *, wasxfail=False, duration=0.05):
    return {"outcome": outcome, "wasxfail": wasxfail, "duration": duration}


def _capture():
    capture = ExecutionCapture()
    capture.items = [
        _item("tests/test_a.py::test_ok"),
        _item("tests/test_a.py::test_skip", skip=True),
        _item("tests/test_a.py::test_xfail", xfail=True),
        _item("tests/test_a.py::test_xpass", xfail=True),
        _item("tests/test_a.py::test_failure"),
        _item("tests/test_a.py::test_error"),
    ]
    capture.phases = {
        "tests/test_a.py::test_ok": {
            "setup": _phase("passed"), "call": _phase("passed"),
            "teardown": _phase("passed"),
        },
        "tests/test_a.py::test_skip": {
            "setup": _phase("skipped"), "teardown": _phase("passed"),
        },
        "tests/test_a.py::test_xfail": {
            "setup": _phase("passed"),
            "call": _phase("skipped", wasxfail=True),
            "teardown": _phase("passed"),
        },
        "tests/test_a.py::test_xpass": {
            "setup": _phase("passed"),
            "call": _phase("passed", wasxfail=True),
            "teardown": _phase("passed"),
        },
        "tests/test_a.py::test_failure": {
            "setup": _phase("passed"),
            "call": _phase("failed"),
            "teardown": _phase("passed"),
        },
        "tests/test_a.py::test_error": {
            "setup": _phase("failed"),
            "teardown": _phase("passed"),
        },
    }
    return capture


@pytest.mark.parametrize(("phases", "expected"), [
    ({"setup": _phase("skipped")}, "skipped"),
    ({"setup": _phase("skipped", wasxfail=True)}, "xfailed"),
    ({"call": _phase("skipped", wasxfail=True)}, "xfailed"),
    ({"call": _phase("passed", wasxfail=True)}, "xpassed"),
    ({"call": _phase("failed")}, "failed"),
    ({"setup": _phase("failed")}, "error"),
    ({"call": _phase("passed"), "teardown": _phase("failed")}, "error"),
    ({"setup": _phase("passed")}, None),
])
def test_terminal_phase_precedence(phases, expected):
    assert outcome_for(phases) == expected


def test_runtime_counts_are_distinct_from_selected_and_marked_cases():
    capture = _capture()
    capture.deselected = ["tests/test_b.py::test_unselected"]
    report = make_execution_report(
        capture, {}, suite="tests", source_sha="a" * 40,
        exit_code=1, clean_head=True, selection_scoped=True,
    )
    assert report["complete"] is True
    assert report["selection_scoped"] is True
    assert report["count_selected"] == 6
    assert report["count_deselected"] == 1
    assert report["count_attempted"] == 6
    assert report["count_executed"] == 4
    assert report["outcomes"] == {
        "passed": 1, "failed": 1, "error": 1, "skipped": 1,
        "xfailed": 1, "xpassed": 1,
    }
    assert len(report["items"]) == 6
    assert sum(row["count"] for row in report["matrix"]) == 6
    assert all(row["owner_architecture"] == "unclassified" for row in report["items"])


def test_actual_outcome_is_not_a_marker_prediction():
    capture = ExecutionCapture()
    capture.items = [_item("tests/test_a.py::test_skipped", xfail=True)]
    capture.phases = {
        "tests/test_a.py::test_skipped": {
            "setup": _phase("skipped"), "teardown": _phase("passed"),
        }
    }
    report = make_execution_report(
        capture, {}, suite="tests", source_sha="b" * 40,
        exit_code=0, clean_head=True,
    )
    assert report["outcomes"]["skipped"] == 1
    assert report["outcomes"]["xfailed"] == 0
    assert report["count_executed"] == 0
    assert report["complete"]


def test_pytest_hook_captures_phases_without_private_failure_details():
    capture = ExecutionCapture()
    capture.pytest_runtest_logreport(NS(
        nodeid="tests/test_a.py::test_case", when="call", outcome="failed",
        duration=0.2, longrepr="TOKEN=secret", capstdout="TOKEN=secret",
    ))
    assert capture.phases == {
        "tests/test_a.py::test_case": {
            "call": {"outcome": "failed", "wasxfail": False, "duration": 0.2},
        }
    }
    assert "secret" not in repr(capture.phases)
    capture.pytest_runtest_logreport(NS(
        nodeid="tests/test_a.py::test_case", when="call", outcome="failed",
        duration=0.3,
    ))
    assert capture.duplicate_phases == ["tests/test_a.py::test_case"]


@pytest.mark.parametrize("alter", [
    "missing_runtime",
    "collection_error",
    "aborted",
    "contradictory_exit",
    "duplicate_nodeid",
    "duplicate_phase",
    "outside_suite",
    "unexpected_nodeid",
])
def test_incomplete_or_ambiguous_runs_never_publish(tmp_path, alter):
    capture = _capture()
    exit_code = 1
    if alter == "missing_runtime":
        capture.phases.pop("tests/test_a.py::test_ok")
    elif alter == "collection_error":
        capture.collection_errors = 1
    elif alter == "aborted":
        exit_code = 2
    elif alter == "contradictory_exit":
        exit_code = 0
    elif alter == "duplicate_nodeid":
        capture.items.append(capture.items[0])
    elif alter == "duplicate_phase":
        capture.duplicate_phases.append("tests/test_a.py::test_ok")
    elif alter == "outside_suite":
        capture.items[0] = _item("src/plugins/p/tests/test_a.py::test_ok")
    elif alter == "unexpected_nodeid":
        capture.phases["tests/test_extra.py::test_extra"] = {
            "setup": _phase("passed")
        }
    output = tmp_path / "report.json"
    output.write_text('{"last_good":true}\n', encoding="utf-8")
    try:
        report = make_execution_report(
            capture, {}, suite="tests", source_sha="a" * 40,
            exit_code=exit_code, clean_head=True,
        )
    except ValueError:
        pass
    else:
        assert not report["complete"]
        with pytest.raises(ValueError, match="incomplete_execution"):
            write_verified_execution(report, output)
    assert json.loads(output.read_text()) == {"last_good": True}


@pytest.mark.parametrize(("sha", "clean"), [
    (None, True), ("bad_head", True), ("a" * 40, False),
])
def test_report_needs_clean_exact_git_head(tmp_path, sha, clean):
    report = make_execution_report(
        _capture(), {}, suite="tests", source_sha=sha,
        exit_code=1, clean_head=clean,
    )
    output = tmp_path / "report.json"
    output.write_text('{"last_good":true}', encoding="utf-8")
    with pytest.raises(ValueError):
        write_verified_execution(report, output)
    assert json.loads(output.read_text()) == {"last_good": True}


def test_completed_failure_is_publishable_but_not_success(tmp_path):
    report = make_execution_report(
        _capture(), {}, suite="tests", source_sha="a" * 40,
        exit_code=1, clean_head=True,
    )
    output = tmp_path / "report.json"
    write_verified_execution(report, output)
    data = json.loads(output.read_text())
    assert data["exit_code"] == 1
    assert data["complete"]
    assert data["outcomes"]["failed"] == 1
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize("nodeid", [
    "tests/../test.py::test_a",
    "/tmp/test_private.py::test_a",
    "src/plugins/p/tests/test_a.py::test_b",
    "tests/test_nonexistent.py::test_b",
    "-k",
])
def test_cli_rejects_noncanonical_nodeids(monkeypatch, tmp_path, nodeid):
    import scripts.ci_test_execution_inventory as mod
    (tmp_path / "tests").mkdir()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    with pytest.raises(SystemExit, match="2"):
        main(["--suite", "tests", "--nodeid", nodeid,
              "--output", str(tmp_path / "report.json")])


def test_plugin_package_has_explicit_owner_in_execution_matrix():
    capture = ExecutionCapture()
    nodeid = "src/plugins/provider-x/tests/test_provider.py::test_case[0]"
    capture.items = [_item(nodeid)]
    capture.phases = {
        nodeid: {"setup": _phase("passed"), "call": _phase("passed")}
    }
    report = make_execution_report(
        capture, {}, suite="src/plugins/provider-x/tests",
        source_sha="b" * 40, exit_code=0, clean_head=True,
    )
    assert report["matrix"] == [{
        "owner_architecture": "plugin:provider-x",
        "business_object": "unclassified",
        "test_level": "unclassified",
        "outcome": "passed",
        "count": 1,
    }]


def test_missing_teardown_never_passes_off_as_complete():
    capture = ExecutionCapture()
    capture.items = [_item("tests/test_a.py::test_a")]
    capture.phases = {
        "tests/test_a.py::test_a": {
            "setup": _phase("passed"), "call": _phase("passed"),
        },
    }
    report = make_execution_report(
        capture, {}, suite="tests", source_sha="a" * 40,
        exit_code=0, clean_head=True,
    )
    assert not report["complete"]


def test_pytest_subprocess_reports_real_pass_skip_xfail_and_xpass(tmp_path):
    """Exercise real pytest hooks without recursive main-suite collection."""
    import os
    import subprocess
    import sys

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_cases.py").write_text(
        'import pytest\n'
        'def test_pass():\n    assert True\n'
        '@pytest.mark.skip(reason="synthetic")\n'
        'def test_skip():\n    assert False\n'
        '@pytest.mark.xfail(reason="synthetic")\n'
        'def test_xfail():\n    assert False\n'
        '@pytest.mark.xfail(reason="synthetic")\n'
        'def test_xpass():\n    assert True\n',
        encoding="utf-8",
    )
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(root), env.get("PYTHONPATH", "")]
    )
    program = (
        "import json, pytest\n"
        "from scripts.ci_test_execution_inventory import "
        "ExecutionCapture, make_execution_report\n"
        "capture = ExecutionCapture()\n"
        "code = int(pytest.main(['-q', 'tests/test_cases.py'], "
        "plugins=[capture]))\n"
        "report = make_execution_report(capture, {}, suite='tests', "
        "source_sha='a'*40, exit_code=code, clean_head=True)\n"
        "print('EXEC_JSON:' + json.dumps({key: report[key] for key in "
        "('count_selected', 'count_attempted', 'count_executed', "
        "'outcomes', 'complete')}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program], cwd=tmp_path,
        env=env, text=True, capture_output=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout[-1800:] + result.stderr[-1200:]
    matches = [
        line.split("EXEC_JSON:", 1)[1]
        for line in result.stdout.splitlines() if line.startswith("EXEC_JSON:")
    ]
    assert len(matches) == 1
    report = json.loads(matches[0])
    assert report == {
        "count_selected": 4, "count_attempted": 4, "count_executed": 3,
        "complete": True,
        "outcomes": {
            "passed": 1, "failed": 0, "error": 0, "skipped": 1,
            "xfailed": 1, "xpassed": 1,
        },
    }
