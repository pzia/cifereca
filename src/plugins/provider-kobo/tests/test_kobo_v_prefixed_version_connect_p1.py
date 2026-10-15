"""#1346: Vault's validated v-prefixed API version must be parsed consistently.

All sessions and provider responses synthetic: no HTTP, Kobo account or DRM.
"""
from __future__ import annotations

import pytest

from cifereca.plugin_api import ProviderError
from tests.test_kobo_manifest_policy_p1 import FakeVault, session_with_vault


@pytest.mark.parametrize("version,expected", [
    ("2.3", True),
    ("v2.3", True),
    ("v2.3.0", True),
    ("v2.4.1", True),
    ("v2.2", False),
    ("2.2", False),
    ("v3.0", False),
    ("v2.3-private", False),
    ("v2.3\nBearer PRIVATE_TOKEN", False),
])
def test_provider_connect_accepts_the_same_versions_as_vault_public_validator(
    version, expected,
):
    vault = FakeVault([])
    calls = []
    vault.status = lambda authenticated=False: (
        calls.append(("status", authenticated))
        or {"api_version": version, "capabilities": ["library"],
            "acquisition_access": "configured"}
    )
    vault.account = lambda: (
        calls.append(("account", None))
        or {"id": "alice", "display_name": "Reader"}
    )
    session = session_with_vault(vault)
    try:
        if expected:
            rows = session.connect()
            assert len(rows) == 1 and rows[0].unique_id == "alice"
            assert session.connected is True
            assert session.api_version == version
            assert calls == [("status", False), ("account", None)]
        else:
            with pytest.raises(ProviderError, match="version API incompatible") as error:
                session.connect()
            assert session.connected is False
            assert calls == [("status", False)]
            for private in ("PRIVATE", "Bearer", "\n"):
                assert private not in str(error.value)
    finally:
        session.close()


def test_v_prefixed_version_does_not_change_inventory_or_acquisition_modes():
    vault = FakeVault([{"product_id": "safe", "owned": True}])
    vault.status = lambda authenticated=False: {
        "api_version": "v2.3",
        "capabilities": ["library", "acquisition"],
        "acquisition_access": "configured",
    }
    vault.account = lambda: {"id": "alice", "display_name": "Reader"}
    session = session_with_vault(vault)
    try:
        assert session.connect()[0].unique_id == "alice"
        assert [book.record_id for book in session.library()] == ["safe"]
        assert vault.manifest_calls == 1
        assert vault.acquisition_calls == 0
        assert session.supports_acquisition_acsm is False
        assert session.supports_acquisition_display_profile is False
    finally:
        session.close()
