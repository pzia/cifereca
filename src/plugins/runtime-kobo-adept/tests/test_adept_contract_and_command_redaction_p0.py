"""P0 #979: no untrusted contract version or configured argv in Core status.

Entirely synthetic offline CLI; no actual credentials, profiles or ACSM.
"""
import json
import subprocess
from types import MethodType, SimpleNamespace

import pytest

import cifereca_kobo_adept_runtime as module
from cifereca_kobo_adept_runtime import KoboAdeptError, KoboAdeptRuntime
from cifereca.plugin_api import AttentionRequired


PRIVATE = "PRIVATE_SESSION"
HOME = "/home/alice-private/.local/bin/kobo-adept"
SIGNED = "https://fulfillment.invalid/file?token=PRIVATE_SESSION"


def runtime(command=None):
    return KoboAdeptRuntime(SimpleNamespace(
        config={"command": command or ["kobo-adept"]},
        logger=None,
        report_diagnostic=lambda *a, **k: None,
    ))


def stub_contract(instance, value):
    commands = []
    instance.run_json = MethodType(
        lambda self, args, **kwargs: commands.append((list(args), kwargs)) or {
            "ok": True, "name": "kobo-adept", "contract_version": value,
        }, instance,
    )
    return commands


@pytest.mark.parametrize("value, version", [
    (8, 8), (9, 9), ("8", 8), ("009", 9), (1000, 1000),
])
def test_typed_contract_version_remains_compatible(value, version):
    adapter = runtime()
    commands = stub_contract(adapter, value)
    assert adapter.contract()["contract_version"] == value
    assert adapter.contract_version == version
    assert commands == [(["contract", "--json"], {"timeout": 20})]


@pytest.mark.parametrize("value", [
    None, False, True, 8.0, -1, 1001, "1001", "8.0", " 8 ",
    "PRIVATE_SESSION", SIGNED, "٨", "", [], {}, ["8"], 10**80,
])
def test_untrusted_contract_version_never_leaks_or_approves(value):
    adapter = runtime()
    stub_contract(adapter, value)
    with pytest.raises(KoboAdeptError, match="Version de contrat Kobo-Adept invalide") as exc:
        adapter.contract()
    assert exc.value.code == "kobo_adept_invalid_contract"
    assert PRIVATE not in str(exc.value)
    assert SIGNED not in str(exc.value)
    assert adapter.contract_version is None


@pytest.mark.parametrize("value", [0, 3, 7, "7"])
def test_old_contract_failure_does_not_echo_untrusted_version(value):
    adapter = runtime()
    stub_contract(adapter, value)
    with pytest.raises(AttentionRequired, match="contrat Kobo-Adept est trop ancien") as exc:
        adapter.contract()
    assert exc.value.code == "kobo_adept_contract_old"
    assert "v" + str(value) not in str(exc.value)
    assert adapter.contract_version is None


def test_wrong_vendor_contract_name_still_rejected():
    adapter = runtime()
    adapter.run_json = lambda *args, **kwargs: {
        "ok": True, "name": "PRIVATE_SESSION", "contract_version": 8,
    }
    with pytest.raises(AttentionRequired) as exc:
        adapter.contract()
    assert exc.value.code == "kobo_adept_invalid_contract"
    assert PRIVATE not in str(exc.value)


def test_core_configured_command_does_not_expose_argv_or_executable_path():
    command = [HOME, "--token=" + PRIVATE, SIGNED]
    adapter = runtime(command)
    info = adapter.configured_command()
    assert type(info) is list and len(info) == 1
    assert "masqués" in info[0]
    assert adapter.command == command  # Real launch remains untouched.
    for secret in (HOME, PRIVATE, SIGNED, "alice-private"):
        assert secret not in repr(info)


def test_real_subprocess_dispatch_still_uses_actual_config(monkeypatch):
    command = [HOME, "--profile=" + PRIVATE]
    adapter = runtime(command)
    calls = []
    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, b'{"ok":true}', b'')
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert adapter.run_json(["contract", "--json"])["ok"] is True
    assert calls == [[*command, "contract", "--json"]]
    assert PRIVATE not in repr(adapter.configured_command())
