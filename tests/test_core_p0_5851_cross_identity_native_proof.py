"""P0 #5851: untrusted loan ledger evidence cannot forge owner or skip veto."""
import pytest

from cifereca.core.adept_borrower import _cross_identity_loan_conflict


class Hostile:
    def __bool__(self):
        raise AssertionError("do not truth-test provider-owned evidence")

    def __str__(self):
        raise AssertionError("do not stringify provider-owned evidence")

    def __eq__(self, other):
        raise AssertionError("do not compare provider-owned evidence")


class HostileString(str):
    def strip(self):
        raise AssertionError("do not normalize provider-owned subclass")

    def __eq__(self, other):
        raise AssertionError("do not compare provider-owned subclass")


def veto(acquisitions=(), *, rights=(), loan="loan-1", owner="alice"):
    return _cross_identity_loan_conflict(
        acquisitions, provider_loan_id=loan, identity_id=owner,
        entitlements=rights,
    )


@pytest.mark.parametrize("bad", [Hostile(), HostileString("loan-1"), None, 5, True, ""])
def test_non_native_requested_loan_is_always_a_veto(bad):
    assert veto(loan=bad)


@pytest.mark.parametrize("bad", [Hostile(), HostileString("alice"), None, 5, True, ""])
def test_non_native_requested_owner_is_always_a_veto(bad):
    assert veto(owner=bad)


@pytest.mark.parametrize("row", [
    Hostile(), {"strategy": "loan_auto", "metadata": Hostile()},
    {"strategy": "loan_auto", "metadata": {"provider_loan_id": Hostile()}},
    {"strategy": "loan_auto", "metadata": {"provider_loan_id": HostileString("loan-1")}},
    {"metadata": {"provider_loan_id": "loan-1"}, "strategy": Hostile()},
    {"metadata": {"provider_loan_id": "loan-1"}, "strategy": "loan_auto", "identity_id": Hostile()},
])
def test_hostile_acquisition_row_fails_closed_without_coercion(row):
    assert veto([row])


@pytest.mark.parametrize("row", [
    Hostile(), {"loan_id": Hostile()},
    {"loan_id": HostileString("loan-1")},
    {"loan_id": "loan-1", "identity_id": Hostile()},
])
def test_hostile_entitlement_row_fails_closed_without_coercion(row):
    assert veto(rights=[row])


def test_matching_same_owner_evidence_is_not_a_grant_and_does_not_veto():
    assert not veto(
        [{"metadata": {"provider_loan_id": "loan-1"}, "strategy": "loan_auto", "identity_id": "alice"}],
        rights=[{"loan_id": "loan-1", "identity_id": "alice"}],
    )


def test_other_owner_evidence_vetoes_without_identity_fallback():
    assert veto(
        [{"metadata": {"provider_loan_id": "loan-1"}, "strategy": "loan_auto", "identity_id": "bob"}],
    )
    assert veto(rights=[{"loan_id": "loan-1", "identity_id": "bob"}])


def test_unrelated_valid_loan_is_not_an_owner_conflict():
    assert not veto(
        [{"metadata": {"provider_loan_id": "loan-2"}, "strategy": "loan_auto", "identity_id": "bob"}],
        rights=[{"loan_id": "loan-2", "identity_id": "bob"}],
    )


@pytest.mark.parametrize("strategy", [None, "manual", "import", "adept_manual"])
def test_foreign_acquisition_strategy_does_not_claim_an_automatic_loan(strategy):
    assert not veto([
        {"metadata": {"provider_loan_id": "loan-1"},
         "strategy": strategy, "identity_id": "different-owner"},
    ])
