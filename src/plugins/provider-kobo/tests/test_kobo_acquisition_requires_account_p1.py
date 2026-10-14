"""#1346: direct Kobo descriptor/acquisition calls require a bound account.

Real KoboVaultClient with MockTransport only. No network, DRM or local writes.
"""
from __future__ import annotations

import json

import httpx
import pytest

from cifereca.plugin_api import Account, ProviderError
from cifereca_kobo_vault import KoboVaultClient
from tests.test_kobo_manifest_policy_p1 import session_with_vault


def _session():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        if request.method == "POST" and request.url.path == "/api/v2/library/book-1/acquisition":
            return httpx.Response(200, json={
                "acquisition": {"candidates": [], "status": "no_download"},
            })
        pytest.fail("unexpected Kobo endpoint")

    vault = KoboVaultClient(
        "https://vault.example", "PRIVATE_ACCESS_TOKEN",
        transport=httpx.MockTransport(handler),
    )
    return session_with_vault(vault), requests


@pytest.mark.parametrize("binding", [
    "disconnected", "no_accounts", "multiple_accounts", "wrong_provider",
    "wrong_credential", "invalid_id", "missing_id", "false_id",
])
@pytest.mark.parametrize("mode", ["descriptor", "delegated"])
def test_direct_acquisition_rejects_unbound_session_without_http(binding, mode):
    session, requests = _session()
    try:
        original = session._accounts[0]
        if binding == "disconnected":
            session.connected = False
        elif binding == "no_accounts":
            session._accounts = []
        elif binding == "multiple_accounts":
            session._accounts = [original, original]
        else:
            account = {
                "provider_id": session.provider_id,
                "credential_id": session.credential_id,
                "unique_id": "alice",
            }
            if binding == "wrong_provider":
                account["provider_id"] = "PRIVATE_FOREIGN_PROVIDER"
            elif binding == "wrong_credential":
                account["credential_id"] = "PRIVATE_FOREIGN_CREDENTIAL"
            elif binding == "invalid_id":
                account["unique_id"] = "alice\nPRIVATE_TOKEN"
            elif binding == "missing_id":
                account["unique_id"] = None
            elif binding == "false_id":
                account["unique_id"] = False
            session._accounts = [Account(display_name="Synthetic", **account)]

        for attempt in range(2):
            with pytest.raises(ProviderError, match="sans compte attesté") as error:
                session.resolve_acquisition(
                    "book-1", revision_id="PRIVATE_REVISION",
                    mode=mode, include_acsm=True,
                )
            for secret in ("PRIVATE_", "alice", "book-1", "Bearer"):
                assert secret not in str(error.value)
        assert requests == []
        assert session.backup_manifest_rejection_count == 0
    finally:
        session.close()


def test_attested_account_keeps_direct_descriptor_and_delegated_requests():
    session, requests = _session()
    try:
        for mode in ("descriptor", "delegated"):
            result = session.resolve_acquisition(
                "book-1", revision_id="revision-1",
                mode=mode, include_acsm=True,
            )
            assert result == {
                "acquisition": {"candidates": [], "status": "no_download"},
            }
        assert len(requests) == 2
        assert all(req.url.path == "/api/v2/library/book-1/acquisition" for req in requests)
        assert [json.loads(req.content)["mode"] for req in requests] == [
            "descriptor", "delegated",
        ]
        assert all(json.loads(req.content)["revision_id"] == "revision-1"
                   for req in requests)
    finally:
        session.close()


def test_acquisition_scope_can_be_restored_after_new_verified_account_binding():
    session, requests = _session()
    try:
        session.connected = False
        with pytest.raises(ProviderError, match="sans compte attesté"):
            session.resolve_acquisition("book-1")
        assert not requests
        session.connected = True
        session._accounts = [Account(
            unique_id="alice", display_name="Synthetic",
            provider_id=session.provider_id, credential_id=session.credential_id,
        )]
        assert session.resolve_acquisition("book-1")["acquisition"]["candidates"] == []
        assert len(requests) == 1
        session._accounts[0] = Account(
            unique_id="alice", display_name="Synthetic",
            provider_id=session.provider_id, credential_id="PRIVATE_CHANGED",
        )
        with pytest.raises(ProviderError, match="sans compte attesté"):
            session.resolve_acquisition("book-1")
        assert len(requests) == 1
    finally:
        session.close()
