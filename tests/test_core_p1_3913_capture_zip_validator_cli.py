"""P1 #3913/#4093: self-service --capture ZIP verification is Core-free.

Every file and token in this test is synthetic. No GUI, provider or network.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import zipfile

from cifereca.core.capture_session_export import export_capture_session
from tests.test_core_p1_4093_capture_session_export import (
    _entry, _http, _journal,
)

ROOT = Path(__file__).resolve().parents[1]
MODULE = "cifereca.core.capture_session_export"


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", MODULE, *arguments],
        cwd=ROOT, env=env, text=True, capture_output=True,
        check=False, timeout=15,
    )


def _archive(tmp_path: Path) -> Path:
    entry = _entry(
        "a" * 32, b"<html>PRIVATE_SYNTHETIC_TOKEN</html>",
        [["content-type", "text/html; charset=utf-8"]],
    )
    return export_capture_session(
        tmp_path, journal=_journal(b"PRIVATE_SYNTHETIC_SESSION"),
        http_snapshot=_http([entry]), app_exit_code=0,
    )


def test_standalone_validator_json_only_reports_closed_integrity_data(tmp_path):
    archive = _archive(tmp_path)
    before = (archive.read_bytes(), archive.stat().st_mtime_ns,
              stat.S_IMODE(archive.stat().st_mode))
    completed = _run("--validate", str(archive), "--json")
    assert completed.returncode == 0
    assert completed.stderr == ""
    verdict = json.loads(completed.stdout)
    assert set(verdict) == {"coverage", "format", "members", "status"}
    assert verdict["format"] == "cifereca-capture-session/1"
    assert verdict["status"] == "valid"
    assert verdict["members"] >= 6
    assert verdict["coverage"] == "instrumented_only"
    assert "PRIVATE" not in completed.stdout
    assert str(tmp_path) not in completed.stdout
    assert before == (archive.read_bytes(), archive.stat().st_mtime_ns,
                      stat.S_IMODE(archive.stat().st_mode))


def test_validator_human_output_is_closed_and_read_only(tmp_path):
    archive = _archive(tmp_path)
    completed = _run("--validate", str(archive))
    assert completed.returncode == 0
    assert completed.stderr == ""
    assert completed.stdout.startswith("capture_archive_valid coverage=")
    assert "members=" in completed.stdout
    assert str(archive) not in completed.stdout
    assert "PRIVATE" not in completed.stdout


def test_validator_rejects_corruption_symlink_and_nonexistent_file(tmp_path):
    archive = _archive(tmp_path)
    with zipfile.ZipFile(archive) as source:
        members = {n: source.read(n) for n in source.namelist()}
    members["logs/journal.jsonl"] = b"PRIVATE_SYNTHETIC_TAMPER"
    corrupted = tmp_path / "PRIVATE_CORRUPTED.zip"
    with zipfile.ZipFile(corrupted, "w") as target:
        for name, value in members.items():
            target.writestr(name, value)
    symlink = tmp_path / "PRIVATE_SYMLINK.zip"
    symlink.symlink_to(archive)
    missing = tmp_path / "PRIVATE_MISSING.zip"
    for invalid in (corrupted, symlink, missing):
        before = invalid.read_bytes() if invalid.exists() and not invalid.is_symlink() else None
        completed = _run("--validate", str(invalid), "--json")
        assert completed.returncode == 2
        assert completed.stderr == ""
        assert json.loads(completed.stdout) == {
            "format": "cifereca-capture-session/1",
            "status": "invalid", "code": "capture_archive_invalid",
        }
        assert "PRIVATE" not in completed.stdout
        assert "PRIVATE" not in completed.stderr
        if before is not None:
            assert invalid.read_bytes() == before

    human = _run("--validate", str(corrupted))
    assert human.returncode == 2
    assert human.stdout == "capture_archive_invalid\n"
    assert human.stderr == ""


def test_validator_rejects_unrelated_zip_without_printing_its_contents(tmp_path):
    unrelated = tmp_path / "PRIVATE_UNRELATED.zip"
    with zipfile.ZipFile(unrelated, "w") as target:
        target.writestr("PRIVATE_CREDENTIALS.txt", "PRIVATE_SYNTHETIC_SECRET")
    completed = _run("--validate", str(unrelated), "--json")
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["status"] == "invalid"
    assert "PRIVATE" not in completed.stdout + completed.stderr


def test_malformed_arguments_never_echo_private_unexpected_text(tmp_path):
    private_arg = str(tmp_path / "PRIVATE_COOKIE_IN_BAD_OPTION.zip")
    for arguments in (
        ("--validate", private_arg, "--unknown=PRIVATE_BEARER"),
        ("--json", "--unexpected", private_arg),
        ("--json",),
    ):
        completed = _run(*arguments)
        assert completed.returncode == 2
        assert completed.stdout == ""
        assert completed.stderr == "capture_validator_invalid_arguments\n"
        assert "PRIVATE" not in completed.stderr
        assert private_arg not in completed.stderr
