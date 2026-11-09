"""#5055: Core claim reader accepts an exact lender scope without a BnF decision."""
from dataclasses import replace

from cifereca.core.borrow_claim_observation import observe_borrow_claim
from tests.test_p1_4082_bnf_borrow_claim_observation import _account, _item


def test_unknown_lender_cannot_read_another_lenders_claim():
    account = replace(_account(), provider_id="future-lender")
    item = replace(_item(account), source_provider="future-lender",
                   docbase="FUTURE", source_credential=account.credential_id)
    calls = []

    class FakeStorage:
        def metadata_get(self, key):
            calls.append(key)
            return None

    result = observe_borrow_claim(
        storage=FakeStorage(), account=account, item=item,
        expected_identity_id=account.identity_id,
        provider_id="future-lender", docbase="FUTURE",
    )
    assert result.state == "no_checkpoint"
    assert len(calls) == 1
    calls.clear()
    wrong = observe_borrow_claim(
        storage=FakeStorage(), account=account, item=item,
        expected_identity_id=account.identity_id,
        provider_id="bnf-pret", docbase="BNF_PRET",
    )
    assert wrong.state == "scope_unverified"
    assert calls == []


def test_no_private_lookup_for_missing_exact_scope():
    class NoStorage:
        pass

    account = _account()
    item = replace(_item(account), source_credential="another-card")
    result = observe_borrow_claim(
        storage=NoStorage(), account=account, item=item,
        expected_identity_id=account.identity_id,
        provider_id="bnf-pret", docbase="BNF_PRET",
    )
    assert result.state == "scope_unverified"
