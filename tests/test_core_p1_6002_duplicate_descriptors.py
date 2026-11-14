"""#6002: two installed declarations for one provider cannot choose a quota."""
from __future__ import annotations

import pytest

from cifereca.core.digital_loan_availability import digital_loan_availability
from cifereca.plugin_api import (
    ProviderCapabilities, ProviderDescriptor, digital_loan_availability_lines,
)
from tests.test_core_p1_6002_digital_loan_availability import (
    account, active_loan, configured, runtime, spec,
)


@pytest.mark.parametrize("reversed_order", [False, True])
def test_conflicting_provider_descriptors_fail_closed_only_for_that_provider(
    reversed_order,
):
    ambiguous = account("demo", "card", "reader")
    independent = account("healthy", "card", "exact-reader")
    lender = spec("demo")
    non_lender = ProviderDescriptor(
        id="demo", display_name="Competing descriptor", description="synthetic",
        state="ready", capabilities=ProviderCapabilities(loans=False),
    )
    declarations = [lender, non_lender]
    if reversed_order:
        declarations.reverse()

    state = runtime(
        [ambiguous, independent],
        [configured("demo", "card", options={"primary_digital": "99"}),
         configured("healthy", "card", options={"primary_digital": "4"})],
        [*declarations, spec("healthy")],
        [active_loan(ambiguous, "untrusted"), active_loan(independent, "healthy")],
    )
    rows = digital_loan_availability(state, "alice")
    assert len(rows) == 2
    by_provider = {row.provider_id: row for row in rows}
    bad = by_provider["demo"]
    assert bad.status == "unknown"
    assert bad.reason == "provider_unverified"
    assert bad.borrower_id is None
    assert bad.observed_loans is None
    assert bad.personal_ceiling is None
    assert bad.remaining is None
    assert bad.provider_label == "Fournisseur non qualifié"

    good = by_provider["healthy"]
    assert good.borrower_id == "exact-reader"
    assert good.reason == "inventory_unverified"
    assert good.observed_loans == 1
    assert good.personal_ceiling == 4
    assert good.remaining is None
    rendered = " ".join(digital_loan_availability_lines(rows, identity_id="alice"))
    assert "Competing descriptor" not in rendered
    assert "untrusted" not in rendered


def test_unique_non_lender_remains_absent_but_duplicate_is_unknown():
    profile = account("demo", "one", "reader")
    non_lender = ProviderDescriptor(
        id="demo", display_name="Public only", description="synthetic",
        state="ready", capabilities=ProviderCapabilities(loans=False),
    )
    state = runtime(
        [profile], [configured("demo", "one")], [non_lender],
    )
    assert digital_loan_availability(state, "alice") == ()
    state.provider_descriptors = lambda: [non_lender, spec("demo")]
    row, = digital_loan_availability(state, "alice")
    assert row.reason == "provider_unverified"
    assert row.borrower_id is None
    assert row.personal_ceiling is None
    assert row.remaining is None
