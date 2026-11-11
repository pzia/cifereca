"""#5048: claim-bound local ACSM receipt is not an arbitrary provider offer."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca import acsm_cache
from cifereca.models import Account
from tests.test_core_p0_3171_atomic_publication_v14 import new_source


@pytest.mark.parametrize("provider,branch", [
    ("fixture-lender-a", "digital"),
    ("fixture-lender-b", "loan"),
    ("paris", "dilicom"),
])
def test_no_private_receipt_must_not_promote_unknown_delivery(
    tmp_path, provider, branch,
):
    key, snapshot = new_source(
        "alice", provider=provider, credential="card-a",
        account="", branch=branch, edition="edition-a",
        right="loan-a", kind="loan",
    )
    account = Account(
        identity_id="alice", provider_id=provider,
        credential_id="card-a", unique_id="",
    )
    calls = []
    service = NS(
        _acquisition_work_dir=lambda *_args: tmp_path / "absent",
        storage=NS(loan_borrow_state=lambda _key: pytest.fail(
            "no claim lookup without an actual receipt"
        )),
    )
    session = NS(loans=lambda *_args: pytest.fail(
        "no provider inventory read without an existing receipt"
    ))
    right = replace(
        snapshot.rights[0], delivery="unknown", delivery_offer_id="",
    )
    assert acsm_cache.qualify_claimed_initial_receipt_offer(
        service, account, session, key, right,
        current=lambda: calls.append(True) or True,
    ) is None
    assert calls
    assert not list(tmp_path.iterdir())


def test_unknown_delivery_stays_unknown_if_source_lease_revoked(tmp_path):
    key, snapshot = new_source(
        "alice", provider="paris", credential="card-a",
        account="", branch="dilicom", edition="edition-a",
        right="loan-a", kind="loan",
    )
    account = Account(
        identity_id="alice", provider_id="paris",
        credential_id="card-a", unique_id="",
    )
    service = NS(_acquisition_work_dir=lambda *_a: pytest.fail(
        "revoked scope must not resolve a private directory"
    ))
    assert acsm_cache.qualify_claimed_initial_receipt_offer(
        service, account, NS(), key, snapshot.rights[0],
        current=lambda: False,
    ) is None
