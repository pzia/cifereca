"""#4987: an explicit lender plan is not a loan, consent or transfer."""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.core.borrow_for_identity_action_plan import (
    prepare_cross_profile_lender_action_plan as plan,
)
from tests.test_core_p1_4662_live_lender_preview import (
    _candidate, _preview, harness,
)


def prepare(service, captured, selected, *rows):
    return plan(
        service, captured_preview=captured, selected_option_id=selected,
        expected_edition_id="edition-1", observations=rows, now=1100,
    )


def test_single_exact_lender_plan_is_internal_and_does_not_borrow(harness):
    service, account, session = harness
    row = _candidate()
    captured = _preview(service, row)
    result = prepare(service, captured, captured.options[0].option_id, row)
    assert (result.status, result.reason_code) == (
        "ready_for_consent", "lender_exact_plan_unverified",
    )
    assert result._account is account and result._notice is row.notice
    assert result._claim_key == borrow_claim_key(account, row.notice)
    assert result.executable is result.transfer_permitted is False
    assert "card-alex" not in repr(result)
    assert "record-1" not in repr(result)
    assert session.calls == 0
    assert service.storage.metadata_get(result._claim_key) is None
    assert service.current_identity.identity_id == "default"


def test_two_identical_lender_observations_do_not_create_two_plan_candidates(harness):
    service, _, session = harness
    row = _candidate()
    captured = _preview(service, row, row)
    selected = captured.options[0].option_id
    assert prepare(service, captured, selected, row, row).status == "revoked"
    assert session.calls == 0


def test_exact_chosen_collection_is_not_substituted_by_another(harness):
    service, _, session = harness
    a = _candidate(collection="one")
    b = _candidate(collection="two")
    captured = _preview(service, a, b)
    selected_a = next(
        option for option in captured.options
        if option._source_coordinates[6] == "one"
    )
    assert prepare(service, captured, selected_a.option_id, a, b)._notice is a.notice
    assert prepare(service, captured, selected_a.option_id, b).status == "revoked"
    assert session.calls == 0


@pytest.mark.parametrize("change", ["account_epoch", "source_epoch", "disabled", "spent", "identity"])
def test_revoked_source_or_claim_cannot_produce_lender_action_plan(harness, change):
    service, account, session = harness
    row = _candidate()
    captured = _preview(service, row)
    if change == "account_epoch":
        service._account_context_epoch += 2
    elif change == "source_epoch":
        service._source_context_epoch += 2
    elif change == "disabled":
        service.storage.provider_connection_put({
            "identity_id": "alex", "provider_id": "paris",
            "credential_id": "card-alex", "label": "Disabled",
            "enabled": False, "metadata": {},
        })
    elif change == "spent":
        service.storage.metadata_set(borrow_claim_key(account, row.notice), "{bad")
    else:
        service.current_identity = service.identity_by_id("alex")
    result = prepare(service, captured, captured.options[0].option_id, row)
    assert result.status == "revoked"
    assert result.executable is result.transfer_permitted is False
    assert result._account is result._notice is result._claim_key is None
    assert session.calls == 0


def test_rebind_during_plan_claim_read_revokes_instead_of_exposing_account(harness, monkeypatch):
    service, _, session = harness
    row = _candidate()
    captured = _preview(service, row)
    original = service.storage.metadata_get

    def rebind(key):
        result = original(key)
        service._source_context_epoch += 1
        return result

    monkeypatch.setattr(service.storage, "metadata_get", rebind)
    result = prepare(service, captured, captured.options[0].option_id, row)
    assert result.status == "revoked"
    assert result._claim_key is None
    assert session.calls == 0


def test_forged_or_nonconsented_inputs_never_permit_a_supplier_request(harness):
    service, _, session = harness
    row = _candidate()
    captured = _preview(service, row)
    selected = captured.options[0].option_id
    for changed in (
        None, {}, [object()], [row] * 65,
        [replace(row, edition_id="foreign-edition")],
    ):
        result = prepare(service, captured, selected, *(changed or ())
                         ) if type(changed) in (tuple, list) else plan(
                             service, captured_preview=captured,
                             selected_option_id=selected,
                             expected_edition_id="edition-1",
                             observations=changed, now=1100,
                         )
        assert result.status == "revoked"
    assert session.calls == 0
