"""P1 #6002: precedence, exact receipt boundaries, no inferred permission."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from cifereca.core.digital_loan_remaining_policy import (
    DigitalCapacityScope, DigitalInventoryReceipt, ProviderCapacityEvidence,
    evaluate_digital_remaining,
)

NOW = datetime(2026, 11, 14, 20, 0, tzinfo=timezone.utc)
SCOPE = DigitalCapacityScope("a", "paris", "card", "subscriber", "reader")
MAX_AGE = timedelta(hours=24)


def provider(kind, value, **kw):
    return ProviderCapacityEvidence(SCOPE, kind, value, NOW, 1, **kw)


def inventory(**kw):
    fields = dict(scope=SCOPE, active_loan_ids=("book1", "book2", "book1"),
                  observed_at=NOW, epoch=1, status="success",
                  pagination_complete=True, durably_committed=True)
    fields.update(kw)
    return DigitalInventoryReceipt(**fields)


def decision(**kw):
    fields = dict(scope=SCOPE, now=NOW, epoch=1, max_age=MAX_AGE)
    fields.update(kw)
    return evaluate_digital_remaining(**fields)


def test_provider_direct_wins_even_without_inventory_or_with_manual_limit():
    for value in (0, 2):
        got = decision(provider_remaining=provider("remaining", value),
                       manual_total=4, inventory=inventory(status="partial"))
        assert (got.status, got.remaining, got.reason, got.eligibility) == (
            "provider_direct", value, "provider_remaining", "not_verified"
        )


def test_provider_ceiling_is_used_before_manual_with_complete_exact_count():
    got = decision(provider_total=provider("total", 3), manual_total=99,
                   inventory=inventory())
    assert (got.status, got.remaining) == ("provider_calculated", 1)
    assert decision(provider_total=provider("total", 4),
                    inventory=inventory()).remaining == 2


def test_manual_fallback_only_after_complete_fresh_inventory_and_without_provider():
    got = decision(manual_total=4, inventory=inventory())
    assert (got.status, got.remaining, got.reason) == (
        "manual_estimate", 2, "personal_ceiling"
    )
    assert decision(manual_total=0, inventory=inventory()).remaining == 0
    assert decision(manual_total=4, inventory=inventory(active_loan_ids=())).remaining == 4


@pytest.mark.parametrize("receipt", [
    None,
    inventory(status="partial"),
    inventory(status="failed"),
    inventory(pagination_complete=False),
    inventory(durably_committed=False),
    inventory(epoch=2),
    inventory(scope=replace(SCOPE, borrower_id="someone_else")),
    inventory(scope=replace(SCOPE, credential_id="other")),
    inventory(scope=replace(SCOPE, identity_id="b")),
    inventory(observed_at=NOW - timedelta(days=2)),
    inventory(observed_at=NOW + timedelta(seconds=1)),
])
def test_unverified_inventory_cannot_calculate_provider_or_manual_remaining(receipt):
    for fields in (dict(provider_total=provider("total", 4), manual_total=99),
                   dict(manual_total=4)):
        got = decision(inventory=receipt, **fields)
        assert (got.status, got.remaining, got.eligibility) == (
            "unknown", None, "not_verified"
        )


@pytest.mark.parametrize("record", [
    provider("remaining", 2),
    replace(provider("remaining", 2), scope=replace(SCOPE, account_id="other")),
    replace(provider("remaining", 2), epoch=2),
    replace(provider("remaining", 2), observed_at=NOW - timedelta(days=2)),
    replace(provider("remaining", 2), observed_at=NOW + timedelta(seconds=1)),
    provider("total", 2),
])
def test_direct_remaining_requires_exact_kind_scope_epoch_and_freshness(record):
    got = decision(provider_remaining=record)
    if record.scope == SCOPE and record.epoch == 1 and record.kind == "remaining" and record.observed_at == NOW:
        assert (got.status, got.remaining) == ("provider_direct", 2)
    else:
        assert (got.status, got.remaining) == ("unknown", None)


def test_stale_or_cross_account_provider_total_falls_back_to_personal_only_when_complete():
    for total in (replace(provider("total", 3), epoch=2),
                  replace(provider("total", 3), scope=replace(SCOPE, account_id="other"))):
        assert decision(provider_total=total, manual_total=4,
                        inventory=inventory()).status == "manual_estimate"


def test_no_quota_anywhere_is_unknown_even_when_inventory_complete():
    assert decision(inventory=inventory()).remaining is None
    assert decision(inventory=inventory()).status == "unknown"


@pytest.mark.parametrize("n", [True, -1, 10001, "4", 1.0])
def test_bad_provider_quantities_are_rejected(n):
    with pytest.raises(ValueError, match="invalid_provider_capacity_evidence"):
        provider("remaining", n)


@pytest.mark.parametrize("n", [True, -1, 100, "4", 1.0])
def test_bad_manual_ceiling_is_rejected(n):
    with pytest.raises(ValueError, match="invalid_manual_loan_limit"):
        decision(manual_total=n)


def test_primary_borrower_empty_id_is_allowed_but_no_implicit_scope_alias():
    scope = replace(SCOPE, borrower_id="")
    assert decision(scope=scope, provider_remaining=provider("remaining", 2)).remaining is None
    assert scope.borrower_id == ""
    assert replace(SCOPE, account_id="").account_id == ""


def test_bad_scope_and_naive_timestamps_are_rejected():
    with pytest.raises(ValueError, match="invalid_digital_capacity_scope"):
        replace(SCOPE, identity_id="")
    with pytest.raises(ValueError, match="invalid_provider_capacity_evidence"):
        replace(provider("total", 4), observed_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="invalid_capacity_evaluation_context"):
        decision(now=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="invalid_capacity_evaluation_context"):
        decision(max_age=timedelta(0))


def test_boolean_or_wrong_types_do_not_coerce_completeness_or_epoch():
    with pytest.raises(ValueError, match="invalid_digital_inventory_receipt"):
        inventory(pagination_complete=1)
    with pytest.raises(ValueError, match="invalid_digital_inventory_receipt"):
        inventory(epoch=True)
    with pytest.raises(ValueError, match="invalid_capacity_evaluation_context"):
        decision(epoch=True)


@pytest.mark.parametrize("bad", ("line\\nfeed", "tab\\tvalue", "return\\rvalue", "delete\\x7fvalue", "null\\x00value"))
@pytest.mark.parametrize("field", ("identity_id", "provider_id", "credential_id", "account_id", "borrower_id"))
def test_capacity_scope_rejects_control_characters_in_every_selector(field, bad):
    with pytest.raises(ValueError, match="invalid_digital_capacity_scope"):
        replace(SCOPE, **{field: bad})


@pytest.mark.parametrize("bad", ("loan\\nsecond", "loan\\tsecond", "loan\\rsecond", "loan\\x7fsecond", "loan\\x00second"))
def test_inventory_receipt_rejects_control_characters_in_loan_ids(bad):
    with pytest.raises(ValueError, match="invalid_digital_inventory_receipt"):
        inventory(active_loan_ids=(bad,))
