"""P1 #3885: startup and doctor share one non-sensitive build identity."""
import json
import sys

from cifereca import app
from cifereca import build_identity as identity_module
from cifereca import support_diagnostic


def test_known_revision_has_closed_structured_identity(monkeypatch):
    monkeypatch.setattr(identity_module, "startup_git_commit", lambda: "a" * 40)
    monkeypatch.setattr(identity_module, "_distribution_mode", lambda: "github")
    result = identity_module.build_identity()
    assert result == {
        "schema_version": 1, "build_version": identity_module.__version__,
        "commit_sha": "a" * 40, "build_timestamp": None,
        "distribution_mode": "github", "availability": "known",
    }


def test_missing_revision_cannot_claim_known_build(monkeypatch):
    monkeypatch.setattr(identity_module, "startup_git_commit", lambda: None)
    monkeypatch.setattr(identity_module, "_distribution_mode", lambda: "unknown")
    result = identity_module.build_identity()
    assert result["commit_sha"] is None
    assert result["availability"] == "unavailable"


def test_startup_emits_build_identity_diagnostic_before_gtk(monkeypatch, capsys):
    identity = {
        "schema_version": 1, "build_version": "test",
        "commit_sha": "b" * 40, "build_timestamp": None,
        "distribution_mode": "checkout", "availability": "known",
    }
    logs = []
    class Logger:
        def info(self, pattern, *args):
            logs.append((pattern, args))
    monkeypatch.setattr(app, "configure_logging", lambda **kw: Logger())
    monkeypatch.setattr(app, "build_identity", lambda: dict(identity))
    monkeypatch.setitem(sys.modules, "gi", None)
    monkeypatch.setattr(sys, "argv", ["cifereca", "--no-plugins"])
    assert app.main() == 3
    events = [json.loads(args[0]) for pattern, args in logs if pattern == "DIAG_JSON %s"]
    assert len(events) == 1
    assert events[0] == {
        "schema_version": 1, "category": "CORE",
        "operation": "build_identity", "status": "ok",
        "message": "build_identity", "detail": identity,
    }
    assert "b" * 40 not in capsys.readouterr().err


def test_doctor_json_reuses_same_identity_and_commit(monkeypatch):
    from tests.test_support_diagnostic import Service
    payload = {
        "schema_version": 1, "build_version": "test",
        "commit_sha": "c" * 40, "build_timestamp": None,
        "distribution_mode": "wheel", "availability": "known",
    }
    monkeypatch.setattr(support_diagnostic, "build_identity", lambda: dict(payload))
    monkeypatch.setattr(support_diagnostic, "_release_metadata", lambda: {})
    monkeypatch.setattr(support_diagnostic, "_runtime_state", lambda: {"status": "ok"})
    monkeypatch.setattr(support_diagnostic, "_database_state", lambda: {"status": "ok"})
    monkeypatch.setattr(support_diagnostic, "_config_state", lambda: {"status": "ok"})
    report = support_diagnostic.build_support_report(Service(), include_checks=False)
    assert report["build_identity"] == payload
    assert report["core"]["commit"] == payload["commit_sha"]
    assert json.loads(json.dumps(report))["build_identity"]["availability"] == "known"
