"""#5813 / #5975: top-level diagnose cannot authenticate providers as a diagnostic."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cifereca import cli, support_diagnostic


def test_retired_diagnose_rejected_by_parser_before_core_or_provider_io(monkeypatch, capsys):
    def forbidden(*_args, **_kwargs):
        pytest.fail("retired diagnose must never open Core")
    monkeypatch.setattr(cli.CiferecaCore, "open", forbidden)

    with pytest.raises(SystemExit) as error:
        cli.main(["diagnose"])
    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert "invalid choice" in stderr
    assert "doctor" in stderr


def test_doctor_json_is_available_without_connect_configured(monkeypatch, capsys):
    calls = []
    def forbidden():
        pytest.fail("doctor must not call connect_configured")
    service = SimpleNamespace(connect_configured=forbidden)
    core = SimpleNamespace(library=service, close=lambda: calls.append("closed"))
    monkeypatch.setattr(cli.CiferecaCore, "open", lambda **_kw: core)
    monkeypatch.setattr(support_diagnostic, "build_support_report",
                        lambda observed, *, verbose=False: {
                            "format": 1, "result": "ok", "plugins": {
                                "status": "ok", "count": 1,
                            }, "checks": [], "verbose": verbose,
                        } if observed is service else pytest.fail("wrong service"))
    assert cli.main(["doctor", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["plugins"]["count"] == 1
    assert result["format"] == 1
    assert calls == ["closed"]
