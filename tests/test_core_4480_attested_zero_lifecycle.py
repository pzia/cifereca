"""#4480: synthetic Core lifecycle, NOT qualification of a BnF empty page.

The test deliberately supplies the exact hypothetical complete-scope input
to the existing Core business transition. The real provider must still
qualify that input; partial/unknown observations never supply it here.
"""
from dataclasses import replace
from types import SimpleNamespace

from cifereca.core.canonical_publication import publish_completed_acquisition
from cifereca.core.library_mutation import _persist_active_loans
from cifereca.models import Loan
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import (
    SHA, new_source, register, target,
)


def test_hypothetical_attested_zero_realizes_only_exact_right_then_reobserves(tmp_path):
    db = Storage(tmp_path / "db.sqlite")
    try:
        register(db, "A", "bnf-pret", "bnf-reader", account="bnf-reader")
        source, positive = new_source(
            "A", provider="bnf-pret", credential="bnf-reader",
            account="bnf-reader", branch="bnf_html",
            edition="edition-0", right="loan-0", kind="loan",
        )
        path = target(tmp_path, "A", "loan-0.pdf")
        publish_completed_acquisition(
            db, library_root=tmp_path / "EBooks", path=path, source=source,
            snapshot=positive, final_sha256=SHA,
            delivery_receipt_ref="synthetic-bnf-delivery",
            adept_receipt_ref="synthetic-bnf-adept",
        )
        service = SimpleNamespace(storage=db)
        loan = Loan(
            id="loan-0", provider="BNF_PRET", medium="digital",
            record_id="edition-0", title="Synthetic",
            source_provider="bnf-pret", source_credential="bnf-reader",
            source_account_id="bnf-reader", identity_id="A",
            due_at="2099-01-01T00:00:00Z",
        )

        def persist(loans, scopes=None):
            return _persist_active_loans(
                service, "A", loans, reconcile_missing=True,
                complete_scopes=scopes, realize_expired=False,
            )

        def right():
            return next(row for row in db.entitlements()
                        if row["loan_id"] == "loan-0")

        def publication():
            with db._lock, db._connect() as conn:
                provenance = tuple(
                    tuple(sorted(map(tuple, conn.execute("SELECT * FROM " + table)),
                                 key=repr))
                    for table in ("artifacts", "placements", "source_references",
                                  "origin_links")
                )
            return provenance, db.acquisitions(), path.read_bytes()

        foreign = replace(loan, id="loan-foreign", record_id="edition-foreign",
                          source_account_id="other")
        persist([loan, foreign])
        entitlement_id = right()["entitlement_id"]
        db.acquisition_put({
            "acquisition_id": "synthetic-bnf-acquisition",
            "entitlement_id": entitlement_id,
            "provider_id": "bnf-pret", "credential_id": "bnf-reader",
            "account_id": "bnf-reader", "item_id": "edition-0",
            "status": "complete", "artifact_id": "sha256:" + SHA,
            "identity_id": "A",
        })
        before = publication()
        assert len(before[1]) == 1
        assert tuple(map(len, before[0])) == (1, 1, 1, 2)
        assert right()["state"] == "active"

        # No supplier proof, or an exact complete scope of another account:
        # neither may turn this loan into a negative observation.
        assert persist([])["returned_realized"] == 0
        assert persist([], {("bnf-pret", "bnf-reader", "unknown")})["returned_realized"] == 0
        assert right()["state"] == "active"
        assert publication() == before

        # Hypothetical authoritative input only, not an actual BnF response.
        scope = {("bnf-pret", "bnf-reader", "bnf-reader")}
        assert persist([], scope)["returned_realized"] == 1
        assert right()["state"] == "realized"
        assert persist([], scope)["returned_realized"] == 0
        assert publication() == before

        persist([loan], scope)
        assert right()["state"] == "active"
        assert next(row for row in db.entitlements()
                    if row["loan_id"] == foreign.id)["state"] == "active"
        assert right()["entitlement_id"] == entitlement_id
        assert publication() == before
    finally:
        db.close()
