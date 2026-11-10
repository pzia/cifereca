"""Core generic attested-writer collision checks, independent of provider projectors."""
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
def test_attested_owned_writer_refuses_every_conflicting_owner_coordinate(
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
        assert service.storage.entitlement_put_attested_owned(offered) is False
        assert service.storage.entitlements() == before
        assert "PRIVATE REASSIGNMENT" not in json.dumps(before)
    finally:
        service.close()


def test_attested_owned_writer_refreshes_exact_owner_and_rejects_invalid_source(
    tmp_path,
):
    service = _service(tmp_path)
    try:
        right = {
            "entitlement_id": "PRIVATE-one-pk",
            "provider_id": "kobo", "credential_id": "vault",
            "account_id": None, "identity_id": "default",
            "item_id": "edition", "entitlement_type": "owned",
            "state": "active",
            "metadata": {"source": "kobo.personal_manifest", "title": "First"},
        }
        assert service.storage.entitlement_put_attested_owned(right) is True
        assert service.storage.entitlement_put_attested_owned(
            {**right, "state": "archived", "metadata": {
                "source": "kobo.personal_manifest", "title": "Revised",
            }},
        ) is True
        saved, = service.storage.entitlements(provider_id="kobo")
        assert saved["identity_id"] == "default"
        assert saved["state"] == "archived"
        assert saved["metadata"]["title"] == "Revised"
        for invalid in (
            {**right, "metadata": {"source": "numilog.authenticated_library"}},
            {**right, "entitlement_type": "loan"},
            {**right, "identity_id": ""},
        ):
            with pytest.raises(ValueError, match="^invalid_attested_owned_right$"):
                service.storage.entitlement_put_attested_owned(invalid)
        assert len(service.storage.entitlements(provider_id="kobo")) == 1
    finally:
        service.close()
