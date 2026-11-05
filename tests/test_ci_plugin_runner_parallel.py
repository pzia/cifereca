"""CI plugin runner: bounded package parallelism without lost failures (#4540)."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/ci-run-plugin-tests.sh"


def _fixture(root: Path, names: tuple[str, ...]):
    for name in names:
        package = root / "src/plugins" / name
        (package / "tests").mkdir(parents=True)
        (package / "cifereca-plugin.json").write_text("{}\n", encoding="utf-8")
    bin_dir = root / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    python.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys, time\n"
        "name = pathlib.Path.cwd().name\n"
        "trace = pathlib.Path(os.environ['CI_TEST_TRACE'])\n"
        "(trace / (name + '.start')).write_text(str(time.monotonic()))\n"
        "# The slow test child stays alive until a freed slot starts c_quick.\n"
        "# A broken fixed-batch runner never starts c_quick, so hit the cap.\n"
        "if name == 'a_slow':\n"
        "    deadline = time.monotonic() + 4.0\n"
        "    while not (trace / 'c_quick.start').is_file() and time.monotonic() < deadline:\n"
        "        time.sleep(0.01)\n"
        "    time.sleep(0.05)\n"
        "else:\n"
        "    time.sleep(0.08 if name.endswith('_quick') else 0.24)\n"
        "(trace / (name + '.end')).write_text(str(time.monotonic()))\n"
        "print('executed ' + name)\n"
        "if name == 'broken': sys.exit(3)\n",
        encoding="utf-8",
    )
    python.chmod(0o755)
    trace = root / "trace"
    trace.mkdir()
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["CI_TEST_TRACE"] = str(trace)
    env.pop("CIFERECA_PLUGIN_TEST_JOBS", None)
    return env, trace


def _run(root: Path, env: dict[str, str], *targets: str):
    return subprocess.run(
        ["bash", str(RUNNER), *targets],
        cwd=root, env=env, text=True, capture_output=True, timeout=20,
    )


def test_parallel_packages_overlap_and_report_every_failure(tmp_path):
    env, trace = _fixture(tmp_path, ("good", "broken", "other"))
    env["CIFERECA_PLUGIN_TEST_JOBS"] = "3"
    result = _run(tmp_path, env)
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.count("::group::") == 3
    for name in ("good", "broken", "other"):
        assert f"executed {name}" in result.stdout
    assert "Tested 3 plugin package(s) (max parallel: 3)" in result.stdout
    starts = [float((trace / f"{name}.start").read_text()) for name in ("good", "broken", "other")]
    ends = [float((trace / f"{name}.end").read_text()) for name in ("good", "broken", "other")]
    assert max(starts) < min(ends), (starts, ends)


def test_local_default_stays_serial_and_targeted(tmp_path):
    env, trace = _fixture(tmp_path, ("first", "second"))
    result = _run(tmp_path, env, "src/plugins/first", "not-a-plugin", "src/plugins/second")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Tested 2 plugin package(s) (max parallel: 1)" in result.stdout
    assert float((trace / "first.end").read_text()) <= float((trace / "second.start").read_text())


@pytest.mark.parametrize("value", ["0", "5", "-1", "abc", "1;true"])
def test_invalid_parallelism_fails_without_starting_tests(tmp_path, value):
    env, trace = _fixture(tmp_path, ("good",))
    env["CIFERECA_PLUGIN_TEST_JOBS"] = value
    result = _run(tmp_path, env)
    assert result.returncode == 2
    assert "between 1 and 4" in result.stderr
    assert not list(trace.iterdir())

def test_workers_refill_while_slow_package_is_still_running(tmp_path):
    # Alphabetic target order: slow child starts first, followed by short ones.
    # Fixed-size batches would deadlock until the bounded rendezvous times out.
    names = ("a_slow", "b_quick", "c_quick", "d_quick")
    env, trace = _fixture(tmp_path, names)
    env["CIFERECA_PLUGIN_TEST_JOBS"] = "2"
    result = _run(tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("::group::") == len(names)
    starts = {name: float((trace / f"{name}.start").read_text()) for name in names}
    ends = {name: float((trace / f"{name}.end").read_text()) for name in names}
    assert starts["c_quick"] < ends["a_slow"], (starts, ends)
    events = sorted(
        [(starts[name], 1) for name in names]
        + [(ends[name], -1) for name in names]
    )
    active = peak = 0
    for _, delta in events:
        active += delta
        peak = max(peak, active)
        assert active >= 0
    assert active == 0
    assert peak == 2
