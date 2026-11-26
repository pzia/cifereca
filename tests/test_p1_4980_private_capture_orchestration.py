"""#4980 private capture orchestration; no real GTK or local private data."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

PATH = Path(__file__).resolve().parents[1] / "e2e/notice_c_private_capture.py"
SPEC = importlib.util.spec_from_file_location("notice_c_private_capture", PATH)
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)
HEAD = "a" * 40


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.setattr(capture, "ROOT", checkout)
    monkeypatch.setattr(capture, "clean_head", lambda: HEAD)
    calls = []

    def run(args, **kwargs):
        scenario = args[args.index("--scenario") + 1]
        theme = args[args.index("--appearance") + 1]
        folder = Path(args[args.index("--out") + 1]) / scenario
        folder.mkdir()
        (folder / "screen.json").write_text(json.dumps({
            "status": "PASS", "git_sha": HEAD, "scenario": "book-detail-gtk",
            "backend": "GTK4/X11/XTEST", "width": int(scenario.split("-")[-1]),
            "local_c_stage": "complete", "steps": [{"appearance": theme}],
        }))
        calls.append((theme, scenario, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(capture.subprocess, "run", run)
    return tmp_path / "private", calls


def test_four_exact_runs_private_and_not_automatic_visual_approval(prepared):
    root, calls = prepared
    result = capture.capture_matrix(root, python="/fictional/python")
    assert result == {"status": "CAPTURES_READY_FOR_PRIVATE_REVIEW",
                      "git_sha": HEAD, "scenario_count": 4}
    assert [(mode, name) for mode, name, _ in calls] == [
        (mode, f"book-detail-{width}") for mode, width in capture.MATRIX
    ]
    assert all(kw["stdout"] == kw["stderr"] == capture.subprocess.DEVNULL
               for _, _, kw in calls)
    assert root.stat().st_mode & 0o777 == 0o700
    assert all((root / mode).stat().st_mode & 0o777 == 0o700
               for mode in ("light", "dark"))
    with pytest.raises(capture.CaptureError):
        capture.capture_matrix(root, python="unused")
    assert len(calls) == 4


def test_capture_refuses_ci_and_checkout_change(prepared, monkeypatch):
    root, calls = prepared
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(capture.CaptureError):
        capture.capture_matrix(root, python="unused")
    assert not root.exists() and not calls
    monkeypatch.delenv("GITHUB_ACTIONS")
    heads = iter([HEAD, HEAD, "b" * 40])
    monkeypatch.setattr(capture, "clean_head", lambda: next(heads))
    with pytest.raises(capture.CaptureError):
        capture.capture_matrix(root, python="unused")
    assert len(calls) == 1
    assert root.is_dir()  # Preserve failed evidence privately; never overwrite.


def test_capture_refuses_wrong_observed_theme(prepared, monkeypatch):
    root, calls = prepared
    actual = capture.subprocess.run

    def wrong_theme(args, **kwargs):
        result = actual(args, **kwargs)
        path = root / "light/book-detail-1366/screen.json"
        report = json.loads(path.read_text())
        report["steps"][0]["appearance"] = "dark"
        path.write_text(json.dumps(report))
        return result

    monkeypatch.setattr(capture.subprocess, "run", wrong_theme)
    with pytest.raises(capture.CaptureError):
        capture.capture_matrix(root, python="unused")
    assert len(calls) == 1
