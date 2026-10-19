"""#2290: Kobo-ADEPT 0.13.6 contract docs are not mutation receipts.

Only synthetic CLI subprocess responses; no real binary, profiles, ACSM or
account data. Private vendor fields never reach public errors/diagnostics.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

import cifereca_kobo_adept_runtime as module
from cifereca_kobo_adept_runtime import KoboAdeptError, KoboAdeptRuntime, Plugin
from cifereca.plugin_api import AttentionRequired

CONTRACT = {
    "name": "kobo-adept", "version": "0.13.6", "contract_version": 8,
    "capabilities": ["strict_json_stdout", "profile_provisioning"],
}
SECRET = "PRIVATE_PROFILE=https://vendor.invalid/?token=PRIVATE_TOKEN"


def make_runtime(monkeypatch, replies):
    events = []
    calls = []

    def fake_run(argv, **kwargs):
        operation = argv[1:]
        calls.append(operation)
        reply, exit_code = replies[tuple(operation)]
        return subprocess.CompletedProcess(
            argv, exit_code, json.dumps(reply).encode("utf-8"),
            SECRET.encode("utf-8"),
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    context = SimpleNamespace(
        config={"command": ["kobo-adept"]}, logger=None,
        report_diagnostic=lambda *a, **kw: events.append((a, kw)),
    )
    return KoboAdeptRuntime(context), context, calls, events


def test_observed_v8_descriptive_contract_unblocks_plugin_activation(monkeypatch):
    replies = {
        ("contract", "--json"): (CONTRACT, 0),
        ("capabilities", "--json"): (
            {"ok": True, "software_version": "0.13.6",
             "contract_version": 8, "capabilities": {}}, 0,
        ),
    }
    runtime, context, calls, events = make_runtime(monkeypatch, replies)
    plugin = Plugin()
    try:
        service = plugin.activate(context)
        assert service.contract_version == 8
        assert service._capabilities_cache["ok"] is True
        assert calls == [["contract", "--json"], ["capabilities", "--json"]]
        assert [report[0][1] for report in events] == ["ok", "ok"]
        assert SECRET not in repr(events)
    finally:
        plugin.deactivate()


@pytest.mark.parametrize("payload,exit_status", [
    ({"name": "wrong", "contract_version": 8}, 0),
    ({"name": "kobo-adept"}, 0),
    ({"name": "kobo-adept", "contract_version": True}, 0),
    ({"name": "kobo-adept", "contract_version": "PRIVATE_TOKEN"}, 0),
    ({**CONTRACT, "ok": None}, 0),
    ({**CONTRACT, "ok": "true"}, 0),
    ({**CONTRACT, "error": {"message": SECRET}}, 0),
    (CONTRACT, 10),
])
def test_invalid_or_failed_contract_never_activates(monkeypatch, payload, exit_status):
    runtime, _, calls, events = make_runtime(monkeypatch, {
        ("contract", "--json"): (payload, exit_status),
    })
    with pytest.raises(KoboAdeptError) as caught:
        runtime.contract()
    assert caught.value.code in {"kobo_adept_invalid_response", "kobo_adept_failed"}
    assert runtime.contract_version is None
    assert calls == [["contract", "--json"]]
    assert SECRET not in str(caught.value) + repr(events) + caught.value.technical_detail


def test_read_only_contract_version_too_old_still_fails(monkeypatch):
    runtime, _, calls, _ = make_runtime(monkeypatch, {
        ("contract", "--json"): ({**CONTRACT, "contract_version": 7}, 0),
    })
    with pytest.raises(AttentionRequired) as caught:
        runtime.contract()
    assert caught.value.code == "kobo_adept_contract_old"
    assert runtime.contract_version is None
    assert len(calls) == 1


@pytest.mark.parametrize("operation,payload", [
    (["capabilities", "--json"], None),
    (["profile", "add", "alice", "--json"], None),
    (["profile", "remove", "alice", "--json"], None),
    (["acsm", "return", "loan", "--json"], None),
    (["download", "request", "-", "--json"], {"source": {"type": "acsm"}}),
])
def test_descriptive_contract_shape_cannot_acknowledge_other_commands(
    monkeypatch, operation, payload,
):
    runtime, _, calls, _ = make_runtime(monkeypatch, {
        tuple(operation): (CONTRACT, 0),
    })
    with pytest.raises(KoboAdeptError) as caught:
        runtime.run_json(operation, payload=payload)
    assert caught.value.code == "kobo_adept_invalid_response"
    assert len(calls) == 1
