"""P0 #5975: no selected ADEPT profile can outlive the pinned loan lease.

Invoke the real extracted profile preflight, with only its external collaborators
stubbed. No provider GET, ACSM delivery, Adobe execution or background retry.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from tests.test_core_p0_1120_adept_profile_guard_extraction import _fixture


class HostileIdentity:
    def __str__(self):
        raise AssertionError("private borrower identity must not be coerced")

    def __bool__(self):
        raise AssertionError("private borrower identity must not be tested")

    def __eq__(self, _other):
        raise AssertionError("private borrower identity must not be compared")


class HostileProfile(str):
    def __eq__(self, _other):
        raise AssertionError("private profile must not select ADEPT")


@pytest.mark.parametrize("stage", ["resolver", "profile_inventory", "readiness"])
@pytest.mark.parametrize("field,replacement", [
    ("id", "foreign-loan"),
    ("record_id", "foreign-edition"),
    ("source_provider", "different-provider"),
    ("source_credential", "foreign-credential"),
    ("source_account_id", "different-borrower"),
])
def test_mutating_external_profile_hook_never_returns_an_adept_profile(
    stage, field, replacement,
):
    invoke, store, borrower, resolver, profiles, loan, opts = _fixture()
    if stage == "resolver":
        def mutate_resolver(_borrower_id):
            setattr(loan, field, replacement)
            return "reader-a", "explicit_binding"
        resolver.side_effect = mutate_resolver
    elif stage == "profile_inventory":
        def mutate_profiles():
            setattr(loan, field, replacement)
            return [{"name": "reader-a", "fetch_capable": True}]
        profiles.side_effect = mutate_profiles
    else:
        def mutate_readiness(_row):
            setattr(loan, field, replacement)
            return False
        opts["adept_acsm_plan"] = NS(
            profile_is_explicitly_acsm_unready=mutate_readiness,
        )

    result = invoke()
    assert result == {
        "status": "attention_required", "loan_id": "loan-1",
        "code": "adept_borrower_ambiguous", "retry": "explicit_only",
    }
    assert store.call_count == 1
    receipt = store.call_args.args[0]
    assert receipt["attention_code"] == "adept_borrower_ambiguous"
    assert receipt["metadata"]["provider_loan_id"] == "loan-1"
    assert receipt["credential_id"] == "reader"
    assert receipt["account_id"] == "primary"
    assert receipt["identity_id"] == "identity-1"
    assert receipt["metadata"]["prior"] == "kept"
    assert borrower.call_count == 1
    if stage == "resolver":
        profiles.assert_not_called()
    else:
        resolver.assert_called_once_with("identity-1")


def test_profile_resolver_exception_after_source_drift_is_borrower_refusal():
    from cifereca.identities import AdeptProfileResolutionError

    invoke, store, _borrower, resolver, profiles, loan, _opts = _fixture()

    def mutate_and_raise(_identity):
        loan.source_account_id = "different-borrower"
        raise AdeptProfileResolutionError(
            "https://private.invalid/profile?token=SECRET",
            code="adept_profile_ambiguous",
        )
    resolver.side_effect = mutate_and_raise
    result = invoke()
    assert result["code"] == "adept_borrower_ambiguous"
    assert result["retry"] == "explicit_only"
    assert store.call_count == 1
    assert store.call_args.args[0]["attention_code"] == "adept_borrower_ambiguous"
    assert "SECRET" not in repr(result) + repr(store.call_args.args[0])
    profiles.assert_not_called()


@pytest.mark.parametrize("borrower_id", [None, "", False, 0, HostileIdentity(),
                                          HostileProfile("identity-1")])
def test_false_proven_borrower_id_never_reaches_profile_selection(borrower_id):
    invoke, store, borrower, resolver, profiles, _loan, _opts = _fixture()
    borrower.return_value = (borrower_id, None)
    result = invoke()
    assert result["code"] == "adept_borrower_ambiguous"
    assert result["retry"] == "explicit_only"
    assert store.call_count == 1
    resolver.assert_not_called()
    profiles.assert_not_called()


@pytest.mark.parametrize("profile_name", [None, "", False, 0, HostileIdentity(),
                                             HostileProfile("reader-a")])
def test_untrusted_profile_name_does_not_reach_profile_inventory(profile_name):
    invoke, store, _borrower, resolver, profiles, _loan, _opts = _fixture()
    resolver.return_value = profile_name, "explicit_binding"
    result = invoke()
    assert result == {
        "status": "attention_required", "loan_id": "loan-1",
        "code": "adept_profile_required", "error": "Profil ADEPT indisponible",
    }
    assert store.call_count == 1
    assert store.call_args.args[0]["attention_code"] == "adept_profile_required"
    profiles.assert_not_called()
