"""End-to-end CLI stdout secrecy proof without live network (#1144)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _script():
    spec = importlib.util.spec_from_file_location(
        "live_youth_1144", ROOT / "scripts/qualify-youth-source-live.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_success_stdout_is_minimal_even_if_plugin_produces_secrets(monkeypatch, capsys):
    script = _script()
    secret = "https://private.example/download.acsm?token=VERY_PRIVATE"
    monkeypatch.setattr(script.subprocess, "check_output", lambda *a, **k: "e" * 40)
    def qualification(_args, report):
        report.update(
            result="PASS", requests_count=1, accepted=1, rejected=0,
            https_requests=[secret], public_query="user secret search",
            qualified_notices=[{"title": "PRIVATE TITLE", "notice_url": secret}],
            ingest_errors=[f"Cookie: {secret}"],
            plugin_diagnostic={"source": "milan", "anchor_count": 2,
                               "headers": {"Authorization": secret}},
        )
    monkeypatch.setattr(script, "qualify", qualification)
    assert script.main(["--source", "milan", "--confirm-live"]) == 0
    printed = capsys.readouterr().out
    assert secret not in printed and "PRIVATE TITLE" not in printed
    assert "user secret search" not in printed and "Cookie" not in printed
    safe = json.loads(printed)
    assert safe["result"] == "PASS"
    assert safe["qualified_notices_count"] == 1
    assert safe["requests_count"] == 1
    assert safe["plugin_diagnostic"] == {"source": "milan", "anchor_count": 2}


def test_exception_stdout_never_echoes_transport_secret(monkeypatch, capsys):
    script = _script()
    secret = "https://private.example/mybook.epub?signature=SECRET"
    monkeypatch.setattr(script.subprocess, "check_output", lambda *a, **k: "e" * 40)
    def fails(_args, _report):
        raise RuntimeError(f"Authorization: Bearer {secret}")
    monkeypatch.setattr(script, "qualify", fails)
    assert script.main(["--source", "ecole-loisirs", "--confirm-live"]) == 1
    stdout = capsys.readouterr().out
    assert secret not in stdout and "Authorization" not in stdout and "Bearer" not in stdout
    safe = json.loads(stdout)
    assert safe["result"] == "BLOCKED"
    assert safe["error_code"] == "QUALIFICATION_RUNTIME"
    assert safe["reason_code"] == "QUALIFICATION_BLOCKED"


def test_invalid_input_stdout_is_structural_only(capsys):
    script = _script()
    assert script.main(["--source", "milan", "--age", "18", "--confirm-live"]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "result": "INVALID_INPUT", "reason_code": "INVALID_INPUT"
    }
