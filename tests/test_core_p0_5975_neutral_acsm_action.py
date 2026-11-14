"""#5975 / #5056: remove vendor dispatch without making ACSM consent implicit.

These tests have no GTK runtime, network, SQLite or provider imports.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.core.explicit_loan_acsm_visibility import (
    explicit_loan_acsm_action_available,
)
from cifereca.models import Account, Loan
from cifereca.providers.base import (
    ProviderCapabilities, ProviderDescriptor, ProviderSession,
)


_DESCRIPTOR = ProviderDescriptor(
    id="future-library", display_name="Synthetic", description="Synthetic",
    state="ready", capabilities=ProviderCapabilities(loans=True),
)


class StaticLoanTokenSession(ProviderSession):
    loan_acsm_stage_profile = (
        "loan-private",
        (
            "future_explicit_confirmation_required",
            "future_loan_scope_unproven",
            "future_loan_token_unavailable",
            "future_loan_token_invalid",
            "future_acquisition_busy",
        ),
    )

    def connect(self):
        raise AssertionError("capability probe must not authenticate")

    def close(self):
        raise AssertionError("capability probe must not close")

    def loan_stage_activity_reason(self, _error):
        raise AssertionError("capability probe must not execute classifiers")

    def loan_stage_candidate_reason(self, _error):
        raise AssertionError("capability probe must not execute classifiers")

    def rights_selected_delivery_offer_snapshot(self, *_args, **_kwargs):
        raise AssertionError("capability probe must not issue offer GET")

    def rights_selected_delivery_candidate(self, *_args, **_kwargs):
        raise AssertionError("capability probe must not issue token GET")


def rig():
    session = StaticLoanTokenSession(
        descriptor=_DESCRIPTOR, credential_id="credential", label="Test",
        credentials={}, options={},
    )
    account = Account(
        unique_id="borrower", provider_id="future-library",
        credential_id="credential", identity_id="alice", is_primary=False,
    )
    session._accounts = [account]
    session.connected = True
    service = NS(
        current_identity=NS(identity_id="alice"),
        _sessions={("future-library", "credential"): session},
        _session_key=lambda provider, credential: (provider, credential),
    )
    loan = Loan(
        id="loan-1", provider="paris", medium="digital",
        record_id="edition", title="Synthetic", state="active",
        source_provider="future-library", source_credential="credential",
        source_account_id="borrower", identity_id="alice",
    )
    return service, session, account, loan


def _ui_action_available(service, loan):
    # CoreUI alone captures the selected identity for a read-only button.
    current = getattr(service, "current_identity", None)
    return explicit_loan_acsm_action_available(
        service, loan, selected_identity_id=getattr(current, "identity_id", None),
    )


def test_installed_non_bnf_session_exposes_action_without_calls_or_mutation():
    service, session, account, loan = rig()
    before = dict(vars(session))
    assert _ui_action_available(service, loan) is True
    assert vars(session) == before
    assert account in session._accounts


def test_identity_account_credential_and_connection_are_exact_fail_closed():
    service, session, account, loan = rig()
    assert _ui_action_available(service, loan)
    service.current_identity.identity_id = "bob"
    assert not _ui_action_available(service, loan)
    service.current_identity.identity_id = "alice"
    loan.source_credential = "unknown"
    assert not _ui_action_available(service, loan)
    loan.source_credential = "credential"
    loan.source_account_id = "foreign"
    assert not _ui_action_available(service, loan)
    loan.source_account_id = "borrower"
    session._accounts.append(Account(
        unique_id="borrower", provider_id="future-library",
        credential_id="credential", identity_id="alice",
    ))
    assert not _ui_action_available(service, loan)
    session._accounts.pop()
    session.connected = False
    assert not _ui_action_available(service, loan)
    session.connected = True
    session._closed = True
    assert not _ui_action_available(service, loan)


def test_unverified_stage_declaration_and_dynamic_getters_are_never_admitted():
    service, session, _account, loan = rig()
    original_type = type(session)

    class Unverified(original_type):
        loan_acsm_stage_profile = ("loan-private", ("future_loan_scope_unproven",))

    session.__class__ = Unverified
    assert not _ui_action_available(service, loan)

    class Poison(original_type):
        def __getattribute__(self, name):
            raise AssertionError(f"untrusted_getter:{name}")

    session.__class__ = Poison
    assert not _ui_action_available(service, loan)


def test_only_live_loan_is_eligible_and_unrelated_session_never_selected():
    service, _session, _account, loan = rig()
    loan.medium = "physical"
    assert not _ui_action_available(service, loan)
    loan.medium = "digital"
    loan.state = "returned"
    assert not _ui_action_available(service, loan)
    loan.state = "active"
    service._sessions.clear()
    assert not _ui_action_available(service, loan)


def test_coreui_loan_button_uses_inert_contract_not_provider_id():
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/gui/detail_panel.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    panel = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "DetailPanel")
    method = next(node for node in panel.body
                  if isinstance(node, ast.FunctionDef) and node.name == "show_loan")
    body = ast.unparse(method)
    assert "explicit_loan_acsm_action_available" in body
    assert "prepare_explicit_loan_acsm_for_loan" in body
    assert "bnf-pret" not in body
    assert "loan.source_provider ==" not in body
    assert "Récupérer le jeton ACSM du prêt" in body


def test_headless_core_action_visibility_never_reads_navigation():
    service, session, _account, loan = rig()

    class Headless:
        @property
        def current_identity(self):
            raise AssertionError("Core cannot inspect GTK navigation")

        def __getattr__(self, key):
            return getattr(service, key)

    headless = Headless()
    assert explicit_loan_acsm_action_available(
        headless, loan, selected_identity_id="alice",
    ) is True
    for bad in (None, "", "bob", " alice ", False):
        assert explicit_loan_acsm_action_available(
            headless, loan, selected_identity_id=bad,
        ) is False
    assert session.connected is True


def test_acsm_visibility_worker_has_no_current_identity_attribute():
    import inspect

    tree = ast.parse(inspect.getsource(explicit_loan_acsm_action_available))
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "current_identity"
        for node in ast.walk(tree)
    )
