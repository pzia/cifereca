"""#2703: seven families without a fabricated provider-wide ability.

Only synthetic CapabilityEvidence and pure local grouping. Never implies a
license, subscription, full inventory, valid connection or mutating consent.
"""
from dataclasses import replace

import pytest

from cifereca.plugin_api import (
    CAPABILITY_FAMILIES, CapabilityEvidence, CapabilityMatrix,
    exact_capability_families,
)


def row(operation, *, scope="account", identity="alice", account="reader-a",
        credential="card-a", field="", status="unverified"):
    proof = status == "supported"
    return CapabilityEvidence(
        provider_id="synthetic", operation=operation, scope=scope,
        identity_id=identity, account_id=account, credential_id=credential,
        field=field, status=status,
        implementation_state="implemented",
        source_kind="account_observation" if proof else "code",
        evidence_ref="fixture:scope-proof" if proof else "",
        reason="only_first_page" if status == "partial" else "",
    )


def grouped(matrix, **kwargs):
    args = dict(scope="account", identity_id="alice",
                account_id="reader-a", credential_id="card-a")
    args.update(kwargs)
    return {x.family: x for x in exact_capability_families(matrix, **args)}


def test_seven_families_never_promote_one_operation_into_a_whole_family():
    matrix = CapabilityMatrix("synthetic", (
        row("loan.inventory", status="supported"),
        row("loan.borrow"),
        row("loan.renew", status="unsupported"),
        row("personal_inventory.rights", status="partial"),
        row("acquisition.download", status="unsupported"),
        row("metadata.enrichment", status="partial"),
        row("drm.adept", status="unsupported"),
        row("cache.refresh"),
        row("catalog.search", scope="public", account="", credential=""),
    ))
    result = exact_capability_families(
        matrix, scope="account", identity_id="alice",
        account_id="reader-a", credential_id="card-a",
    )
    assert tuple(x.family for x in result) == CAPABILITY_FAMILIES
    by_family = {x.family: x for x in result}
    assert by_family["catalogue"].missing is True
    assert by_family["metadata"].partial_count == 1
    assert by_family["accounts_rights"].partial_count == 1
    assert by_family["loans"].supported_count == 1
    assert by_family["loans"].unverified_count == 1
    assert by_family["loans"].unsupported_count == 1
    assert by_family["acquisition"].unsupported_count == 1
    assert by_family["drm"].unsupported_count == 1
    assert by_family["crosscutting"].unverified_count == 1
    assert all(not item.authorizes_mutation for item in result)


def test_account_credential_field_edition_and_unknown_scope_do_not_widen():
    matrix = CapabilityMatrix("synthetic", (
        row("loan.inventory", status="supported"),
        row("loan.inventory.branch", scope="digital", field="dilicom",
            status="supported"),
        row("catalog.search", scope="unknown", identity="", account="", credential=""),
        row("loan.borrow", account="reader-b", credential="card-b"),
        row("acquisition.download", identity="bob"),
    ))
    exact = grouped(matrix)
    assert [x.operation for x in exact["loans"].evidence] == ["loan.inventory"]
    assert all(x.missing for f, x in exact.items() if f != "loans")
    assert all(x.missing for x in grouped(matrix, credential_id="card-b").values())
    assert all(x.missing for x in grouped(matrix, account_id="reader-b").values())
    assert all(x.missing for x in grouped(matrix, identity_id="bob").values())
    assert all(x.missing for x in grouped(matrix, field="dilicom").values())
    branch = grouped(matrix, scope="digital", field="dilicom")
    assert [x.operation for x in branch["loans"].evidence] == ["loan.inventory.branch"]
    code = grouped(matrix, scope="unknown", identity_id="", account_id="", credential_id="")
    assert code["catalogue"].unverified_count == 1
    assert code["loans"].missing


def test_corrupt_cached_matrix_fails_closed_and_never_promotes_code_to_live():
    fact = row("loan.borrow")
    matrix = CapabilityMatrix("synthetic", (fact,))
    object.__setattr__(fact, "status", "supported")
    with pytest.raises(ValueError, match="capability_unverified_support"):
        grouped(matrix)


@pytest.mark.parametrize("kwargs", (
    {"scope": None}, {"account_id": ["borrower"]},
    {"credential_id": "bad\x00id"}, {"field": "bad\nbranch"},
))
def test_invalid_selector_cannot_become_a_wildcard(kwargs):
    matrix = CapabilityMatrix("synthetic", ())
    with pytest.raises(ValueError, match="capability_invalid_scope"):
        grouped(matrix, **kwargs)


def test_no_observation_is_a_gap_not_a_provider_refusal():
    empty = grouped(CapabilityMatrix("synthetic", ()))
    assert len(empty) == 7
    assert all(x.missing and x.evidence == () for x in empty.values())
    assert all(x.unsupported_count == 0 and x.supported_count == 0
               for x in empty.values())
