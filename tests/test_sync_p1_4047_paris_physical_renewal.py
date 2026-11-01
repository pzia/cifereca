"""#4047: Paris physical renewal only at provider + CoreUI boundaries.

Synthetic sources; no GTK, HTTP or ADEPT. The Core holds the independent
authoritative fresh CanRenew/borrower/at-most-once check.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account, Loan
from cifereca.plugin_api import ProviderCapabilityError
from cifereca.gui.loan_renewal_visibility import paris_physical_renewal_visible
from tests.test_core_p1_3121_paris_renewal_one_post import account, loan


ROOT = Path(__file__).resolve().parents[1]


def _provider_renew():
    path = ROOT / "src/plugins/provider-paris/cifereca_paris.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and
                 node.name == "ParisProviderSession")
    fn = next(node for node in owner.body
              if isinstance(node, ast.FunctionDef) and node.name == "renew")
    module = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[
            ast.alias(name="annotations")
        ], level=0), fn], type_ignores=[],
    ))
    env = {"Loan": Loan, "Account": Account,
           "ProviderCapabilityError": ProviderCapabilityError}
    exec(compile(module, str(path), "exec"), env)
    return env["renew"]


@pytest.mark.parametrize("variant", [
    "digital", "foreign_provider", "other_credential",
    "other_borrower", "other_identity",
])
def test_paris_provider_renewal_rejects_unsupported_scope_before_post(variant):
    a = account()
    selected = loan(a, raw=True)
    if variant == "digital":
        selected = loan(a, branch="DILICOM", raw=True, can_renew=True)
    elif variant == "foreign_provider":
        selected = replace(selected, source_provider="bnf-pret")
    elif variant == "other_credential":
        selected = replace(selected, source_credential="card-other")
    elif variant == "other_borrower":
        selected = replace(selected, source_account_id="other")
    else:
        selected = replace(selected, identity_id="other")
    posted = []
    session = NS(
        provider_id="paris", credential_id="card",
        client=NS(renew_loan=lambda *args: posted.append(args)),
    )
    with pytest.raises(ProviderCapabilityError, match="paris_renew_physical_only"):
        _provider_renew()(session, selected, a)
    assert posted == []


def test_paris_provider_physical_scope_can_reach_authorized_core_dispatch():
    a = account()
    selected = loan(a, raw=True)
    posted = []
    session = NS(
        provider_id="paris", credential_id="card",
        client=NS(renew_loan=lambda *args: (
            posted.append(args) or {"Renewed": True}
        )),
    )
    assert _provider_renew()(session, selected, a) == {"Renewed": True}
    assert len(posted) == 1


@pytest.mark.parametrize("variant", [
    "digital", "bnf", "other", "unrenewable", "returned", "expired",
])
def test_gtk_visibility_never_offers_unsupported_renewal(variant):
    a = account()
    selected = loan(a)
    if variant == "digital":
        selected = loan(a, branch="DILICOM", can_renew=True)
    elif variant in {"bnf", "other"}:
        selected = replace(
            selected, source_provider=(
                "bnf-pret" if variant == "bnf" else "provider.future"
            ),
        )
    elif variant == "unrenewable":
        selected = replace(selected, renewable=False)
    else:
        selected = replace(selected, state=variant)
    assert paris_physical_renewal_visible(selected) is False
    assert paris_physical_renewal_visible(loan(a)) is True


def test_gui_button_guard_is_independent_of_experimental_switch():
    path = ROOT / "src/cifereca/gui/detail_panel.py"
    text = path.read_text(encoding="utf-8")
    section = text.split("def show_loan(", 1)[1].split("def _decorate(", 1)[0]
    assert "if paris_physical_renewal_visible(loan):" in section
    assert section.index("if paris_physical_renewal_visible(loan):") < section.index(
        'config.get("experimental_actions", False)'
    )
    guarded = section.split("if paris_physical_renewal_visible(loan):", 1)[1]
    guarded = guarded.split("experimental =", 1)[0]
    assert 'label="Prolonger"' in guarded
    assert "self.window.confirm_renew(loan, renew)" in guarded
