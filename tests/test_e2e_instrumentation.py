from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "e2e/run.py"


def _module():
    spec = importlib.util.spec_from_file_location("cifereca_e2e_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_redaction_masks_named_secret_values_and_inline_credentials():
    runner = _module()
    env = {
        "API_TOKEN": "super-secret-value",
        "HOME": "/tmp/home",
    }
    text = "token=abc password:xyz bearer super-secret-value cookie=session"
    redacted = runner._redact(text, env)

    assert "super-secret-value" not in redacted
    assert "token=[REDACTED]" in redacted
    assert "password:[REDACTED]" in redacted
    assert "cookie=[REDACTED]" in redacted


def test_event_trace_is_jsonl_and_correlated_by_scenario(tmp_path):
    runner = _module()
    trace = tmp_path / "trace.jsonl"

    runner._event(trace, "startup", "scenario_start", plugin="core")
    runner._event(trace, "startup", "scenario_end", returncode=0)

    rows = [runner.json.loads(line) for line in trace.read_text().splitlines()]
    assert [row["event"] for row in rows] == ["scenario_start", "scenario_end"]
    assert {row["scenario"] for row in rows} == {"startup"}
    assert all(row["timestamp_utc"] for row in rows)


def test_runner_uses_stable_per_scenario_artifact_names():
    source = RUNNER.read_text()

    assert 'scenario_dir = args.out / name' in source
    assert 'scenario_dir / "screen.png"' in source
    assert 'scenario_dir / "stdout.log"' in source
    assert 'scenario_dir / "stderr.log"' in source
    assert 'scenario_dir / "trace.jsonl"' in source
