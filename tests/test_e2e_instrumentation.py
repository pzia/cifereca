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


def test_e2e_logs_are_redacted_before_first_artifact_write(tmp_path, monkeypatch):
    """#1143: both green and red probe paths must never persist raw child output."""
    import sys
    from types import SimpleNamespace

    runner = _module()
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/fake/xvfb-run")
    monkeypatch.setenv("E2E_TEST_TOKEN", "PRIVATE_ENV_TOKEN")
    for status in (0, 7):
        calls = []

        def fake_run(*args, **kwargs):
            assert kwargs.get("capture_output") is True
            assert kwargs.get("text") is True
            assert "stdout" not in kwargs and "stderr" not in kwargs
            calls.append(1)
            return SimpleNamespace(
                returncode=status,
                stdout=(
                    "token=INLINE_SECRET bearer PRIVATE_BEARER "
                    "https://example.invalid/file?signature=PRIVATE_URL "
                    "/home/private_user/data user@example.test PRIVATE_ENV_TOKEN"
                ),
                stderr="Authorization: PRIVATE_AUTH cookie=PRIVATE_COOKIE",
            )

        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        dest = tmp_path / f"case-{status}"
        monkeypatch.setattr(sys, "argv", ["e2e/run.py", "--scenario", "startup", "--out", str(dest)])
        assert runner.main() == status
        assert len(calls) == 1
        for logfile in ("stdout.log", "stderr.log"):
            public = (dest / "startup" / logfile).read_text(encoding="utf-8")
            for private in (
                "INLINE_SECRET", "PRIVATE_BEARER", "PRIVATE_URL",
                "/home/private_user", "user@example.test",
                "PRIVATE_ENV_TOKEN", "PRIVATE_AUTH", "PRIVATE_COOKIE",
            ):
                assert private not in public
            assert len(public) < runner.MAX_E2E_LOG_CHARS


def test_oversized_e2e_subprocess_log_is_bounded_and_sanitized(tmp_path):
    runner = _module()
    output = tmp_path / "stdout.log"
    raw = "a" * (runner.MAX_E2E_LOG_CHARS + 200) + "SECRET=PRIVATE"
    runner._write_redacted_log(output, raw, {"API_TOKEN": "PRIVATE"})
    text = output.read_text(encoding="utf-8")
    assert text.endswith("[TRUNCATED]\n")
    assert "PRIVATE" not in text
    assert len(text) <= runner.MAX_E2E_LOG_CHARS + 15
