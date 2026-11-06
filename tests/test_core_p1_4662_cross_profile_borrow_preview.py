"""#4662: lender identity + credential/edition selection remains immutable advice.

No provider, Storage, network, claims, transfer or callback is invoked.
"""
from dataclasses import replace

import pytest

from cifereca.models import Account, SearchResult
from cifereca.plugin_api.lending_capabilities import (
    BorrowerOperationEligibility, LendingOperationFact,
)
from cifereca.core.borrow_for_identity_preview import (
    BorrowForIdentityWitness, preview_borrow_for_identity,
)


def witness(identity="alice", credential="card-A", account="borrower-A",
            *, lender_name=None, record="notice-1", docbase="EBOOKS",
            edition="edition-1", observed_at=1000, source_epoch=1,
            transfer="unknown"):
    a = Account(
        identity_id=identity, provider_id="paris", credential_id=credential,
        unique_id=account, display_name="Bibliothèque",
    )
    item = SearchResult(
        id="result:" + identity + ":" + credential, record_id=record,
        docbase=docbase, medium="digital", title="Même titre",
        author=None, type=None, format=None, publisher=None,
        published_at=None, description=None, thumbnail_url=None,
        friendly_url=None, scenario="EBOOKS",
        source_provider="paris", source_credential=credential,
        source_collection="collection-1",
    )
    cap = LendingOperationFact(
        "loan.borrow", "supported", "available_unverified_permission",
        True, True, True, True, True,
    )
    elig = BorrowerOperationEligibility(
        "eligible_observed", "borrow_permission_observed", "loan.borrow",
        docbase,
    )
    return BorrowForIdentityWitness(
        account=a, notice=item, capability=cap, eligibility=elig,
        lender_name=lender_name or identity.capitalize(), observed_at=observed_at,
        service_context_epoch=4, source_epoch=source_epoch,
        edition_id=edition, transfer_state=transfer,
    )


def project(*witnesses, **updates):
    return preview_borrow_for_identity(
        requesting_identity_id=updates.get("requester", "bob"),
        expected_edition_id=updates.get("edition", "edition-1"),
        witnesses=list(witnesses), now=updates.get("now", 1100),
        context_epoch=updates.get("epoch", 4),
    )


def test_no_offer_is_inert_unknown_not_false_global_zero():
    output = project()
    assert (output.status, output.label) == ("none", "Emprunt non vérifié")
    assert output.options == ()
    assert output.executable is False
    assert output.requires_explicit_choice is False


def test_same_profile_keeps_normal_action_without_creating_loan():
    v = project(witness("bob"))
    assert v.status == "own"
    assert v.label == "Emprunter"
    assert len(v.options) == 1
    assert v.options[0].borrowing_identity_id == "bob"
    assert v.options[0].executable is False
    assert not hasattr(v.options[0], "borrowed_loan")


def test_single_other_profile_is_labeled_but_not_a_transfer_permission():
    v = project(witness("alice", transfer="blocked"))
    assert v.status == "external"
    assert v.label == "Emprunter avec Alice"
    assert len(v.options) == 1
    assert v.options[0].transfer_state == "blocked"
    assert v.options[0].executable is False


def test_two_profiles_and_two_accounts_never_default_to_first():
    offers = [
        witness("alice", credential="first", account="reader-one"),
        witness("charlie", credential="charlie", account="reader-charlie"),
        witness("alice", credential="second", account="reader-two"),
    ]
    a = project(*offers)
    b = project(*reversed(offers))
    assert a == b
    assert a.status == "choose"
    assert a.label == "Emprunter avec…"
    assert a.requires_explicit_choice is True
    assert len(a.options) == 3
    assert len({x.option_id for x in a.options}) == 3
    assert all(x.executable is False for x in a.options)
    assert {x.label for x in a.options} == {
        "Emprunter avec Alice", "Emprunter avec Charlie",
    }
    assert "card-" not in repr(a)


def test_same_exact_witness_deduplicated_but_different_credential_preserved():
    a = witness()
    v = project(a, a, witness(credential="card-B", account="borrower-B"))
    assert v.status == "choose"
    assert len(v.options) == 2


def test_conflicting_epoch_or_transfer_for_same_account_fails_closed():
    original = witness()
    for changed in (witness(source_epoch=2), witness(transfer="blocked")):
        with pytest.raises(ValueError, match="conflicting_cross_profile_borrow_offer"):
            project(original, changed)
    # An unselected edition never becomes an action for the requested book.
    v = project(original, witness(edition="edition-2"))
    assert len(v.options) == 1 and v.rejected_count == 1


@pytest.mark.parametrize("change", [
    {"source_epoch": -1}, {"source_epoch": True},
    {"observed_at": 1}, {"observed_at": 1200},
    {"service_context_epoch": 5},
    {"edition_id": "edition-OTHER"},
    {"lender_name": "Bad\nName"},
    {"transfer_state": "allowed"},
])
def test_old_foreign_or_unsupported_witness_is_rejected_not_promoted(change):
    v = project(replace(witness(), **change))
    assert v.options == () and v.rejected_count == 1
    assert v.executable is False


def test_foreign_credential_wrong_docbase_nonprimary_and_physical_refused():
    good = witness()
    candidates = [
        replace(good, account=replace(good.account, credential_id="other")),
        replace(good, notice=replace(good.notice, source_credential="other")),
        replace(good, notice=replace(good.notice, medium="physical")),
        replace(good, account=replace(good.account, is_primary=False)),
        replace(good, eligibility=replace(good.eligibility, branch="CATALOGUE")),
        replace(good, eligibility=replace(good.eligibility,
                                         state="unverified")),
        replace(good, capability=replace(good.capability,
                                        availability="unsupported")),
    ]
    v = project(*candidates)
    assert v.status == "none" and v.rejected_count == len(candidates)


def test_offer_id_pins_edition_account_and_epoch_but_never_authorizes_replay():
    left = project(witness(source_epoch=1)).options[0]
    right = project(witness(source_epoch=2)).options[0]
    other = project(witness(credential="other")).options[0]
    assert len({left.option_id, right.option_id, other.option_id}) == 3
    assert left.option_id.startswith("borrow-profile.v1:")
    assert "alice" not in left.option_id
    assert left._source_coordinates[0] == "alice"


def test_ambiguous_same_account_two_branches_is_not_silently_chosen():
    a = witness()
    changed = replace(a, notice=replace(a.notice,
        docbase="OTHER", scenario="OTHER"),
        eligibility=replace(a.eligibility, branch="OTHER"))
    with pytest.raises(ValueError, match="conflicting_cross_profile_borrow_offer"):
        project(a, changed)


def test_invalid_snapshot_is_rejected_before_any_action():
    for bad in (None, {}, [witness()] * 65):
        with pytest.raises(ValueError, match="invalid_cross_profile_borrow_scope"):
            preview_borrow_for_identity(
                requesting_identity_id="bob",
                expected_edition_id="edition-1",
                witnesses=bad, now=1100, context_epoch=4,
            )
    with pytest.raises(ValueError, match="invalid_cross_profile_borrow_witness"):
        project(witness(), object())
    with pytest.raises(ValueError, match="invalid_cross_profile_borrow_scope"):
        project(witness(), requester="bad\nidentity")


def test_transfer_unknown_remains_unknown_not_silently_enabled():
    one = project(witness())
    assert one.options[0].transfer_state == "unknown"
    assert one.executable is False
