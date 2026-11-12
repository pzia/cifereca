"""P0 #5731: neutral exact-source preview and confirmed borrower binding."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core import provider_sessions
from cifereca.models import Account
from cifereca.providers import ProviderConfigurationError


class Session:
    def __init__(self, provider, credential, calls, capability=True):
        self.provider_id = provider
        self.credential_id = credential
        self.connected = False
        self.capabilities = NS(backup=capability)
        self._accounts = [Account(
            unique_id="borrower", provider_id=provider,
            credential_id=credential, identity_id="default",
        )]
        self._epoch = 0
        self.calls = calls

    @property
    def accounts(self):
        return list(self._accounts)

    def connect(self):
        self.calls.append(("connect", self.provider_id, self.credential_id))
        self.connected = True
        return self.accounts

    def close(self):
        self.calls.append(("close", self.provider_id, self.credential_id))
        self.connected = False

    def rights_observation_snapshot(self, *args, **kwargs):
        raise AssertionError("no rights read during source admission")

    def rights_selected_delivery_offer_snapshot(self, *args, **kwargs):
        raise AssertionError("no offers during source admission")

    def rights_selected_delivery_candidate(self, *args, **kwargs):
        raise AssertionError("no download during source admission")


def setup_source(*, provider="synthetic", owner="alice", credential="card-a",
                 configured=True, ready=True, capability=True):
    calls = []
    plugin = NS(
        descriptor=NS(id=provider, state="ready" if ready else "unavailable",
                      display_name="Synthetic"),
        create_session=lambda **kw: (
            calls.append(("create", provider, kw["credential_id"]))
            or Session(provider, kw["credential_id"], calls, capability)
        ),
    )
    row = {
        "provider": provider, "id": credential,
        "identity_id": owner, "enabled": True,
    }
    stored = NS(
        identity_for_connection=lambda p, c: (
            owner if (p, c) == (provider, credential) else None
        ),
        provider_scope_account_bound=lambda **kw: (
            kw == dict(identity_id=owner, provider_id=provider,
                       credential_id=credential, account_id="borrower")
        ),
    )
    primary = Account(unique_id="local", provider_id="local",
                      credential_id="local", identity_id=owner)
    host = NS(
        storage=stored,
        config=NS(provider_accounts=lambda enabled_only=False:
                  [row] if configured else []),
        registry=NS(maybe_get=lambda p: plugin if p == provider else None),
        diagnostics=NS(),
        _sessions={},
        _session_key=lambda p, c: f"{p}::{c}",
        accounts=[primary], primary=primary, current_account=primary,
        current_identity=NS(identity_id=owner),
        identity_by_id=lambda ident: NS(identity_id=ident) if ident == owner else None,
        _source_context_epoch=9,
    )
    return host, calls


@pytest.mark.parametrize("provider", ["synthetic", "another-seller"])
def test_preview_and_canonical_admit_only_one_exact_provider(provider):
    host, calls = setup_source(provider=provider)
    session = provider_sessions.connect_source_preview(
        host, provider, "card-a", required_capability="backup",
    )
    assert calls == [("create", provider, "card-a"),
                     ("connect", provider, "card-a")]
    assert provider_sessions.connect_source_preview(
        host, provider, "card-a", required_capability="backup",
    ) is session
    assert len(calls) == 2
    result = provider_sessions.bind_confirmed_source_account(
        host, provider, "card-a", session,
    )
    assert result is session
    assert len(host.accounts) == 2
    assert host.accounts[-1].identity_id == "alice"
    assert host.current_account is host.primary
    assert host._source_context_epoch == 10
    assert provider_sessions.bind_confirmed_source_account(
        host, provider, "card-a", session,
    ) is session
    assert len(host.accounts) == 2


@pytest.mark.parametrize("provider,credential,configured,ready,capability", [
    ("different", "card-a", True, True, True),
    ("synthetic", "card-b", True, True, True),
    ("synthetic", "card-a", False, True, True),
    ("synthetic", "card-a", True, False, True),
    ("synthetic", "card-a", True, True, False),
])
def test_preview_failure_never_contacts_unrelated_source(
    provider, credential, configured, ready, capability,
):
    host, calls = setup_source(
        configured=configured, ready=ready, capability=capability,
    )
    with pytest.raises(ProviderConfigurationError):
        provider_sessions.connect_source_preview(
            host, provider, credential, required_capability="backup",
        )
    assert set(host._sessions) == set()
    assert len(host.accounts) == 1
    assert not any(call[1] != "synthetic" for call in calls)
    if provider != "synthetic" or credential != "card-a" or not configured or not ready:
        assert calls == []


def test_canonical_refuses_rebound_scope_before_mutation():
    host, calls = setup_source()
    session = provider_sessions.connect_source_preview(host, "synthetic", "card-a")
    host.storage.provider_scope_account_bound = lambda **kw: False
    with pytest.raises(ProviderConfigurationError, match="source_canonical_account_unavailable"):
        provider_sessions.bind_confirmed_source_account(
            host, "synthetic", "card-a", session,
        )
    assert len(host.accounts) == 1
    assert host._source_context_epoch == 9
    assert not any(kind == "close" for kind, *_ in calls)
