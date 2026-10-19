"""P0 #1120: ADEPT borrower/profile extraction, no extra I/O or retry.

Only the original facade's injected collaborators are mocked. The unit under
test owns exactly the original grant/profile/readiness and error-ledger paths;
it cannot issue ACSM GET, Adobe fulfillment, scan or return requests.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core import adept_acsm_plan, loan_adept_profile_guard
from cifereca.identities import AdeptProfileResolutionError


def _fixture(*, provider="paris", borrower_code=None, profile_error=None,
             profiles=None):
    loan = NS(source_provider=provider, id="loan-1")
    borrower = Mock(return_value=("identity-1", borrower_code))
    resolver = Mock(return_value=("reader-a", "explicit_binding"))
    if profile_error is not None:
        resolver.side_effect = profile_error
    provider_profiles = Mock(return_value=profiles if profiles is not None else [
        {"name": "reader-a", "fetch_capable": True},
    ])
    store = Mock()
    owner = NS(
        storage=NS(acquisition_put=store),
        _paris_adept_identity_for_loan=borrower,
        resolve_adept_profile_for_identity=resolver,
        kobo_profiles=provider_profiles,
        _adept_profile_row=lambda name, values:
            next((row for row in values if row.get("name") == name), None),
    )
    options = dict(
        identity=NS(identity_id="identity-1"),
        ledger_base={"entitlement_id": "ent-1", "identity_id": "identity-1"},
        previous_meta={"prior": "kept"},
        retained_acsm=Path("/synthetic/staged.acsm"),
        trace_ref="0123456789abcdef0123",
        AdeptProfileResolutionError=AdeptProfileResolutionError,
        adept_acsm_plan=adept_acsm_plan,
        _TRACE_LOG=logging.getLogger("cifereca.tests.loan-guard"),
    )
    def invoke():
        return loan_adept_profile_guard.check(owner, loan, **options)
    return invoke, store, borrower, resolver, provider_profiles, loan, options


def test_borrower_revocation_aborts_before_profile_or_runtime():
    invoke, store, borrower, resolver, provider_profiles, loan, options = _fixture(
        borrower_code="adept_borrower_binding_required",
    )
    assert invoke() == {
        "status": "attention_required", "loan_id": loan.id,
        "code": "adept_borrower_binding_required", "retry": "explicit_only",
    }
    borrower.assert_called_once_with(loan, connection_identity_id="identity-1")
    resolver.assert_not_called()
    provider_profiles.assert_not_called()
    store.assert_called_once()
    row = store.call_args.args[0]
    assert row["status"] == "attention_required"
    assert row["strategy"] == "loan_auto"
    assert row["executor"] == "kobo-adept"
    assert row["attention_code"] == "adept_borrower_binding_required"
    assert row["metadata"] == {
        "prior": "kept", "provider_loan_id": "loan-1",
        "acsm_staging_path": "/synthetic/staged.acsm",
    }


def test_unresolved_profile_keeps_exact_refusal_and_staged_receipt():
    error = AdeptProfileResolutionError(
        "Profil non choisi", code="adept_profile_ambiguous",
    )
    invoke, store, borrower, resolver, profiles, loan, opts = _fixture(profile_error=error)
    outcome = invoke()
    assert outcome == {
        "status": "attention_required", "loan_id": loan.id,
        "code": "adept_profile_ambiguous", "error": "Profil ADEPT indisponible",
    }
    profiles.assert_not_called()
    [row] = [c.args[0] for c in store.call_args_list]
    assert row["attention_code"] == "adept_profile_ambiguous"
    assert row["error_message"] == "Profil ADEPT indisponible"
    assert row["metadata"]["acsm_staging_path"] == str(opts["retained_acsm"])


def test_explicit_negative_readiness_blocks_without_adobe_attempt():
    invoke, store, borrower, resolver, profiles, loan, opts = _fixture(profiles=[
        {"name": "reader-a", "fetch_capable": True,
         "provisioning": {"acsm_fetch_ready": False}},
    ])
    refusal = invoke()
    assert refusal["status"] == "attention_required"
    assert refusal["code"] == "adept_profile_acsm_not_ready"
    assert "n'est pas prêt" in refusal["error"]
    store.assert_called_once()
    row = store.call_args.args[0]
    assert row["attention_code"] == "adept_profile_acsm_not_ready"
    assert row["error_message"] == refusal["error"]
    assert row["metadata"]["adept_attempt_profile"] == "reader-a"
    resolver.assert_called_once_with("identity-1")


@pytest.mark.parametrize("profile_row", [
    {"name": "reader-a", "fetch_capable": True},
    {"name": "reader-a"},
    {"name": "reader-a", "provisioning": {"acsm_fetch_ready": True}},
])
def test_missing_or_positive_readiness_keeps_old_compatibility(profile_row):
    invoke, store, borrower, resolver, profiles, _, _ = _fixture(profiles=[profile_row])
    assert invoke() == ("identity-1", "reader-a")
    store.assert_not_called()


def test_nonparis_profile_resolution_does_not_change_borrower_account():
    invoke, store, borrower, resolver, profiles, *_ = _fixture(provider="kobo")
    assert invoke() == ("identity-1", "reader-a")
    borrower.assert_not_called()
    store.assert_not_called()


def test_profile_inventory_failure_is_nonblocking_as_before():
    invoke, store, borrower, resolver, profiles, *_ = _fixture()
    profiles.side_effect = RuntimeError("synthetic provider unavailable")
    assert invoke() == ("identity-1", "reader-a")
    store.assert_not_called()


def test_service_lock_and_profile_runtime_guards_remain_ordered_in_one_method():
    service_path = Path(__file__).resolve().parents[1] / "src/cifereca/services.py"
    tree = ast.parse(service_path.read_text(encoding="utf-8"))
    service = next(x for x in tree.body
                   if isinstance(x, ast.ClassDef) and x.name == "LibraryService")
    method = next(x for x in service.body if isinstance(x, ast.FunctionDef)
                  and x.name == "_acquire_active_loan_automatically_guarded")
    source = ast.unparse(method)
    assert "loan_acquisition_admission.resolve" in source
    assert "loan_local_preparation.prepare" in source
    assert "loan_provider_delivery.acquire_provider_loan" in source
    assert "loan_adept_profile_guard.check" in source
    assert "loan_adept_fulfillment.fulfill" in source
    assert "loan_provider_delivery.index_local_loan_artifact" in source
    assert source.index("loan_provider_delivery.acquire_provider_loan") < source.index(
        "loan_adept_profile_guard.check"
    ) < source.index("loan_adept_fulfillment.fulfill") < source.index(
        "loan_provider_delivery.index_local_loan_artifact"
    )
    # No scheduling, retry, new profile choice or ADEPT import belongs in the helper.
    helper = ast.unparse(ast.parse(
        (service_path.parent / "core/loan_adept_profile_guard.py").read_text(encoding="utf-8")
    ))
    for forbidden in ("execute_acquisition_request", "acquire_digital_loan",
                      "download_digital", "source_request", "request.get(", "profile_binding_set"):
        assert forbidden not in helper


@pytest.mark.parametrize("unsafe_code", [
    "https://private.invalid/ebook?auth=NEVER_SHOW",
    "adept_profile_required_PRIVATE",
    None, True, {"profile": "secret"},
])
def test_profile_refusal_never_persists_private_codes_or_exception_messages(unsafe_code):
    private = "https://private.invalid/path/Adobe?token=NEVER_SHOW"
    class HostileResolution(AdeptProfileResolutionError):
        def __str__(self):
            raise AssertionError("Raw profile exception must never be stringified")
        def __repr__(self):
            raise AssertionError("Raw profile exception must never be repr'ed")

    failure = HostileResolution(private, code=unsafe_code)
    invoke, store, _borrower, _resolver, profiles, loan, _opts = _fixture(
        profile_error=failure,
    )
    outcome = invoke()
    assert outcome == {
        "status": "attention_required",
        "loan_id": loan.id,
        "code": "adept_profile_required",
        "error": "Profil ADEPT indisponible",
    }
    row = store.call_args.args[0]
    assert row["attention_code"] == "adept_profile_required"
    assert row["error_message"] == "Profil ADEPT indisponible"
    assert private not in repr(row) + repr(outcome)
    assert "NEVER_SHOW" not in repr(row) + repr(outcome)
    profiles.assert_not_called()

