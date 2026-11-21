"""#2845: reproduce eleven invocation shapes, not private terrain history."""
import json
import subprocess
from collections import Counter, deque
from types import SimpleNamespace

import pytest

import cifereca_kobo_adept_runtime as module
from cifereca.plugin_api import AttentionRequired


def rig(monkeypatch, replies):
    events, calls = [], []
    pending = deque(replies)

    def fake_run(argv, **kwargs):
        assert kwargs["input"] is None
        command = tuple(argv[1:])
        expected, status, payload = pending.popleft()
        assert command == expected
        calls.append(command)
        return subprocess.CompletedProcess(argv, status, json.dumps(payload).encode(), b"")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.shutil, "which", lambda _: "/synthetic/kobo-adept")
    ctx = SimpleNamespace(config={"command": ["synthetic"]}, logger=None,
                          report_diagnostic=lambda *a, **k: events.append(k["detail"]))
    return ctx, events, calls, pending


CONTRACT = ("contract", "--json")
CAPABILITIES = ("capabilities", "--json")
PROFILES = ("profile", "list", "--json")


def loan_command(profile):
    return ("loans", "list", "--profile", profile, "--json")


def contract():
    return {"name": "kobo-adept", "contract_version": 8}


def caps(names=()):
    return {"ok": True, "capabilities": {
        "download": {"acsm": {"available": True}},
        "adept": {"fetch_capable_profiles": list(names)},
    }}


def loans(*ids):
    return {"ok": True, "loans": [
        {"id": name, "expired": False, "returned": False, "token_present": False}
        for name in ids
    ]}


def test_eleven_reads_same_scope_is_not_same_freshness(monkeypatch):
    profile = {"name": "PRIVATE_A", "profile_id": "PRIVATE_ID"}
    ctx, events, calls, pending = rig(monkeypatch, [
        (CONTRACT, 0, contract()), (CAPABILITIES, 0, caps()),
        (CONTRACT, 0, contract()), (CAPABILITIES, 0, caps()),
        (PROFILES, 0, {"ok": True, "profiles": []}),
        (PROFILES, 0, {"ok": True, "profiles": [profile]}),
        (PROFILES, 10, {"ok": False, "error": {"code": "adept_profile_error"}}),
        (PROFILES, 0, {"ok": True, "profiles": []}),
        (loan_command("PRIVATE_A"), 0, loans("PRIVATE_LOAN")),
        (loan_command("PRIVATE_B"), 0, loans()),
        (loan_command("PRIVATE_A"), 0, loans()),
    ])
    plugin = module.Plugin()
    runtime = plugin.activate(ctx)
    try:
        runtime.probe_core()
        assert runtime.profiles()["profiles"] == []
        assert runtime.profiles()["profiles"] == [profile]
        with pytest.raises(module.KoboAdeptError) as caught:
            runtime.profiles()
        assert caught.value.code == "adept_profile_error"
        assert runtime.profiles()["profiles"] == []
        assert len(runtime.loans("PRIVATE_A")["loans"]) == 1
        assert runtime.loans("PRIVATE_B")["loans"] == []
        assert runtime.loans("PRIVATE_A")["loans"] == []
        assert not pending
        assert len(calls) == len(events) == 11
        assert Counter(e["operation"] for e in events) == {
            "contract": 2, "capabilities": 2, "profile list": 4, "loans list": 3,
        }
        assert [e["caller_category"] for e in events[:4]] == [
            "activation", "activation", "core_probe", "core_probe",
        ]
        assert all(e["caller_category"] == "unattributed" for e in events[4:])
        assert len({e["read_scope_ref"] for e in events[4:8]}) == 1
        a, b, again = [e["read_scope_ref"] for e in events[8:]]
        assert a == again and a != b
        assert events[6]["error_code"] == "adept_profile_error"
        assert "error_code" not in events[7]
        assert all(type(e["duration_ms"]) is int and e["duration_ms"] >= 0 for e in events)
        assert "PRIVATE" not in repr(events)
    finally:
        plugin.deactivate()


def test_two_fresh_preflights_same_scope_observe_revoked_readiness(monkeypatch):
    ctx, events, calls, pending = rig(monkeypatch, [
        (CAPABILITIES, 0, caps(["PRIVATE_A"])),
        (CAPABILITIES, 0, caps([])),
    ])
    runtime = module.KoboAdeptRuntime(ctx)
    runtime.contract_version = 8
    assert runtime.preflight_acsm_profile("PRIVATE_A") is True
    with pytest.raises(AttentionRequired) as caught:
        runtime.preflight_acsm_profile("PRIVATE_A")
    assert caught.value.code == "adept_profile_required"
    assert not pending
    assert calls == [CAPABILITIES, CAPABILITIES]
    assert events[0]["read_scope_ref"] == events[1]["read_scope_ref"]
    assert "PRIVATE" not in repr(events)
