"""P0 #4673: a real ProviderSession-shaped offline Paris fixture is locally actionable.

The GTK acceptance uses the same typed, inert contract. This regression
uses actual Core/SQLite/config and deliberately never invokes provider I/O.
"""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account, SearchResult
from cifereca.providers.base import (
    ProviderSession, ProviderDescriptor, ProviderCapabilities,
)
from tests.test_core043_progress_cache_stats import _service


class OfflineParisSession(ProviderSession):
    def __init__(self, identity):
        credential = "card-" + identity
        super().__init__(
            descriptor=ProviderDescriptor(
                id="paris", display_name="Paris", description="offline fixture",
                state="ready",
                capabilities=ProviderCapabilities(
                    search=True, holdings=True, borrow=True,
                ),
            ),
            credential_id=credential, label="Paris",
            credentials={}, options={},
        )
        self.connected = True
        self._accounts = [Account(
            unique_id="", provider_id="paris", credential_id=credential,
            identity_id=identity, display_name="Fictive",
        )]

    def connect(self):
        return self._accounts

    def close(self):
        self.connected = False

    def borrow(self, item, account):
        raise AssertionError("No provider call")

    def borrow_explicit_with_claim(self, *args, **kwargs):
        raise AssertionError("No provider call")


def _book(credential):
    return SearchResult(
        id="paris:" + credential, record_id="edition-" + credential,
        docbase="DILICOM", medium="digital", title="Serpentine",
        author="Autrice fictive", type="ebook", format="EPUB",
        publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario="EBOOKS",
        source_provider="paris", source_credential=credential,
        source_collection="catalog",
    )


@pytest.mark.parametrize("identity", ["alice", "bob"])
def test_two_account_offline_sessions_are_locally_actionable_for_owner_only(
    tmp_path, identity,
):
    service = _service(tmp_path)
    service.identities.append(replace(
        service.current_identity, identity_id="bob", label="Bob",
    ))
    service.config.set("identities", [
        {"id": name, "label": name,
         "primary_paris_connection_id": "card-" + name}
        for name in ("alice", "bob")
    ])
    service.config.set("providers.accounts", [
        {"id": "card-" + name, "provider": "paris",
         "identity_id": name, "enabled": True}
        for name in ("alice", "bob")
    ])
    service._provider_plugin_ids["paris"] = "fixture.paris"
    service._plugin_manager = NS(status=lambda _plugin_id: NS(
        enabled=True, state="ready",
        descriptor=NS(
            id="fixture.paris", type="provider.library",
            capabilities=("acquisition.borrow",),
        ),
    ))
    alice, bob = OfflineParisSession("alice"), OfflineParisSession("bob")
    service._sessions = {
        service._session_key("paris", "card-bob"): bob,
        service._session_key("paris", "card-alice"): alice,
    }
    service.accounts = [*alice._accounts, *bob._accounts]
    for name in ("alice", "bob"):
        service.storage.provider_connection_put({
            "identity_id": name, "provider_id": "paris",
            "credential_id": "card-" + name,
            "label": "Fictive", "enabled": True, "metadata": {},
        })
    try:
        service.select_identity(identity)
        own = service.paris_borrow_diagnostic(_book("card-" + identity))
        # A closed reason only, no secrets and no remote supplier request.
        assert own["local_action_possible"] is True, own["reason_code"]
        assert own["reason_code"] == "local_action_available_provider_unknown"
        other = "bob" if identity == "alice" else "alice"
        foreign = service.paris_borrow_diagnostic(_book("card-" + other))
        assert foreign["local_action_possible"] is False
    finally:
        service.close()
