"""#4988: an already borrowed lender is recovered only from exact UI choice."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account
from cifereca.core.borrow_for_identity_preview import (
    BorrowForIdentityOption, BorrowForIdentityPreview,
)
from cifereca.gui.cross_profile_local_send_scope import selected_lender_local_scope
from tests.test_core_p1_4662_live_lender_preview import _candidate


def scope():
    observation = _candidate()
    account = Account(
        identity_id="alex", provider_id="paris", credential_id="card-alex",
        unique_id="", display_name="Synthetic Library",
    )
    coordinates = (
        account.identity_id, account.provider_id, account.credential_id,
        account.unique_id, observation.notice.docbase,
        observation.notice.record_id,
        observation.notice.source_collection or "",
        observation.edition_id,
    )
    option = BorrowForIdentityOption(
        option_id="borrow-profile.v1:synthetic", borrowing_identity_id="alex",
        lender_name="Alex", provider_id="paris", edition_id="edition-1",
        label="Emprunter avec Alex", transfer_state="unknown",
        _source_coordinates=coordinates,
    )
    preview = BorrowForIdentityPreview(
        requesting_identity_id="default", status="external",
        label="Emprunter avec Alex", options=(option,),
        rejected_count=0, requires_explicit_choice=False,
    )
    service = NS(accounts=[account],
                 execute_cross_profile_borrow=lambda **kw: pytest.fail("no POST"),
                 materialize_to_identity=lambda *a: pytest.fail("no copy"))
    return service, account, observation, preview, option


def chosen(service, observation, preview, option):
    return selected_lender_local_scope(
        service, captured_preview=preview, selected_option_id=option.option_id,
        expected_edition_id="edition-1", observations=(observation,),
    )


def test_only_chosen_account_and_exact_edition_can_feed_separate_send():
    service, account, observation, preview, option = scope()
    result = chosen(service, observation, preview, option)
    assert result is not None
    assert result.account is account
    assert result.notice is observation.notice
    assert result.executable is False
    assert "card-alex" not in repr(result)


@pytest.mark.parametrize("mutation", [
    "account", "account_duplicate", "edition", "collection", "record",
    "borrower", "edition_borrower", "provider", "duplicate_offer",
])
def test_stale_or_ambiguous_selection_rejected_without_network(mutation):
    service, account, observation, preview, option = scope()
    if mutation == "account":
        service.accounts = [replace(account, credential_id="wrong")]
    elif mutation == "account_duplicate":
        service.accounts.append(account)
    elif mutation == "edition":
        observation = replace(observation, edition_id="different")
    elif mutation == "collection":
        observation = replace(observation, notice=replace(
            observation.notice, source_collection="different"))
    elif mutation == "record":
        observation = replace(observation, notice=replace(
            observation.notice, record_id="different"))
    elif mutation == "borrower":
        observation = replace(observation, evidence=replace(
            observation.evidence, account_id="different"))
    elif mutation == "edition_borrower":
        observation = replace(observation, edition_evidence=replace(
            observation.edition_evidence, borrower_id="different"))
    elif mutation == "provider":
        observation = replace(observation, notice=replace(
            observation.notice, source_provider="different"))
    else:
        observation = (observation, observation)
    observations = observation if type(observation) is tuple else (observation,)
    assert selected_lender_local_scope(
        service, captured_preview=preview, selected_option_id=option.option_id,
        expected_edition_id="edition-1", observations=observations,
    ) is None


def test_wrong_option_never_defaults_to_first_lender():
    service, _, observation, preview, option = scope()
    assert selected_lender_local_scope(
        service, captured_preview=preview,
        selected_option_id="borrow-profile.v1:another",
        expected_edition_id="edition-1",
        observations=(observation,),
    ) is None
