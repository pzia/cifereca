"""P0 #2797/#2714: authenticated purchase mirrors must not steal rights.

Both personal-manifest read/index phases happen before SQLite writes. A
different identity may claim the canonical ID between the two. No network,
DRM, provider request, file placement or invented acquisition is permitted.
"""
from __future__ import annotations

from dataclasses import replace
import json

import pytest

from cifereca.acquisition_engine import Entitlement, SourceIdentity, stable_id
from cifereca.core.kobo_manifest_rights import mirror_kobo_manifest_rights
from cifereca.core.numilog_manifest_rights import mirror_numilog_manifest_rights
from tests.test_core_1337_kobo_personal_manifest_rights import _row as kobo_row
from tests.test_core_p0_1862_numilog_store_mirror import _connect, _row as numilog_row
from tests.test_sync_state_contract import _service


def _foreign(service, *, entitlement_id, provider, credential, account, item):
    service.storage.entitlement_put({
        "entitlement_id": entitlement_id,
        "provider_id": provider,
        "credential_id": credential,
        "account_id": account,
        "identity_id": "alex",
        "item_id": item,
        "entitlement_type": "owned",
        "state": "active",
        "metadata": {"title": "PRIVATE FOREIGN OWNER"},
    })


def test_numilog_canonical_claimed_after_reservations_uses_safe_fallback(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        account, _session, calls = _connect(service, number=0)
        purchase = numilog_row(account.credential_id, 0)
        source = SourceIdentity("numilog", account.credential_id,
                                account.unique_id, account.identity_id)
        canonical = Entitlement(source=source, item_id=purchase.record_id).entitlement_id
        key = (account.identity_id, account.credential_id,
               account.unique_id, purchase.record_id)
        fallback = stable_id("numilog-owned-identity", *key)
        original = service.storage.numilog_entitlement_reservations
        injected = [False]

        def race_after_reservation(*args, **kwargs):
            observed = original(*args, **kwargs)
            if not injected[0]:
                injected[0] = True
                _foreign(service, entitlement_id=canonical, provider="numilog",
                         credential=account.credential_id,
                         account=account.unique_id, item=purchase.record_id)
            return observed

        monkeypatch.setattr(
            service.storage, "numilog_entitlement_reservations",
            race_after_reservation,
        )
        count = mirror_numilog_manifest_rights(
            service, [purchase], identity_id=account.identity_id,
            credential_id=account.credential_id, account_id=account.unique_id,
        )
        assert count == 1
        rights = service.storage.entitlements(provider_id="numilog")
        assert len(rights) == 2
        foreign = next(r for r in rights if r["entitlement_id"] == canonical)
        own = next(r for r in rights if r["entitlement_id"] == fallback)
        assert foreign["identity_id"] == "alex"
        assert foreign["metadata"]["title"] == "PRIVATE FOREIGN OWNER"
        assert own["identity_id"] == "default"
        assert own["account_id"] == account.unique_id
        assert own["metadata"]["source"] == "numilog.authenticated_library"
        assert service.storage.acquisitions() == []
        assert service.storage.placements() == []
        assert calls == []
        # A subsequent refresh reuses the fallback, not the foreign PK.
        assert mirror_numilog_manifest_rights(
            service, [purchase], identity_id=account.identity_id,
            credential_id=account.credential_id, account_id=account.unique_id,
        ) == 1
        assert len(service.storage.entitlements(provider_id="numilog")) == 2
    finally:
        service.close()


def test_kobo_canonical_claimed_after_initial_read_uses_safe_fallback(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        row = kobo_row(product="owned-1", credential="vault-shared")
        source = SourceIdentity("kobo", "vault-shared", "account-shared", "default")
        canonical = Entitlement(source=source, item_id="owned-1").entitlement_id
        fallback = stable_id(
            "kobo-manifest-identity", "default", "vault-shared",
            "account-shared", "owned-1",
        )
        original = service.storage.entitlements
        injected = [False]

        def race_after_discovery(*args, **kwargs):
            observed = original(*args, **kwargs)
            if kwargs.get("provider_id") == "kobo" and not injected[0]:
                injected[0] = True
                _foreign(service, entitlement_id=canonical, provider="kobo",
                         credential="vault-shared", account="account-shared",
                         item="owned-1")
            return observed

        monkeypatch.setattr(service.storage, "entitlements", race_after_discovery)
        count = mirror_kobo_manifest_rights(
            service, [row], identity_id="default",
            credential_id="vault-shared", account_id="account-shared",
        )
        assert count == 1
        rights = original(provider_id="kobo")
        assert len(rights) == 2
        victim = next(r for r in rights if r["entitlement_id"] == canonical)
        own = next(r for r in rights if r["entitlement_id"] == fallback)
        assert victim["identity_id"] == "alex"
        assert victim["metadata"]["title"] == "PRIVATE FOREIGN OWNER"
        assert own["identity_id"] == "default"
        assert own["metadata"]["source"] == "kobo.personal_manifest"
        assert mirror_kobo_manifest_rights(
            service, [replace(row, metadata={"archived": True})],
            identity_id="default",
            credential_id="vault-shared", account_id="account-shared",
        ) == 1
        assert next(r for r in original(provider_id="kobo")
                    if r["entitlement_id"] == fallback)["state"] == "archived"
        assert len(original(provider_id="kobo")) == 2
        assert service.storage.acquisitions() == []
        assert service.storage.placements() == []
    finally:
        service.close()


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
