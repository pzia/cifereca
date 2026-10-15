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
