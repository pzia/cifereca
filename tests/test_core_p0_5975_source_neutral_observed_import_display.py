"""#5975: generic, inert manual-import display linkage for exact loan sources.

No provider calls or new possessions. The legacy BnF key remains readable;
future source names use the same frozen writer grammar, never a Core switch.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cifereca.core.observed_import_provenance import (
    observed_import_links_for_sources, observed_loan_provenance_field,
)

SCOPE = {
    "identity_id": "alice", "credential_id": "credential-a",
    "account_id": "borrower-a", "loan_id": "loan-a",
    "item_id": "edition-a",
}
EXACT = ("alice", "credential-a", "borrower-a", "loan-a", "edition-a")


def _case(provider, *, path, placement, artifact, observed=None):
    field = observed_loan_provenance_field(provider)
    return ({
        "provider_id": "manual", "identity_id": "alice",
        "strategy": "manual_acsm", "status": "complete",
        "entitlement_id": None, "artifact_id": artifact,
        "metadata": {
            "path": path, field: dict(SCOPE if observed is None else observed),
        },
    }, {
        "path": path, "placement_id": placement,
        "identity_id": "alice", "provider_id": provider,
        "artifact_id": artifact,
    })


def _project(rows, placements, *, providers):
    db = SimpleNamespace(acquisitions=lambda **kw: (
        rows if kw == {"provider_id": "manual", "status": "complete"} else []
    ))
    keys = {(p["placement_id"], p["artifact_id"]):
            "local-" + p["placement_id"] for p in placements}
    return observed_import_links_for_sources(
        db, placements, keys, providers=providers,
    )


def test_legacy_bnf_and_future_provider_are_both_displayed_without_core_dispatch():
    a, pa = _case("bnf-pret", path="/library/a.epub",
                  placement="a", artifact="sha256:a")
    b, pb = _case("future-lender", path="/library/b.epub",
                  placement="b", artifact="sha256:b")
    assert _project([a, b], [pa, pb],
                    providers=["future-lender", "bnf-pret"]) == {
        ("bnf-pret", *EXACT): "local-a",
        ("future-lender", *EXACT): "local-b",
    }


def test_two_providers_with_same_field_prefix_do_not_exchange_local_placements():
    a, pa = _case("future-lender", path="/library/a.pdf",
                  placement="a", artifact="sha256:a")
    b, pb = _case("future-library", path="/library/b.pdf",
                  placement="b", artifact="sha256:b")
    assert a["metadata"].keys() == b["metadata"].keys()
    assert _project([a, b], [pa, pb],
                    providers=["future-library", "future-lender"]) == {
        ("future-lender", *EXACT): "local-a",
        ("future-library", *EXACT): "local-b",
    }
    assert _project([a, b], [pa, pb],
                    providers=["future-lender"]) == {
        ("future-lender", *EXACT): "local-a",
    }


@pytest.mark.parametrize("tamper", ["borrower", "identity", "artifact", "provider",
                                    "duplicate", "ambiguous"])
def test_historical_loan_display_refuses_foreign_or_ambiguous_provenance(tamper):
    row, placement = _case("bnf-pret", path="/library/book.pdf",
                           placement="a", artifact="sha256:a")
    placements = [placement]
    if tamper == "borrower":
        row["metadata"]["bnf_observed_loan"]["account_id"] = "stranger"
    if tamper == "identity":
        row["identity_id"] = "bob"
    if tamper == "artifact":
        row["artifact_id"] = "sha256:foreign"
    if tamper == "provider":
        placement["provider_id"] = "future-lender"
    if tamper == "duplicate":
        placements = [placement, dict(placement, placement_id="b")]
    if tamper == "ambiguous":
        second, p2 = _case("bnf-pret", path="/library/other.pdf",
                           placement="b", artifact="sha256:b")
        rows = [row, second]
        placements = [placement, p2]
    else:
        rows = [row]
    observed = _project(rows, placements, providers=["bnf-pret"])
    if tamper in {"borrower", "identity", "artifact", "provider", "duplicate"}:
        # Another borrower's hint cannot attach this exact remote right.
        assert ("bnf-pret", *EXACT) not in observed
    else:
        assert observed == {}


@pytest.mark.parametrize("source", [None, True, "PARIS",
                                    "lib.example", "", " malicious"])
def test_unproven_legacy_key_grammar_never_creates_display_join(source):
    assert observed_loan_provenance_field(source) is None


def test_unbounded_or_malformed_provider_inventory_denies_all_display_hints():
    row, placement = _case("bnf-pret", path="/library/a.epub",
                           placement="a", artifact="sha256:a")
    providers = ["bnf-pret"] + ["provider-" + str(n) for n in range(65)]
    assert _project([row], [placement], providers=providers) == {}
    assert _project([row], [placement], providers=None) == {}


def test_writer_field_grammar_matches_legacy_bnf_and_future_source():
    assert observed_loan_provenance_field("bnf-pret") == "bnf_observed_loan"
    assert observed_loan_provenance_field("future-lender") == "future_observed_loan"
    assert observed_loan_provenance_field("future-library") == "future_observed_loan"
