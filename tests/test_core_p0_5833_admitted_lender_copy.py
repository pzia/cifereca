"""#5833: confirmed lender copy stays pinned to the exact source and placement."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core.borrow_for_identity_copy_admission import (
    capture_lender_copy, execute_lender_copy,
)
from cifereca.core.borrow_for_identity_placement_resolution import LenderLocalPlacement
from cifereca.core import borrow_for_identity_placement_resolution as placement
from cifereca.core import borrow_for_identity_transfer_preflight as preflight
from tests.test_core_p1_4662_cross_profile_borrow_preview import witness


@pytest.fixture
def admitted():
    w = witness()
    account, notice = w.account, w.notice
    identities = {"bob": NS(identity_id="bob"),
                  account.identity_id: NS(identity_id=account.identity_id)}
    rows = [{
        "identity_id": account.identity_id, "provider_id": account.provider_id,
        "credential_id": account.credential_id, "enabled": True,
    }]
    record = {
        "identity_id": account.identity_id, "provider_id": account.provider_id,
        "source_item_id": notice.record_id, "state": "present",
        "placement_id": "P", "artifact_id": "A",
    }
    storage = NS(
        identity_for_connection=Mock(return_value=account.identity_id),
        provider_connections=Mock(return_value=rows),
        metadata_get=Mock(return_value="claim"),
        loan_borrow_state=Mock(return_value={
            "status": "confirmed", "request_accepted": True,
            "verification": "positive_active_loan", "verified_loan_id": "L",
        }),
        placement_get=Mock(return_value=record),
    )
    class Host:
        _account_context_epoch = 7
        _source_context_epoch = 11

        @property
        def current_identity(self):
            pytest.fail("post-consent Core must not read navigation")

        def identity_by_id(self, identity):
            return identities.get(identity)

    service = Host()
    service.accounts = [account]
    service.storage = storage
    service.materialize_to_identity = Mock(return_value={"status": "complete"})
    lease = capture_lender_copy(
        service, requesting_identity_id="bob", borrowing_account=account,
        exact_notice=notice, lender_placement_id="P",
    )
    assert lease is not None
    return service, lease, record, identities, notice


def test_admitted_copy_survives_navigation_even_during_resolution(admitted, monkeypatch):
    host, lease, record, _, _ = admitted
    def locate(*_args, **_kwargs):
        host._account_context_epoch += 2
        return record
    monkeypatch.setattr(placement, "local_placement_for_source", locate)
    host._account_context_epoch += 1
    result = execute_lender_copy(host, lease)
    assert result["status"] == "complete", result
    host.materialize_to_identity.assert_called_once_with("P", "bob")


@pytest.mark.parametrize("change", ["source", "requester", "notice", "placement", "owner"])
def test_real_source_or_placement_change_blocks_copy(admitted, monkeypatch, change):
    host, lease, record, identities, notice = admitted
    if change == "source":
        host._source_context_epoch += 1
    elif change == "requester":
        identities["bob"] = NS(identity_id="bob")
    elif change == "notice":
        notice.record_id = "other"
    elif change == "placement":
        record = {**record, "placement_id": "replacement"}
    else:
        host.storage.provider_connections.return_value[0]["enabled"] = False
    monkeypatch.setattr(placement, "local_placement_for_source",
                        lambda *_a, **_kw: record)
    assert execute_lender_copy(host, lease)["status"] == "blocked"
    host.materialize_to_identity.assert_not_called()
