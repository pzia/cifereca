"""#4858 D1: canonical CAS must not resurrect 0.7 presence when ABI drops.

Real Storage/LibraryService and synthetic Kobo transport. The old 0.7
archive is deliberately present but must never act as local B/rights after
a source-wide canonical policy was configured (ON, OFF or corrupted).
"""
from __future__ import annotations

from dataclasses import replace
import json

import pytest

from cifereca.core import rights_snapshot_read
from cifereca.core.source_sync_consent import policy_key, scope_for_account
from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_guard import _service
from tests.test_source_materialization_confirmation import case  # noqa: F401
from tests.test_core_p0_4858_d1_canonical_preview import typed  # noqa: F401


def _policy(service, account, mode):
    scope = scope_for_account(account)
    raw = (
        "not-valid-cas"
        if mode == "invalid" else json.dumps({
            "schema": "cifereca.source-sync-consent/1",
            "scope": [
                scope.identity_id, scope.provider_id,
                scope.credential_id, scope.account_id,
            ],
            "enabled": mode == "on", "revision": "a" * 32,
        })
    )
    service.storage.metadata_set(policy_key(scope), raw)
    return policy_key(scope)


@pytest.mark.parametrize("policy_mode", ("on", "off", "invalid"))
@pytest.mark.parametrize("partial", (False, True), ids=("no-ABI", "partial-ABI"))
def test_cas_retired_legacy_preview_never_reads_backup_or_manifest(
    tmp_path, monkeypatch, policy_mode, partial,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    account = session.accounts[0]
    service.accounts = [account]
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "alice", "account_id": "account",
        "item_id": "private-old-edition", "title": "PRIVATE",
        "revision_id": "legacy", "status": "complete",
        "sha256": "a" * 64, "size": 1024,
        "local_path": "/private/old/ebook.epub",
    })
    if partial:
        monkeypatch.setattr(
            session, "downloadable_resource_snapshot",
            lambda *_a, **_k: pytest.fail("partial A ABI is not callable"),
            raising=False,
        )
    _policy(service, account, policy_mode)

    def forbid(*_a, **_kw):
        pytest.fail("legacy preview must not read 0.7 rights, manifest or GET")
    monkeypatch.setattr(service.storage, "backup_list", forbid)
    monkeypatch.setattr(service.storage, "backup_get", forbid)
    monkeypatch.setattr(session, "backup_manifest", forbid)
    monkeypatch.setattr(service, "_execute_kobo_acquisition", forbid)
    monkeypatch.setattr(service.storage, "backup_put", forbid)
    try:
        with pytest.raises(ProviderConfigurationError,
                           match="canonical_kobo_preview_abi_unavailable"):
            service.kobo_backup_preview("vault-1", refresh_manifest=True)
        for kwargs in (
            {"_manifest_evidence": ([], 0)},
            {"_item_reasons": {}},
        ):
            with pytest.raises(ProviderConfigurationError,
                               match="canonical_kobo_preview_abi_unavailable"):
                service.kobo_backup_preview("vault-1", **kwargs)

        historical = service.kobo_backup_preview(
            "vault-1", verify_local_files=False,
        )
        assert historical["canonical_source"] is True
        assert historical["historical_canonical_only"] is True
        assert historical["manifest_valid"] is False
        assert historical["manifest_refreshed"] is False
        assert historical["acquisition_authorized"] is False
        assert historical["known_item_count"] == 0
        assert historical["historical_canonical"]["recorded_rights"] == 0
        assert all(n == 0 for n in historical["counts"].values())
        assert "PRIVATE" not in json.dumps(historical)

        # A disconnected local-only service must also honor the stored CAS.
        session.connected = False
        service.accounts = []
        disconnected = service.kobo_backup_preview("vault-1")
        assert disconnected["canonical_source"] is True
        assert disconnected["historical_canonical_only"] is True
        assert disconnected["counts"] == historical["counts"]
    finally:
        service.close()


def test_unreadable_exact_cas_cannot_restore_legacy_preview(tmp_path, monkeypatch):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    account = session.accounts[0]
    service.accounts = [account]
    key = policy_key(scope_for_account(account))
    get = service.storage.metadata_get

    def broken(query):
        if query == key:
            raise OSError("private SQLITE fault")
        return get(query)

    monkeypatch.setattr(service.storage, "metadata_get", broken)
    monkeypatch.setattr(session, "backup_manifest",
                        lambda: pytest.fail("unreadable CAS is not legacy access"))
    try:
        with pytest.raises(ProviderConfigurationError,
                           match="canonical_kobo_preview_abi_unavailable"):
            service.kobo_backup_preview("vault-1", refresh_manifest=True)
    finally:
        service.close()


@pytest.mark.parametrize("mutation", (
    "provider_id", "credential_id", "identity_id", "registry_alias",
))
def test_full_abi_live_preview_revalidates_four_coordinates_after_provider_a(
    typed, monkeypatch, mutation,
):
    case = typed.case
    account = case.target.accounts[0]
    before = case.service.storage.placements()
    original = rights_snapshot_read.read_account_rights_snapshot

    def rebind(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        if mutation == "registry_alias":
            oldkey = case.service._session_key("kobo", account.credential_id)
            case.service._sessions["kobo-stale-alias"] = case.service._sessions.pop(oldkey)
        else:
            value = {
                "provider_id": "other-provider",
                "credential_id": "another-credential",
                "identity_id": "someone-else",
            }[mutation]
            case.target._accounts = [replace(account, **{mutation: value})]
        return snapshot

    monkeypatch.setattr(
        rights_snapshot_read, "read_account_rights_snapshot", rebind,
    )
    with pytest.raises(ProviderConfigurationError,
                       match="canonical_kobo_preview_scope_changed"):
        case.service.kobo_backup_preview(
            "vault-2", refresh_manifest=True,
        )
    assert case.service.storage.placements() == before
    assert not case.downloaded
