"""P1 #4979: borrower-scoped, read-only local image fallback.

No GTK, provider I/O, entitlement inference or physical files are required.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from cifereca.core.loan_cover_fallback import (
    attested_local_loan_cover_proof, loan_local_cover,
)
from cifereca.core.loan_source_identity import loan_source_key
from cifereca.models import Loan


def loan(*, identity="alice", credential="card-a", account="borrower-a",
         record="edition-1", identifier="loan-1"):
    return Loan(
        id=identifier, provider="paris", medium="digital", record_id=record,
        title="Un même titre chez plusieurs emprunteurs",
        source_provider="bnf-pret", source_credential=credential,
        source_account_id=account, identity_id=identity,
    )


def local_row(current, url="file:///home/alice/.cache/cifereca/cover.png",
              *, tier="embedded", artifact="artifact-a", placement="placement-a"):
    return {
        "local": True, "identity_id": current.identity_id,
        "artifact_id": artifact, "placement_id": placement,
        "_exact_loan_cover_proofs": [{
            "loan_source_key": loan_source_key(current),
            "identity_id": current.identity_id,
            "source_provider": current.source_provider,
            "source_credential": current.source_credential,
            "source_account_id": current.source_account_id,
            "record_id": current.record_id,
            "loan_id": current.id,
            "artifact_id": artifact, "placement_id": placement,
            "url": url, "tier": tier,
        }],
    }


def test_exact_local_placement_supplies_missing_loan_cover_without_rights_mutation():
    current = loan()
    original = deepcopy(current)
    resolution = loan_local_cover(current, [local_row(current)])
    assert resolution.status == "selected"
    assert resolution.tier == "embedded"
    assert resolution.url.endswith("/cover.png")
    assert current == original
    assert not current.thumbnail_url


def test_missing_unknown_account_unlinked_and_nonlocal_are_not_cover_evidence():
    current = loan()
    assert loan_local_cover(current, []).status == "missing"
    unknown = loan(account=None)
    assert loan_local_cover(unknown, [local_row(current)]).status == "missing"
    not_local = local_row(current)
    not_local["local"] = False
    assert loan_local_cover(current, [not_local]).status == "missing"
    for field in ("placement_id", "artifact_id"):
        unplaced = local_row(current)
        unplaced[field] = None
        assert loan_local_cover(current, [unplaced]).status == "missing"


def test_exact_provider_credential_account_edition_loan_and_identity_required():
    current = loan()
    target = local_row(current)
    for changed in (
        loan(identity="bob"), loan(credential="card-b"),
        loan(account="borrower-b"), loan(record="edition-2"),
        loan(identifier="loan-2"),
    ):
        assert loan_local_cover(changed, [target]).status == "missing"
    for field, value in (
        ("identity_id", "bob"), ("source_provider", "paris"),
        ("source_credential", "card-b"),
        ("source_account_id", "borrower-b"),
        ("record_id", "edition-2"), ("loan_id", "loan-2"),
        ("placement_id", "different-placement"),
        ("artifact_id", "different-artifact"),
    ):
        forged = deepcopy(target)
        forged["_exact_loan_cover_proofs"][0][field] = value
        assert loan_local_cover(current, [forged]).status == "missing", field


def test_explicit_empty_paris_primary_account_is_valid_but_not_unknown():
    current = loan(account="")
    assert loan_local_cover(current, [local_row(current)]).status == "selected"
    assert loan_local_cover(loan(account=None), [local_row(current)]).status == "missing"
    assert loan_local_cover(loan(account="borrower-b"), [local_row(current)]).status == "missing"


def test_hierarchy_embedded_over_origin_over_catalog():
    current = loan()
    rows = [
        local_row(current, "https://catalog.example/cover.png", tier="catalog",
                  artifact="art-c", placement="place-c"),
        local_row(current, "https://origin.example/cover.png", tier="origin",
                  artifact="art-o", placement="place-o"),
        local_row(current, "file:///home/alice/.cache/cifereca/high.png", tier="embedded",
                  artifact="art-e", placement="place-e"),
    ]
    chosen = loan_local_cover(current, rows)
    assert chosen.status == "selected" and chosen.tier == "embedded"
    assert chosen.url.endswith("high.png")


def test_same_tier_two_local_artifacts_remain_ambiguous_without_first_wins():
    current = loan()
    rows = [
        local_row(current, "https://one.example/cover.png", tier="origin",
                  artifact="art-1", placement="place-1"),
        local_row(current, "https://two.example/cover.png", tier="origin",
                  artifact="art-2", placement="place-2"),
    ]
    assert loan_local_cover(current, rows).status == "ambiguous"
    assert not loan_local_cover(current, rows).url


def test_url_scheme_and_untrusted_proof_types_are_fail_closed():
    current = loan()
    for url in ("javascript:alert(1)", "/tmp/cover.png", "", None):
        assert loan_local_cover(current, [local_row(current, url)]).status == "missing"
    for proofs in ("secret", {}, [object()], [1] * 129):
        row = local_row(current)
        row["_exact_loan_cover_proofs"] = proofs
        assert loan_local_cover(current, [row]).status == "missing"


def projection_args(current):
    row = local_row(current)
    url = row["_exact_loan_cover_proofs"][0]["url"]
    row["thumbnail_url"] = url
    row["metadata"] = {
        "cover_resolution": {"status": "selected", "url": url, "tier": "embedded"}
    }
    entitlement = {
        "loan_id": current.id, "credential_id": current.source_credential,
        "account_id": current.source_account_id,
    }
    scope = {
        "identity_id": current.identity_id,
        "provider_id": current.source_provider,
        "item_id": current.record_id,
    }
    return row, entitlement, scope


def test_core_projection_creates_only_exact_local_loan_cover_proof():
    current = loan()
    row, entitlement, scope = projection_args(current)
    proof = attested_local_loan_cover_proof(
        current, entitlement, row, **scope
    )
    assert proof == row["_exact_loan_cover_proofs"][0]
    assert loan_local_cover(current, [{**row, "_exact_loan_cover_proofs": [proof]}]).url


def test_core_projection_rejects_foreign_profiles_credentials_and_accounts():
    current = loan()
    row, entitlement, scope = projection_args(current)
    for changed in (
        loan(identity="bob"), loan(credential="card-b"),
        loan(account="borrower-b"), loan(identifier="loan-2"),
        loan(record="edition-2"),
    ):
        assert attested_local_loan_cover_proof(changed, entitlement, row, **scope) is None
    for key, value in (
        ("loan_id", "different-loan"),
        ("account_id", "other-borrower"),
        ("credential_id", "other-credential"),
    ):
        other = dict(entitlement)
        other[key] = value
        assert attested_local_loan_cover_proof(current, other, row, **scope) is None


def test_core_projection_refuses_unplaced_unselected_and_conflicting_cover():
    current = loan()
    row, entitlement, scope = projection_args(current)
    for key, value in (
        ("local", False), ("identity_id", "bob"),
        ("placement_id", ""), ("artifact_id", ""),
        ("thumbnail_url", "https://different.example/cover.png"),
    ):
        changed = deepcopy(row)
        changed[key] = value
        assert attested_local_loan_cover_proof(current, entitlement, changed, **scope) is None
    for status, tier in (("ambiguous", "origin"), ("missing", "catalog"),
                         ("selected", "unverified")):
        changed = deepcopy(row)
        changed["metadata"]["cover_resolution"].update(status=status, tier=tier)
        assert attested_local_loan_cover_proof(current, entitlement, changed, **scope) is None
    assert attested_local_loan_cover_proof(current, entitlement, row, **{
        **scope, "provider_id": "other-provider",
    }) is None


def test_projection_attests_only_existing_local_placement_and_exact_borrower():
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/core/library_projection.py").read_text(encoding="utf-8")
    assert "attested_local_loan_cover_proof(" in source
    assert 'if entitlement_type == "loan" else None' in source
    assert '_exact_loan_cover_proofs' in source


def test_failing_lazy_library_projection_never_returns_partial_cover():
    current = loan()

    def partial_then_private_failure():
        yield local_row(current)
        raise RuntimeError("/private/library/reader-token")

    result = loan_local_cover(current, partial_then_private_failure())
    assert result.status == "missing"
    assert result.url == ""
    assert "reader-token" not in repr(result)


def test_oversized_local_library_fails_closed_without_consuming_all_rows(monkeypatch):
    from cifereca.core import loan_cover_fallback as mod

    current = loan()
    monkeypatch.setattr(mod, "_MAX_LOAN_COVER_ROWS", 2)
    inspected = []

    def rows():
        for index in range(20):
            inspected.append(index)
            yield local_row(current)

    result = mod.loan_local_cover(current, rows())
    assert result.status == "missing"
    assert result.url == ""
    # A single lookahead establishes an oversized sequence.
    assert inspected == [0, 1, 2]


def test_bounded_local_library_preserves_exact_cover_and_ambiguous_result(monkeypatch):
    from cifereca.core import loan_cover_fallback as mod

    current = loan()
    monkeypatch.setattr(mod, "_MAX_LOAN_COVER_ROWS", 2)
    a = local_row(current, "https://one.example/cover.jpg",
                  tier="origin", artifact="art-a", placement="place-a")
    b = local_row(current, "https://two.example/cover.jpg",
                  tier="origin", artifact="art-b", placement="place-b")
    assert mod.loan_local_cover(current, (a,)).status == "selected"
    assert mod.loan_local_cover(current, (a, b)).status == "ambiguous"
