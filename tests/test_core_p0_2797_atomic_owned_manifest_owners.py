"""Owner collision checks at the active entitlement writer."""
from __future__ import annotations

import json
import pytest

from tests.test_sync_state_contract import _service


@pytest.mark.parametrize("mutation", [
    {"identity_id": "alex"},
    {"credential_id": "other"},
    {"account_id": "other"},
    {"item_id": "other-edition"},
    {"provider_id": "kobo"},
    {"entitlement_type": "loan"},
    {"loan_id": "someone-else-loan"},
])
def test_entitlement_writer_refuses_every_conflicting_owner_coordinate(
    tmp_path, mutation,
):
    service = _service(tmp_path)
    try:
        original = {
            "entitlement_id": "PRIVATE-shared-pk",
            "provider_id": "numilog", "credential_id": "credential",
            "account_id": "account", "identity_id": "default",
            "item_id": "edition", "entitlement_type": "owned",
            "state": "active", "metadata": {
                "title": "PRIVATE HISTORY",
                "source": "numilog.authenticated_library",
            },
        }
        service.storage.entitlement_put({**original, **mutation})
        before = service.storage.entitlements()
        offered = {**original, "metadata": {
            "title": "PRIVATE REASSIGNMENT",
            "source": "numilog.authenticated_library",
        }}
        with pytest.raises(ValueError, match="entitlement_source_scope_conflict"):
            service.storage.entitlement_put(offered)
        assert service.storage.entitlements() == before
        assert "PRIVATE REASSIGNMENT" not in json.dumps(before)
    finally:
        service.close()


