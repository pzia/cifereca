"""#4048: direct provider and DRM adapter calls cannot post/execute returns.

All checks are synthetic and do not import the installed plugin runtime.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Loan, Account
from cifereca.plugin_api import ProviderCapabilityError
from tests.test_core_p1_3121_paris_renewal_one_post import account, loan


ROOT = Path(__file__).resolve().parents[1]


def _extract(path: str, function: str, *, classname: str, namespace: dict):
    source = ROOT / path
    owner = ast.parse(source.read_text(encoding="utf-8"))
    cls = next(c for c in owner.body if isinstance(c, ast.ClassDef)
               and c.name == classname)
    method = next(n for n in cls.body
                  if isinstance(n, ast.FunctionDef) and n.name == function)
    tree = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[
            ast.alias(name="annotations")
        ], level=0), method
    ], type_ignores=[]))
    exec(compile(tree, str(source), "exec"), namespace)
    return namespace[function]


def test_paris_plugin_and_client_cannot_send_return_digital_post():
    selected = loan(account(), branch="DILICOM", can_renew=True)
    posted = []
    class Reject(Exception): pass
    plugin = _extract(
        "src/plugins/provider-paris/cifereca_paris.py", "return_digital",
        classname="ParisProviderSession", namespace={
            "ProviderCapabilityError": ProviderCapabilityError,
        },
    )
    client = _extract(
        "src/plugins/provider-paris/cifereca_paris_client.py", "return_digital_loan",
        classname="ParisClient", namespace={"ParisError": Reject},
    )
    fake = NS(client=NS(return_digital_loan=lambda *args: posted.append(args)))
    with pytest.raises(ProviderCapabilityError, match="loan_return_removed"):
        plugin(fake, selected, account())
    with pytest.raises(Reject, match="loan_return_removed"):
        client(fake, selected)
    assert posted == []


def test_adept_runtime_disallows_return_from_all_direct_entries():
    class DisabledReturn(Exception):
        def __init__(self, text, *, code):
            super().__init__(text)
            self.code = code
    path = "src/plugins/runtime-kobo-adept/cifereca_kobo_adept_runtime.py"
    direct = _extract(
        path, "return_loan", classname="KoboAdeptRuntime",
        namespace={"KoboAdeptError": DisabledReturn},
    )
    # class name is looked up independently to avoid importing provider code.
    execute = _extract(
        path, "execute", classname="KoboAdeptRuntime",
        namespace={"KoboAdeptError": DisabledReturn},
    )
    calls = []
    adapter = NS(
        _request_dict=lambda request: request,
        run_json=lambda *args, **kw: calls.append((args, kw)),
    )
    with pytest.raises(DisabledReturn) as direct_error:
        direct(adapter, "synthetic-adept-loan", "synthetic-profile")
    assert direct_error.value.code == "loan_return_removed"
    with pytest.raises(DisabledReturn) as execute_error:
        execute(adapter, {"operation": "return"})
    assert execute_error.value.code == "loan_return_removed"
    assert calls == []


def test_return_capability_absent_from_installed_adept_manifest():
    manifest = json.loads((
        ROOT / "src/plugins/runtime-kobo-adept/cifereca-plugin.json"
    ).read_text(encoding="utf-8"))
    text = json.dumps(manifest)
    assert '"loan.return"' not in text
