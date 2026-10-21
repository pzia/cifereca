"""P0 #1120: observed ok_missing is a read-only loan-envelope mismatch.

All CLI output and profiles are synthetic; only subprocess.run is stubbed.
A valid, complete minimum loans[] document can be consumed without `ok`
by one exact loans list command; no mutation/expired command is licensed.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace as NS

import pytest

import cifereca_kobo_adept_runtime as runtime_module
from cifereca_kobo_adept_runtime import KoboAdeptRuntime, KoboAdeptError

PROFILE = "synthetic-reader"
PRIVATE = "https://private.invalid/adept?token=DO_NOT_PUBLISH"
PRIVATE_XML = "<fulfillmentToken>PRIVATE_LICENSE</fulfillmentToken>"
GOOD_ROW = {
    "id": "synthetic-adobe-loan",
    "expired": False, "returned": False, "token_present": True,
    "title": "Synthetic title", "opaque": PRIVATE_XML,
}
LIST_ARGS = ["loans", "list", "--profile", PROFILE, "--json"]


def runtime(monkeypatch, reply, *, exit_code=0):
    published = []
    calls = []
    cli = KoboAdeptRuntime(NS(
        config={"command": ["kobo-adept"]}, logger=None,
        report_diagnostic=lambda *a, **kwargs: published.append((a, kwargs)),
    ))
    cli.contract_version = 8  # Avoid a second subprocess for contract().
    raw = json.dumps(reply).encode("utf-8")

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, exit_code, raw, b"")

    monkeypatch.setattr(runtime_module.subprocess, "run", fake_run)
    return cli, published, calls, raw


@pytest.mark.parametrize("rows", [
    [], [GOOD_ROW], [
        GOOD_ROW, {**GOOD_ROW, "id": "second-loan", "returned": True,
                   "token_present": False},
    ],
])
def test_valid_descriptive_loans_list_only_and_no_raw_diagnostics(
    monkeypatch, rows,
):
    payload = {"profile": PROFILE, "loans": rows, "unknown": PRIVATE}
    cli, published, calls, raw = runtime(monkeypatch, payload)
    # True CLI read-only path, including normal inventory sanitization.
    result = cli.loans(PROFILE)
    assert len(result["loans"]) == len(rows)
    for loan in result["loans"]:
        assert "opaque" not in loan
        assert set(loan) <= {
            "id", "expired", "returned", "token_present",
            "title", "validity", "local_book",
        }
    assert cli.last_run_diagnostic["loan_count"] == len(rows)
    assert cli.last_run_diagnostic["inventory_valid"] is True
    assert cli.last_run_diagnostic["returncode"] == 0
    assert "validation_reason" not in cli.last_run_diagnostic
    assert "error_code" not in cli.last_run_diagnostic
    assert len(calls) == 1 and calls[0][0] == ["kobo-adept", *LIST_ARGS]
    assert calls[0][1]["input"] is None
    assert calls[0][1]["capture_output"] is True
    assert cli.last_run_diagnostic["stdout_bytes"] == len(raw)
    assert len(published) == 1 and published[0][0][1] == "ok"
    public = repr(published) + repr(cli.last_run_diagnostic)
    assert PRIVATE not in public and PRIVATE_XML not in public
    assert PROFILE not in public and "Synthetic title" not in public


@pytest.mark.parametrize("bad", [
    {"loans": [{"id": "missing-bool"}]},
    {"loans": [{**GOOD_ROW, "token_present": 1}]},
    {"loans": [{**GOOD_ROW, "returned": "false"}]},
    {"loans": [{**GOOD_ROW, "id": "-invalid"}]},
    {"loans": [{**GOOD_ROW, "id": "name\nprivate"}]},
    {"loans": [GOOD_ROW, GOOD_ROW]},
    {"loans": "private-bad-type"},
    {"loans": None},
    {"profile": "different-borrower", "loans": [GOOD_ROW]},
    {"loans": [GOOD_ROW], "error": None},
    {"loans": [GOOD_ROW], "error": {"token": PRIVATE}},
    {"loans": [GOOD_ROW], "success": False},
    {"loans": [GOOD_ROW], "status": "failed"},
    {"loans": [GOOD_ROW], "errors": [PRIVATE]},
    {"private": PRIVATE},
])
def test_ambiguous_or_incomplete_loan_document_keeps_fail_closed(
    monkeypatch, bad,
):
    cli, published, calls, raw = runtime(monkeypatch, bad)
    with pytest.raises(KoboAdeptError) as caught:
        cli.run_json(LIST_ARGS)
    assert caught.value.code == "kobo_adept_invalid_response"
    assert caught.value.retryable is False
    assert cli.last_run_diagnostic["validation_reason"] == "ok_missing"
    assert cli.last_run_diagnostic["stdout_bytes"] == len(raw)
    assert len(calls) == len(published) == 1
    assert published[0][0][1] == "error"
    public = str(caught.value) + caught.value.technical_detail + repr(published)
    assert PRIVATE not in public and PRIVATE_XML not in public
    assert PROFILE not in public and "Synthetic title" not in public


@pytest.mark.parametrize("args,payload,exit_code", [
    (["loans", "expired", "--profile", PROFILE, "--json"], None, 0),
    (["profile", "add", PROFILE, "--json"], None, 0),
    (["download", "request", "-", "--json"], {"source": {"type": "acsm"}}, 0),
    (["acsm", "return", "synthetic-id", "--profile", PROFILE, "--json"], None, 0),
    (["loans", "list", "--json"], None, 0),
    (["loans", "list", "--profile", "wrong", "--json", "more"], None, 0),
    (LIST_ARGS, None, 1),
    (LIST_ARGS, {"unexpected": True}, 0),
])
def test_no_other_command_or_nonzero_exit_can_infer_success(
    monkeypatch, args, payload, exit_code,
):
    cli, published, calls, raw = runtime(
        monkeypatch, {"loans": [GOOD_ROW], "profile": PROFILE}, exit_code=exit_code,
    )
    with pytest.raises(KoboAdeptError) as caught:
        cli.run_json(args, payload=payload)
    assert caught.value.code in {"kobo_adept_failed", "kobo_adept_invalid_response"}
    assert caught.value.retryable is False
    assert len(calls) == len(published) == 1
    assert published[0][0][1] == "error"


def test_explicit_boolean_receipts_preserve_existing_command_semantics(
    monkeypatch,
):
    cli, published, calls, raw = runtime(
        monkeypatch, {"ok": True, "loans": [GOOD_ROW]},
    )
    assert cli.run_json(LIST_ARGS)["ok"] is True
    assert "validation_reason" not in cli.last_run_diagnostic
    assert len(calls) == 1 and len(published) == 1
