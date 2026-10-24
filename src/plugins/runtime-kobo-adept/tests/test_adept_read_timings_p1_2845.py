"""P1 #2845: startup ADEPT read timings are visible, closed and scoped.

No account, profile, network, ACSM or real subprocess is used. Instrumentation
must never infer equivalence between separate CLI invocations.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

import cifereca_kobo_adept_runtime as module
from cifereca_kobo_adept_runtime import KoboAdeptRuntime


PRIVATE = "https://identity.example.invalid/?token=PRIVATE_LICENSE"


def runtime(monkeypatch, *, response=None, stderr=b""):
    events, calls = [], []
    output = json.dumps(response or {"ok": True, "token": PRIVATE}).encode("utf-8")

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, output, stderr)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    result = KoboAdeptRuntime(SimpleNamespace(
        config={"command": ["/home/SECRET_ACCOUNT/kobo-adept", "--private", PRIVATE]},
        logger=None,
        report_diagnostic=lambda *args, **kwargs: events.append((args, kwargs)),
    ))
    return result, events, calls


@pytest.mark.parametrize("args,family", (
    (["contract", "--json"], "contract"),
    (["capabilities", "--json"], "capabilities"),
    (["profile", "list", "--json"], "profile list"),
    (["loans", "list", "--json"], "loans list"),
    (["loans", "list", "--profile", "SECRET_ACCOUNT", "--json"], "loans list"),
))
def test_each_readonly_observation_at_normal_level_without_private_evidence(
    monkeypatch, args, family,
):
    runtime_obj, events, calls = runtime(monkeypatch)
    assert runtime_obj.run_json(args)["ok"] is True
    assert len(calls) == 1 and len(events) == 1
    (message, status), kw = events[0]
    assert message == "Kobo-Adept " + family and status == "ok"
    assert kw["level"] == "normal"
    detail = kw["detail"]
    assert detail["operation"] == family
    assert type(detail["duration_ms"]) is int and detail["duration_ms"] >= 0
    assert detail["stdout_bytes"] > 0
    assert "error_code" not in detail
    for forbidden in ("PRIVATE_LICENSE", "SECRET_ACCOUNT", "identity.example",
                      "token", "profile_id", "account_id", "argv", "credential"):
        assert forbidden not in repr(events)
    assert "PRIVATE_LICENSE" not in repr(runtime_obj.last_run_diagnostic)


@pytest.mark.parametrize("args", (
    ["profile", "show", "PRIVATE_PROFILE", "--json"],
    ["loans", "expired", "--json"],
    ["download", "request", "-", "--json"],
    ["status", "--json"],
    ["contract", "--json", "EXTRA"],
))
def test_other_commands_stay_detailed_and_no_new_cli_operation(monkeypatch, args):
    runtime_obj, events, calls = runtime(monkeypatch)
    assert runtime_obj.run_json(args)["ok"] is True
    assert len(calls) == 1 and len(events) == 1
    assert events[0][1]["level"] == "detailed"
    assert PRIVATE not in repr(events)


def test_opaque_stderr_is_not_published_on_success(monkeypatch):
    runtime_obj, events, calls = runtime(
        monkeypatch, stderr=(PRIVATE + "\nPRIVATE_PROFILE").encode(),
    )
    assert runtime_obj.run_json(["profile", "list", "--json"])["ok"] is True
    assert len(calls) == 1 and events[0][1]["level"] == "normal"
    detail = events[0][1]["detail"]
    assert detail["stderr_bytes"] > 0
    assert detail["stderr_summary"] == "non vide ; contenu non structuré omis"
    assert PRIVATE not in repr(events)
    assert "PRIVATE_PROFILE" not in repr(events)


def test_activation_and_core_probe_are_distinguishable_without_profile_or_token(
    monkeypatch,
):
    events, argv_seen = [], []
    replies = {
        ("contract", "--json"): {"name": "kobo-adept", "contract_version": 8},
        ("capabilities", "--json"): {"ok": True, "capabilities": {}},
        ("profile", "list", "--json"): {"ok": True, "profiles": []},
    }

    def fake_run(command, **kwargs):
        argv_seen.append(tuple(command[-2:] if len(command) >= 2 else command))
        signature = tuple(command[2:])
        assert signature in replies
        return subprocess.CompletedProcess(
            command, 0, json.dumps(replies[signature]).encode(), b"",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.shutil, "which", lambda _bin: "/PRIVATE/bin/adept")
    context = SimpleNamespace(
        config={"command": ["kobo-adept", "--private=PRIVATE_PROFILE"]},
        logger=None,
        report_diagnostic=lambda *args, **kwargs: events.append((args, kwargs)),
    )
    plugin = module.Plugin()
    service = plugin.activate(context)
    try:
        snapshot = service.probe_core()
        assert snapshot["probe_timings_ms"].keys() == {"contract", "capabilities"}
        assert [event[1]["detail"]["caller_category"] for event in events] == [
            "activation", "activation", "core_probe", "core_probe",
        ]
        assert [event[1]["detail"]["operation"] for event in events] == [
            "contract", "capabilities", "contract", "capabilities",
        ]
        assert len(argv_seen) == 4  # Classification adds no CLI call.
        assert all(event[1]["level"] == "normal" for event in events)

        service.profiles()  # Unclassified callers must not inherit probe scope.
        assert events[-1][1]["detail"]["caller_category"] == "unattributed"
        assert events[-1][1]["detail"]["operation"] == "profile list"
        assert len(argv_seen) == 5
        assert "PRIVATE" not in repr(events)
    finally:
        plugin.deactivate()


def test_adept_caller_scope_resets_after_exception(monkeypatch):
    r, events, calls = runtime(monkeypatch)
    with pytest.raises(RuntimeError, match="test"):
        with module._adept_caller("activation"):
            assert module._ADEPT_CALLER.get() == "activation"
            raise RuntimeError("test")
    r.run_json(["profile", "list", "--json"])
    assert events[-1][1]["detail"]["caller_category"] == "unattributed"
    assert len(calls) == 1
    with pytest.raises(ValueError, match="closed ADEPT caller"):
        with module._adept_caller("PRIVATE_PROFILE"):
            pytest.fail("unknown category accepted")
