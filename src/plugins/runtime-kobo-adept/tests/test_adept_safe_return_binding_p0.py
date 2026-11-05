"""#4048: retired ADEPT loan return must not be addressable by the runtime.

The former P0 #979 command binding tests guarded an action withdrawn by
product decision. Preserve the privacy boundary as a stronger absence test:
no subprocess invocation, Core receipt, inventory mutation, or coercion of
untrusted provider identifiers. All data are synthetic.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import AttentionRequired
from cifereca_kobo_adept_runtime import KoboAdeptRuntime


ROOT = Path(__file__).resolve().parents[1]
SECRET = "ADOBE_PRIVATE_ACCESS_TOKEN_IN_EXCEPTION"


def runtime():
    r = KoboAdeptRuntime(SimpleNamespace(
        config={"command": ["kobo-adept"]}, logger=logging.getLogger("test.safe.adept"),
        report_diagnostic=lambda *args, **kwargs: None,
    ))
    calls = []

    def no_cli(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("retired return must never reach CLI")

    r.run_json = no_cli
    return r, calls


class HostileReference:
    def __str__(self):
        raise AssertionError("untrusted references must not be coerced")


@pytest.mark.parametrize("bad", [
    None, False, True, 123, {}, [], HostileReference(),
    "", "-loan", "\tloan", "loan\n" + SECRET,
    "loan\x7f" + SECRET, "loan\u200b" + SECRET, "x" * 257,
    "urn:uuid:exact-private-loan",
])
def test_return_request_unconditionally_refused_before_binding_or_cli(bad):
    r, calls = runtime()
    payload = {"operation": "return", "metadata": {
        "adept_loan_id": bad, "adept_profile_name": "original-profile",
    }}
    assert r.supports(payload) is False
    with pytest.raises(AttentionRequired) as exc:
        r.execute(payload)
    assert exc.value.code == "kobo_adept_drm_mutation_unsupported"
    assert SECRET not in str(exc.value)
    assert calls == []


@pytest.mark.parametrize("metadata", [
    None, "", [], [SECRET], "not a dict", 15,
    {"adept_loan_id": "loan-10"}, {"adept_loan_id": "loan-10",
                                    "adept_profile_name": "profile"},
])
def test_return_never_inspects_untrusted_metadata_or_calls_vendor(metadata):
    r, calls = runtime()
    with pytest.raises(AttentionRequired) as exc:
        r.execute({"operation": "return", "metadata": metadata})
    assert exc.value.code == "kobo_adept_drm_mutation_unsupported"
    assert calls == []


def test_runtime_has_no_direct_return_entry_or_cli_command():
    r, calls = runtime()
    assert not hasattr(r, "return_loan")
    assert not hasattr(r, "return_diagnostic")
    assert not hasattr(r, "_return_binding")
    source = (ROOT / "cifereca_kobo_adept_runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                 and n.name == "KoboAdeptRuntime")
    functions = {n.name for n in klass.body if isinstance(n, ast.FunctionDef)}
    assert "return_loan" not in functions
    assert "_return_binding" not in functions
    assert "['acsm', 'return'" not in source
    assert calls == []


def test_plugin_manifest_keeps_readonly_loans_and_fulfillment_without_return():
    import json

    descriptor = json.loads((ROOT / "cifereca-plugin.json").read_text(encoding="utf-8"))
    runtime = descriptor["plugins"][0]
    assert descriptor["package"]["version"] == runtime["version"] == "1.2.37"
    assert "loan.return" not in runtime["capabilities"]
    assert "loan.inventory.readonly" in runtime["capabilities"]
    assert "acquisition.fulfill" in runtime["capabilities"]
    assert "adept.profile.provision" in runtime["capabilities"]


@pytest.mark.parametrize("argv", [
    ["acsm", "return", "loan-17", "--profile", "alice", "--json"],
    ["acsm", "--json", "return", "loan-17"],
    ("acsm", "return"),
    ["acsm", "RETURN", "loan-17"],
])
def test_legacy_generic_cli_return_blocked_even_with_valid_argv(argv):
    from unittest.mock import patch

    r = KoboAdeptRuntime(SimpleNamespace(
        config={"command": ["kobo-adept"]},
        logger=logging.getLogger("test.safe.adept"),
        report_diagnostic=lambda *args, **kwargs: None,
    ))
    assert r.last_run_diagnostic == {}
    with patch("cifereca_kobo_adept_runtime.subprocess.run") as invocation:
        with pytest.raises(AttentionRequired) as error:
            r.run_json(argv, payload={"secret": SECRET})
    assert error.value.code == "kobo_adept_drm_mutation_unsupported"
    assert SECRET not in str(error.value)
    assert r.last_run_diagnostic == {}
    invocation.assert_not_called()
