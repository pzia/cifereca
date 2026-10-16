"""A manual Kobo refresh must never persist a stale personal manifest."""
from types import SimpleNamespace
import pytest

from cifereca.providers import ProviderConfigurationError
from tests.test_core_p0_1346_kobo_owned_mirror_binding import _setup
from tests.test_core_1337_kobo_personal_manifest_rights import _row, _service


def ready(tmp_path):
    service = _service(tmp_path)
    account, calls = _setup(service)
    session = service._sessions[service._session_key("kobo", account.credential_id)]
    session.capabilities.loans = False
    return service, account, session, calls


def test_manual_kobo_refresh_projects_rights_with_valid_binding(tmp_path):
    service, account, session, calls = ready(tmp_path)
    try:
        result = service._synchronize_account_source_impl(account)
        assert result["owned_rights"] == 1
        assert result["library"] == 1
        assert calls == ["library"]
        assert len(service.storage.entitlements(provider_id="kobo")) == 1
    finally:
        service.close()


def test_manual_kobo_refresh_rejects_rebound_owner_before_provider_fetch(tmp_path):
    service, account, session, calls = ready(tmp_path)
    try:
        service.storage.provider_connection_put({
            "identity_id": "bob", "provider_id": "kobo",
            "credential_id": account.credential_id, "enabled": True,
        })
        with pytest.raises(ProviderConfigurationError):
            service._synchronize_account_source_impl(account)
        assert calls == []
        assert service.storage.entitlements(provider_id="kobo") == []
    finally:
        service.close()


@pytest.mark.parametrize("mutation", ["rebind", "disable", "replace_session"])
def test_manual_kobo_refresh_drops_rows_if_authority_changes_during_fetch(tmp_path, mutation):
    service, account, session, calls = ready(tmp_path)

    def changed_library():
        calls.append("library")
        if mutation == "rebind":
            service.storage.provider_connection_put({
                "identity_id": "bob", "provider_id": "kobo",
                "credential_id": account.credential_id, "enabled": True,
            })
        elif mutation == "disable":
            service.storage.metadata_set(
                "sync.source.enabled." + service.sync_source_id(account), "off",
            )
        else:
            service._sessions[service._session_key("kobo", account.credential_id)] = (
                SimpleNamespace(provider_id="kobo", credential_id=account.credential_id)
            )
        return [_row(credential=account.credential_id)]

    session.library = changed_library
    try:
        with pytest.raises(ProviderConfigurationError):
            service._synchronize_account_source_impl(account)
        assert calls == ["library"]
        assert service.storage.entitlements(provider_id="kobo") == []
        assert service.storage.catalog_entries() == []
    finally:
        service.close()
