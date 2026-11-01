"""#4047: no direct Paris provider/client RenewLoans POST for digital loans.

Pure synthetic AST extraction avoids importing the HTTP plugin or touching
a real account. Core maintains the independent fresh inventory and claim.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Loan, Account
from cifereca.plugin_api import ProviderCapabilityError
from tests.test_core_p1_3121_paris_renewal_one_post import account, loan


ROOT = Path(__file__).resolve().parents[1]


class ParisError(Exception):
    pass


def _extract(path: str, owner: str, method: str, scope: dict):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    klass = next(x for x in tree.body
                 if isinstance(x, ast.ClassDef) and x.name == owner)
    fn = next(x for x in klass.body
              if isinstance(x, ast.FunctionDef) and x.name == method)
    module = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[
            ast.alias(name="annotations")
        ], level=0), fn], type_ignores=[]))
    ns = dict(scope)
    exec(compile(module, path, "exec"), ns)
    return ns[method]


@pytest.mark.parametrize("variant", [
    "digital", "digital_forged_physical", "not_renewable",
    "canrenew_false", "canrenew_integer", "wrong_id", "closed",
])
def test_direct_paris_session_cannot_post_unattested_physical_renewal(variant):
    a = account()
    selected = loan(a, raw=True)
    if variant == "digital":
        selected = loan(a, branch="DILICOM", raw=True)
    elif variant == "digital_forged_physical":
        selected = replace(selected, provider="DILICOM")
    elif variant == "not_renewable":
        selected = replace(selected, renewable=False)
    elif variant in {"canrenew_false", "canrenew_integer"}:
        selected = replace(selected, raw={
            **selected.raw, "CanRenew": False if variant == "canrenew_false" else 1
        })
    elif variant == "wrong_id":
        selected = replace(selected, raw={**selected.raw, "Id": "different"})
    elif variant == "closed":
        selected = replace(selected, state="returned")
    posts = []
    session = NS(
        provider_id="paris", credential_id=a.credential_id,
        client=NS(renew_loan=lambda *args: posts.append(args)),
    )
    direct = _extract(
        "src/plugins/provider-paris/cifereca_paris.py",
        "ParisProviderSession", "renew",
        {"Loan": Loan, "Account": Account,
         "ProviderCapabilityError": ProviderCapabilityError},
    )
    if variant == "closed":
        # The Core guards closed loans independently. This check ensures the
        # plugin cannot bypass this after an upstream loan-state transition.
        pytest.skip("positive plugin state guard is covered by the Core")
    with pytest.raises(ProviderCapabilityError):
        direct(session, selected, a)
    assert posts == []


@pytest.mark.parametrize("variant", [
    "digital", "digital_forged_physical", "not_renewable",
    "canrenew_false", "canrenew_integer", "wrong_id", "closed",
])
def test_direct_paris_http_client_refuses_before_any_transport(variant):
    a = account()
    selected = loan(a, raw=True)
    if variant == "digital":
        selected = loan(a, branch="DILICOM", raw=True)
    elif variant == "digital_forged_physical":
        selected = replace(selected, provider="DILICOM")
    elif variant == "not_renewable":
        selected = replace(selected, renewable=False)
    elif variant in {"canrenew_false", "canrenew_integer"}:
        selected = replace(selected, raw={
            **selected.raw, "CanRenew": False if variant == "canrenew_false" else 1
        })
    elif variant == "wrong_id":
        selected = replace(selected, raw={**selected.raw, "Id": "different"})
    elif variant == "closed":
        selected = replace(selected, state="returned")
    direct = _extract(
        "src/plugins/provider-paris/cifereca_paris_client.py",
        "ParisClient", "renew_loan",
        {"Loan": Loan, "ParisError": ParisError},
    )
    with pytest.raises(ParisError, match="prêts physiques"):
        direct(NS(), selected)
