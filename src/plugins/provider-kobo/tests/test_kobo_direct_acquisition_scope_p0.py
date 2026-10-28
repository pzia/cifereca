"""#1346: provider direct acquisition cannot bypass account-scoped 403 guards.

Every session is synthetic; no real Kobo account, Vault HTTP, DRM or file I/O.
"""
import pytest

from cifereca.plugin_api import ProviderError
from tests.test_kobo_manifest_policy_p1 import FakeVault, session_with_vault


@pytest.mark.parametrize("access", (
    "denied", "forbidden", "requires_scope", "disabled", "unavailable",
))
@pytest.mark.parametrize("mode", ("descriptor", "delegated"))
def test_explicitly_denied_account_scope_blocks_all_modes_before_post(access, mode):
    vault = FakeVault([])
    session = session_with_vault(vault)
    try:
        session.acquisition_access = access
        with pytest.raises(ProviderError, match="acquisition indisponible") as error:
            session.resolve_acquisition(
                "edition-1", revision_id="PRIVATE_REVISION",
                mode=mode, display_profile="Android",
            )
        assert vault.acquisition_calls == 0
        assert "PRIVATE" not in str(error.value)
        assert "Bearer" not in str(error.value)
    finally:
        session.close()


@pytest.mark.parametrize("mode", ("delegated", "DELEGATED", "Delegated"))
def test_persisted_delegated_403_blocks_only_delegated_requests(mode):
    vault = FakeVault([])
    session = session_with_vault(vault)
    try:
        session._cifereca_delegation_forbidden = True
        with pytest.raises(ProviderError, match="délégation indisponible"):
            session.resolve_acquisition("PRIVATE_EDITION", mode=mode)
        assert vault.acquisition_calls == 0
    finally:
        session.close()


def test_delegated_denial_does_not_block_independent_descriptor_path():
    vault = FakeVault([])
    calls = []

    def acquisition(product_id, **options):
        calls.append((product_id, options))
        return {"acquisition": {"mode": "descriptor"}}

    vault.acquisition = acquisition
    session = session_with_vault(vault)
    try:
        session._cifereca_delegation_forbidden = True
        result = session.resolve_acquisition("edition-1", mode="descriptor")
        assert result["acquisition"]["mode"] == "descriptor"
        assert calls == [("edition-1", {
            "revision_id": None, "mode": "descriptor",
            "display_profile": None, "include_acsm": None,
        })]
    finally:
        session.close()


def test_connected_account_without_denial_can_use_delegated_mode():
    vault = FakeVault([])
    calls = []
    vault.acquisition = lambda product_id, **kwargs: (
        calls.append((product_id, kwargs["mode"])) or {"ok": True}
    )
    session = session_with_vault(vault)
    try:
        assert session.resolve_acquisition("edition-1", mode="delegated") == {"ok": True}
        assert calls == [("edition-1", "delegated")]
    finally:
        session.close()


def test_one_accounts_delegated_denial_never_blocks_other_accounts():
    vaults = [FakeVault([]) for _ in range(3)]
    calls = [[] for _ in vaults]
    sessions = []
    for index, vault in enumerate(vaults):
        vault.acquisition = (
            lambda product_id, *, _index=index, **kwargs:
            calls[_index].append((product_id, kwargs["mode"])) or {"ok": True}
        )
        sessions.append(session_with_vault(vault, account_id=f"reader-{index}"))
    try:
        sessions[1]._cifereca_delegation_forbidden = True
        with pytest.raises(ProviderError, match="délégation indisponible"):
            sessions[1].resolve_acquisition("edition-1", mode="delegated")
        sessions[0].resolve_acquisition("edition-1", mode="delegated")
        sessions[2].resolve_acquisition("edition-1", mode="delegated")
        assert calls == [
            [("edition-1", "delegated")], [],
            [("edition-1", "delegated")],
        ]
    finally:
        for session in sessions:
            session.close()


def test_first_direct_delegated_403_is_remembered_and_next_request_is_offline():
    from cifereca_kobo_vault import KoboVaultError

    vault = FakeVault([])
    calls = []

    def denied(product_id, **kwargs):
        calls.append((product_id, kwargs["mode"]))
        raise KoboVaultError(
            "Kobo-Vault : autorisation de délégation requise",
            code="forbidden", status_code=403,
        )

    vault.acquisition = denied
    session = session_with_vault(vault)
    try:
        with pytest.raises(ProviderError):
            session.resolve_acquisition("edition-a", mode="delegated")
        assert session._cifereca_delegation_forbidden is True
        assert calls == [("edition-a", "delegated")]

        with pytest.raises(ProviderError, match="délégation indisponible"):
            session.resolve_acquisition("edition-b", mode="delegated")
        assert calls == [("edition-a", "delegated")]
    finally:
        session.close()


@pytest.mark.parametrize("status", (401, 409, 429, 503))
def test_non_403_direct_error_does_not_poison_delegated_capability(status):
    from cifereca_kobo_vault import KoboVaultError

    vault = FakeVault([])
    attempts = []

    def fail(product_id, **kwargs):
        attempts.append((product_id, kwargs["mode"]))
        raise KoboVaultError("Kobo-Vault : service refusé", code="http_error", status_code=status)

    vault.acquisition = fail
    session = session_with_vault(vault)
    try:
        for _ in range(2):
            with pytest.raises(KoboVaultError):
                session.resolve_acquisition("edition-a", mode="delegated")
        assert not getattr(session, "_cifereca_delegation_forbidden", False)
        assert attempts == [("edition-a", "delegated")] * 2
    finally:
        session.close()


def test_descriptor_403_does_not_poison_delegated_scope():
    from cifereca_kobo_vault import KoboVaultError

    vault = FakeVault([])
    calls = []

    def respond(product_id, **kwargs):
        calls.append(kwargs["mode"])
        if kwargs["mode"] == "descriptor":
            raise KoboVaultError("scope denied", code="forbidden", status_code=403)
        return {"ok": True}

    vault.acquisition = respond
    session = session_with_vault(vault)
    try:
        with pytest.raises(KoboVaultError):
            session.resolve_acquisition("edition", mode="descriptor")
        assert not getattr(session, "_cifereca_delegation_forbidden", False)
        assert session.resolve_acquisition("edition", mode="delegated") == {"ok": True}
        assert calls == ["descriptor", "delegated"]
    finally:
        session.close()


@pytest.mark.parametrize("race", [
    "account_replaced", "account_rebound", "reconnect_ABA",
    "credential_rebound", "broker_ABA", "broker_owner_B", "vault_replaced",
])
@pytest.mark.parametrize("outcome", ["success", "delegated_403"])
def test_kobo_acquisition_inflight_rebind_cannot_publish_old_descriptor_or_denial(
    race, outcome,
):
    """A completed Vault POST is not a Core delivery proof for another owner."""
    from dataclasses import replace
    from types import SimpleNamespace as NS
    from cifereca_kobo_vault import KoboVaultError

    vault = FakeVault([])
    session = session_with_vault(vault)
    broker = NS(owner="identity-A")
    broker.accounts = lambda: [
        NS(provider_id="kobo", account_id="alice", identity_id=broker.owner,
           credential_id=session.credential_id, scope_version=2)
    ]
    session.identity_broker = broker
    session._accounts[0].identity_id = "identity-A"
    calls = []

    def acquire(product_id, **_):
        calls.append("POST")
        if race == "account_replaced":
            session._accounts = [replace(session._accounts[0])]
        elif race == "account_rebound":
            session._accounts[0].identity_id = "identity-B"
        elif race == "reconnect_ABA":
            session._personal_binding_generation += 1
        elif race == "credential_rebound":
            session._accounts[0].credential_id = "different-credential"
        elif race == "broker_ABA":
            other = NS(owner="identity-A")
            other.accounts = broker.accounts
            session.identity_broker = other
        elif race == "broker_owner_B":
            broker.owner = "identity-B"
        else:
            session.client = FakeVault([])
        if outcome == "delegated_403":
            raise KoboVaultError(
                "Private upstream error",
                code="forbidden", status_code=403,
            )
        return {"private_signed_descriptor": "PRIVATE-URL"}

    vault.acquisition = acquire
    try:
        with pytest.raises(ProviderError, match="modifiée|modifié"):
            session.resolve_acquisition("edition-1", mode="delegated")
        assert calls == ["POST"]
        assert not getattr(session, "_cifereca_delegation_forbidden", False)
    finally:
        session.close()


def test_kobo_acquisition_stable_scope_returns_only_selected_descriptor():
    vault = FakeVault([])
    calls = []
    vault.acquisition = lambda product_id, **opts: (
        calls.append((product_id, opts["mode"])) or {"ok": True}
    )
    session = session_with_vault(vault)
    try:
        assert session.resolve_acquisition("edition-1", mode="descriptor") == {"ok": True}
        assert calls == [("edition-1", "descriptor")]
    finally:
        session.close()
