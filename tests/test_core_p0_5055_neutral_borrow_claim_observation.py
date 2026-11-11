"""#5055: synthetic lender contract; Core tests never import BnF fixtures."""
from dataclasses import replace

import pytest

from cifereca.core.borrow_claim_observation import observe_borrow_claim
from cifereca.models import Account, SearchResult


def _account(provider="synthetic-lender"):
    return Account(
        identity_id="alice", provider_id=provider,
        credential_id="synthetic-credential", unique_id="synthetic-user",
    )


def _item(account, docbase="SYNTHETIC"):
    return SearchResult(
        id="synthetic:edition", record_id="edition-a", docbase=docbase,
        medium="digital", title="Synthetic book", author=None,
        type="Livre numérique", format=None, publisher=None,
        published_at=None, description=None, thumbnail_url=None,
        friendly_url=None, scenario=docbase, source_provider=account.provider_id,
        source_credential=account.credential_id,
    )


class RecordingStorage:
    def __init__(self, *, fails=False):
        self.calls = []
        self.fails = fails

    def metadata_get(self, key):
        self.calls.append(key)
        if self.fails:
            raise OSError("simulated metadata failure")
        return None


def _observe(storage, account, item, *, identity=None,
             provider="synthetic-lender", docbase="SYNTHETIC"):
    return observe_borrow_claim(
        storage=storage, account=account, item=item,
        expected_identity_id=identity or account.identity_id,
        provider_id=provider, docbase=docbase,
    )


def test_exact_synthetic_scope_is_read_only_and_not_borrow_permission():
    account = _account()
    storage = RecordingStorage()
    observed = _observe(storage, account, _item(account))
    assert observed.state == "no_checkpoint"
    assert observed.claim_consumed is False
    assert observed.provider_verified is False
    assert observed.retry_authorized is False
    assert len(storage.calls) == 1
    assert "synthetic-credential" not in repr(observed)


@pytest.mark.parametrize("mismatch", [
    "provider", "docbase", "identity", "credential", "medium",
    "item_provider", "item_docbase",
])
def test_scope_mismatch_never_reads_private_checkpoint(mismatch):
    account = _account()
    item = _item(account)
    kwargs = {}
    if mismatch == "provider":
        kwargs["provider"] = "other-lender"
    elif mismatch == "docbase":
        kwargs["docbase"] = "OTHER"
    elif mismatch == "identity":
        kwargs["identity"] = "bob"
    elif mismatch == "credential":
        item = replace(item, source_credential="other-credential")
    elif mismatch == "medium":
        item = replace(item, medium="physical")
    elif mismatch == "item_provider":
        item = replace(item, source_provider="other-lender")
    else:
        item = replace(item, docbase="OTHER")
    storage = RecordingStorage()
    observed = _observe(storage, account, item, **kwargs)
    assert observed.state == "scope_unverified"
    assert observed.retry_authorized is False
    assert storage.calls == []


def test_unavailable_checkpoint_read_is_not_permission_to_retry():
    account = _account()
    storage = RecordingStorage(fails=True)
    observed = _observe(storage, account, _item(account))
    assert observed.state == "read_unavailable"
    assert observed.claim_consumed is None
    assert observed.provider_verified is False
    assert observed.retry_authorized is False
    assert len(storage.calls) == 1
