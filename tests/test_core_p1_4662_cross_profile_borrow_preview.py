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
        "Emprunter avec Alice · Bibliothèque · choix 1/2",
        "Emprunter avec Alice · Bibliothèque · choix 2/2",
        "Emprunter avec Charlie",
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


def test_same_account_two_exact_branches_require_explicit_selection():
    a = witness()
    changed = replace(a, notice=replace(a.notice,
        docbase="OTHER", scenario="OTHER"),
        eligibility=replace(a.eligibility, branch="OTHER"))
    actual = project(a, changed)
    assert actual.status == "choose"
    assert actual.requires_explicit_choice is True
    assert len(actual.options) == 2
    assert len({x.option_id for x in actual.options}) == 2
    assert all(x.executable is False for x in actual.options)


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


def test_same_account_and_record_in_two_collections_are_distinct_offers():
    original = witness()
    second = replace(original, notice=replace(
        original.notice, source_collection="collection-2",
    ))
    a = project(original, second)
    b = project(second, original)
    assert a == b
    assert a.status == "choose"
    assert a.requires_explicit_choice is True
    assert len(a.options) == 2
    assert len({x.option_id for x in a.options}) == 2
    assert len({x._source_coordinates for x in a.options}) == 2
    assert all(x.executable is False for x in a.options)


def test_same_exact_collection_conflicting_source_epoch_still_fails_closed():
    original = witness()
    stale = replace(original, source_epoch=2)
    with pytest.raises(ValueError, match="conflicting_cross_profile_borrow_offer"):
        project(original, stale)


def test_distinct_display_account_names_yield_readable_options_not_secret_ids():
    first = witness("alice", "card-secret-A", "PRIVATE_ACCOUNT_A")
    second = witness("alice", "card-secret-B", "PRIVATE_ACCOUNT_B")
    first = replace(first, account=replace(
        first.account, display_name="Réseau municipal",
    ))
    second = replace(second, account=replace(
        second.account, display_name="Bibliothèque universitaire",
    ))
    result = project(first, second)
    assert result.status == "choose"
    assert {x.label for x in result.options} == {
        "Emprunter avec Alice · Réseau municipal",
        "Emprunter avec Alice · Bibliothèque universitaire",
    }
    assert all("PRIVATE_ACCOUNT" not in x.label and "card-secret" not in x.label
               for x in result.options)
    assert result.executable is False


def test_sensitive_or_duplicate_account_display_falls_back_to_safe_ordinals():
    primary = witness("alice", "card-A", "PRIVATE_ACCOUNT_A")
    secondary = witness("alice", "card-B", "PRIVATE_ACCOUNT_B")
    for unsafe in ("alice@example.com", "123456789012", "https://secret.test"):
        first = replace(primary, account=replace(
            primary.account, display_name=unsafe,
        ))
        second = replace(secondary, account=replace(
            secondary.account, display_name=unsafe,
        ))
        result = project(first, second)
        assert len({x.label for x in result.options}) == 2
        assert all("Compte bibliothèque · choix " in x.label for x in result.options)
        assert all(unsafe not in x.label for x in result.options)
        assert all(x.executable is False for x in result.options)


def test_single_source_keeps_simple_label_and_no_account_hints_in_hash():
    result = project(witness("alice"))
    assert result.label == "Emprunter avec Alice"
    assert result.options[0].label == "Emprunter avec Alice"
    assert result.options[0].option_id.startswith("borrow-profile.v1:")
    assert "Bibliothèque" not in result.options[0].option_id


def test_same_account_different_collections_also_disambiguate_without_choice_by_order():
    first = witness("alice")
    second = replace(first, notice=replace(
        first.notice, source_collection="collection-2",
    ))
    direct, reversed_options = project(first, second), project(second, first)
    assert direct == reversed_options
    assert direct.status == "choose"
    assert len({x.label for x in direct.options}) == 2
    assert len({x.option_id for x in direct.options}) == 2
    assert direct.requires_explicit_choice is True


def test_option_id_is_revoked_by_context_epoch_even_after_profile_aba():
    from dataclasses import replace

    initial = witness(identity="alice", credential="card-A")
    before = project(initial, requester="bob", epoch=4)
    assert len(before.options) == 1

    # A→B→A may restore the exact same account, edition and source epoch.
    # A fresh Core context must still produce a different opaque button id.
    current = replace(initial, service_context_epoch=5)
    after = project(current, requester="bob", epoch=5)
    assert len(after.options) == 1
    assert before.options[0]._source_coordinates == after.options[0]._source_coordinates
    assert before.options[0].option_id != after.options[0].option_id
    assert before.options[0].executable is False
    assert after.options[0].executable is False

    # Old witness cannot enter the newer preview even with identical source.
    stale = project(initial, requester="bob", epoch=5)
    assert stale.options == () and stale.rejected_count == 1


def test_option_id_remains_deterministic_within_one_context_epoch():
    one = project(witness(), requester="bob", epoch=4).options[0]
    two = project(witness(), requester="bob", epoch=4).options[0]
    assert one.option_id == two.option_id


def test_account_choice_labels_do_not_swap_when_epoch_changes_opaque_ids():
    from dataclasses import replace

    first = witness(identity="alice", credential="card-A")
    second = witness(identity="alice", credential="card-B")
    before = project(first, second, requester="bob", epoch=4)
    after = project(
        replace(first, service_context_epoch=5),
        replace(second, service_context_epoch=5),
        requester="bob", epoch=5,
    )
    assert before.status == after.status == "choose"
    assert [(x._source_coordinates, x.label) for x in before.options] == [
        (x._source_coordinates, x.label) for x in after.options
    ]
    assert {x.option_id for x in before.options}.isdisjoint(
        x.option_id for x in after.options
    )
