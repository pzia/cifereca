"""#3121 — first Paris ACSM is a Core candidate, never another GET.

Synthetic private receipts and claims; no provider HTTP or ADEPT calls.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca import acsm_cache
from cifereca.core.paris_initial_canonical_candidate import stage_first_paris_acsm
from cifereca.models import Account
from tests.test_core_p0_3121_initial_acsm_receipt import first_offer
from tests.test_core_p0_3171_atomic_publication_v14 import new_source


@pytest.fixture
def setup(first_offer, tmp_path):
    directory, path, loan = first_offer
    key = "loan.borrow.v1:" + "f" * 64
    op = "synthetic-confirmed"
    receipt = dict(
        schema=1, operation="loan.borrow", status="confirmed",
        operation_id=op, request_accepted=True,
        verification="positive_active_loan", verified_loan_id=loan.id,
    )
    assert acsm_cache.remember_initial(
        path, directory, loan, "identity-A", "primary",
        borrow_claim=(key, op),
    )
    claim = {key: receipt}
    storage = SimpleNamespace(loan_borrow_state=lambda k: claim.get(k))
    service = SimpleNamespace(
        storage=storage,
        _acquisition_work_dir=lambda *a: directory,
    )
    source, _ = new_source(
        "identity-A", provider="paris", credential="credential-A",
        account="", branch="dilicom", edition="edition-1",
        right="right-1", kind="loan",
    )
    account = Account(
        identity_id="identity-A", provider_id="paris",
        credential_id="credential-A", unique_id="",
    )
    session = SimpleNamespace(loans=lambda _: [replace(loan, ebook_url=None)])
    attempt = tmp_path / "canonical" / "run-exact"
    attempt.mkdir(parents=True)
    return service, session, account, source, attempt, path, claim, loan


def stage(case, *, current=lambda: True):
    service, session, account, source, attempt, *_ = case
    return stage_first_paris_acsm(
        service, session, account, source, attempt=attempt,
        admission_current=current)


def test_first_claim_bound_token_copied_without_provider_candidate_get(setup):
    service, session, account, source, attempt, path, claim, loan = setup
    calls = []
    original_loans = session.loans
    def inventory(account):
        calls.append("inventory")
        return original_loans(account)
    session.loans = inventory
    result = stage(setup)
    assert calls == ["inventory"]
    assert result == attempt / "first-offer.acsm"
    assert result.read_bytes() == path.read_bytes()
    assert result.stat().st_ino != path.stat().st_ino
    assert acsm_cache.INITIAL_MANIFEST not in result.name


@pytest.mark.parametrize("mutate", [
    "foreign_loan", "other_account", "claim_not_confirmed",
    "sidecar_symlink", "token_symlink", "token_modified", "epoch",
])
def test_first_offer_fail_closed(setup, mutate, tmp_path):
    service, session, account, source, attempt, path, claim, loan = setup
    if mutate == "foreign_loan":
        session.loans = lambda _: [replace(loan, record_id="other")]
    elif mutate == "other_account":
        other = replace(account, unique_id="another")
        case = (service, session, other, source, attempt, path, claim, loan)
        with pytest.raises(ValueError, match="canonical_paris_initial_scope_invalid"):
            stage(case)
        return
    elif mutate == "claim_not_confirmed":
        claim.clear()
    elif mutate == "sidecar_symlink":
        outside = tmp_path / "outside.json"
        sidecar = path.parent / acsm_cache.INITIAL_MANIFEST
        outside.write_bytes(sidecar.read_bytes())
        sidecar.unlink()
        sidecar.symlink_to(outside)
    elif mutate == "token_symlink":
        outside = tmp_path / "outside.acsm"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    elif mutate == "token_modified":
        path.write_bytes(b"not-the-initial-token")
    elif mutate == "epoch":
        with pytest.raises(ValueError, match="canonical_paris_initial_admission_revoked"):
            stage(setup, current=lambda: False)
        return
    with pytest.raises(ValueError, match="canonical_paris_initial_"):
        stage(setup)
    assert list(attempt.iterdir()) == []


def test_no_receipt_preserves_ordinary_existing_loan_delivery(setup):
    service, session, account, source, attempt, path, claim, loan = setup
    (path.parent / acsm_cache.INITIAL_MANIFEST).unlink()
    session.loans = lambda _: pytest.fail("no inventory read without a receipt")
    assert stage(setup) is None

def test_other_paris_borrower_keeps_existing_selected_right_path(setup):
    service, session, account, source, attempt, path, claim, loan = setup
    other = replace(account, unique_id="reader-B")
    alternate, _ = new_source(
        "identity-A", provider="paris", credential="credential-A",
        account="reader-B", branch="dilicom", edition="edition-1",
        right="right-1", kind="loan",
    )
    session.loans = lambda _: pytest.fail(
        "no borrow inventory for non-primary first-offer receipt"
    )
    assert stage_first_paris_acsm(
        service, session, other, alternate, attempt=attempt,
        admission_current=lambda: True,
    ) is None
    assert list(attempt.iterdir()) == []
