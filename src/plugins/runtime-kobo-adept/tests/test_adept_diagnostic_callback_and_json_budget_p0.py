"""P0 #979: external CLI output and diagnostic callback never leak secrets.

All process results are synthetic and offline. No ADEPT profile, account,
ACSM license, network or actual kobo-adept-next invocation is needed.
"""
from __future__ import annotations

import json
import logging
import subprocess
from types import SimpleNamespace

import pytest

import cifereca_kobo_adept_runtime as module
from cifereca_kobo_adept_runtime import KoboAdeptError, KoboAdeptRuntime

PRIVATE = (
    "account@example.invalid",
    "https://server.invalid/fulfill?token=PRIVATE_SESSION",
    "<fulfillmentToken>PRIVATE_LICENSE</fulfillmentToken>",
    "PRIVATE_SESSION",
    "PRIVATE_LICENSE",
)
UNTRUSTED = " ".join(PRIVATE)


def runtime(*, report=None, logger=None):
    return KoboAdeptRuntime(SimpleNamespace(
        config={"command": ["kobo-adept"]},
        report_diagnostic=report, logger=logger,
    ))


def fake_process(monkeypatch, stdout, *, code=0, stderr=b"", record=None):
    if type(stdout) is str:
        stdout = stdout.encode("utf-8")
    def fake_run(*args, **kwargs):
        if record is not None:
            record.append((args, kwargs))
        return subprocess.CompletedProcess(["synthetic"], code, stdout, stderr)
    monkeypatch.setattr(module.subprocess, "run", fake_run)


def test_reporter_error_never_logs_private_exception_on_success(monkeypatch, caplog):
    logger = logging.getLogger("adept.callback.non_private")
    logger.setLevel(logging.DEBUG)
    def malicious_report(*args, **kwargs):
        raise RuntimeError("renderer failed: " + UNTRUSTED)
    fake_process(monkeypatch, '{"ok":true,"contract_version":8}')
    instance = runtime(report=malicious_report, logger=logger)
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        response = instance.run_json(["contract", "--json"])
    assert response["contract_version"] == 8
    assert instance.last_run_diagnostic["stdout_mode"] == "json"
    assert "diagnostic reporter unavailable" in caplog.text
    for secret in PRIVATE:
        assert secret not in caplog.text
    # No exception_info or traceback entered any emitted LogRecord.
    assert all(record.exc_info is None for record in caplog.records)


def test_reporter_error_never_logs_private_exception_on_vendor_failure(
        monkeypatch, caplog):
    logger = logging.getLogger("adept.callback.failure")
    logger.setLevel(logging.DEBUG)
    def malicious_report(*args, **kwargs):
        raise ValueError("error callback private: " + UNTRUSTED)
    fake_process(
        monkeypatch,
        json.dumps({"ok": False, "error": {
            "code": "adept_error", "message": UNTRUSTED,
        }}),
        code=10,
        stderr=UNTRUSTED.encode("utf-8"),
    )
    instance = runtime(report=malicious_report, logger=logger)
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        with pytest.raises(KoboAdeptError) as raised:
            instance.run_json(
                ["download", "request", "-", "--json"],
                payload={"source": {"type": "acsm"}},
            )
    assert raised.value.code == "adept_error"
    assert "diagnostic reporter unavailable" in caplog.text
    published = caplog.text + str(raised.value) + repr(raised.value.details)
    for private in PRIVATE:
        assert private not in published
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("command", [
    ["contract", "--json"],
    ["capabilities", "--json"],
    ["profile", "list", "--json"],
    ["profile", "show", "fake", "--json"],
    ["acsm", "return", "fake", "--json"],
    ["download", "request", "-", "--json"],
])
def test_unbounded_default_is_rejected_for_every_cli_operation(monkeypatch, command):
    # A 4-MiB JSON string is legal UTF-8, but may not be parsed or returned
    # by any command, including contract() and profiles().
    output = b'{"ok": true, "secret": "' + b"x" * (4*1024*1024) + b'"}'
    fake_process(monkeypatch, output, stderr=UNTRUSTED.encode("utf-8"))
    instance = runtime(report=lambda *args, **kwargs: None)
    with pytest.raises(KoboAdeptError) as raised:
        instance.run_json(command)
    error = raised.value
    assert error.code == "kobo_adept_output_too_large"
    assert instance.last_run_diagnostic["stdout_mode"] == "oversized"
    assert instance.last_run_diagnostic["stdout_bytes"] == len(output)
    assert error.details["stdout_summary"] == "sortie trop volumineuse"
    public = str(error) + error.technical_detail + repr(instance.last_run_diagnostic)
    for secret in PRIVATE:
        assert secret not in public


@pytest.mark.parametrize("budget", [0, -1, True, False, 1.0, "1024", [], {},
                                     4*1024*1024+1, 10**100])
def test_invalid_json_budgets_fail_before_starting_process(monkeypatch, budget):
    calls = []
    fake_process(monkeypatch, '{"ok":true}', record=calls)
    instance = runtime()
    with pytest.raises(ValueError, match="invalid Kobo-Adept JSON response limit"):
        instance.run_json(["contract", "--json"], max_output_bytes=budget)
    assert not calls


def test_explicit_smaller_json_budget_is_enforced_without_parser_side_effects(monkeypatch):
    calls = []
    fake_process(monkeypatch, '{"ok":true,"name":"kobo-adept"}', record=calls)
    instance = runtime()
    with pytest.raises(KoboAdeptError) as raised:
        instance.run_json(["contract", "--json"], max_output_bytes=4)
    assert raised.value.code == "kobo_adept_output_too_large"
    assert len(calls) == 1


def test_explicit_four_megabyte_budget_remains_supported_for_loan_inventory(monkeypatch):
    fake_process(monkeypatch, '{"ok":true,"loans":[]}')
    instance = runtime()
    response = instance.run_json(
        ["loans", "list", "--json"], max_output_bytes=4*1024*1024,
    )
    assert response["ok"] is True and response["loans"] == []


def test_output_exactly_at_budget_is_accepted(monkeypatch):
    encoded = b'{"ok":true}'
    fake_process(monkeypatch, encoded)
    instance = runtime()
    assert instance.run_json(["contract", "--json"], max_output_bytes=len(encoded))["ok"] is True


def test_error_callback_absent_has_no_logging_side_effect(monkeypatch, caplog):
    fake_process(monkeypatch, '{"ok":true}')
    instance = runtime(report=None)
    with caplog.at_level(logging.DEBUG):
        assert instance.run_json(["contract", "--json"])["ok"] is True
    assert "diagnostic reporter unavailable" not in caplog.text
