"""#4980: exact loan proofs at the Core projection boundary, not terrain."""
from dataclasses import replace
from itertools import permutations

import pytest

from cifereca.models import Loan
from cifereca.core.loan_cover_fallback import attested_local_loan_cover_proof
from cifereca.core.book_cover_source_projection import project_exact_loan_cover


@pytest.mark.parametrize("order", list(permutations(range(2))))
def test_exact_loan_conflicting_artifacts_refuse_catalogue_and_foreign_proof(order):
    loan = Loan(
        id="loan:1", provider="paris", medium="ebook", record_id="edition:1",
        title="Synthetic edition", identity_id="alice", source_provider="paris",
        source_credential="credential:1", source_account_id="account:1",
        thumbnail_url="https://catalog.example/cover.png",
    )
    entitlement = {
        "loan_id": loan.id, "credential_id": loan.source_credential,
        "account_id": loan.source_account_id,
    }
    rows = []
    for suffix in ("a", "b"):
        url = f"file:///tmp/fixture-{suffix}.png"
        row = {
            "local": True, "identity_id": loan.identity_id,
            "artifact_id": f"artifact:{suffix}", "placement_id": f"placement:{suffix}",
            "thumbnail_url": url,
            "metadata": {"cover_resolution": {
                "status": "selected", "tier": "embedded", "url": url,
            }},
        }
        proof = attested_local_loan_cover_proof(
            loan, entitlement, row, identity_id=loan.identity_id,
            provider_id=loan.source_provider, item_id=loan.record_id,
        )
        assert proof is not None
        row["_exact_loan_cover_proofs"] = (proof,)
        rows.append(row)

    def project(snapshot, revision):
        return project_exact_loan_cover(
            loan, tuple(snapshot), revision=revision, snapshot_complete=True,
        )

    first = project([rows[0]], 1)
    assert first.status == "selected"
    assert first.tier == "embedded"
    conflict = project([rows[i] for i in order], 2)
    assert conflict.status == "ambiguous"
    assert conflict.tier == "embedded"
    assert not conflict.uri
    assert conflict.candidate_count == 2

    foreign = replace(loan, source_account_id="account:foreign")
    assert attested_local_loan_cover_proof(
        foreign, entitlement, rows[1], identity_id=loan.identity_id,
        provider_id=loan.source_provider, item_id=loan.record_id,
    ) is None
    stale = dict(rows[1], placement_id="placement:rebound")
    restored = project([rows[0], stale], 3)
    assert restored.status == "selected"
    assert restored.tier == "embedded"
    assert restored.uri == first.uri
