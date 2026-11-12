"""P0 #5048: private declared borrower aliases are installed-contract-only.

No network, file I/O, rights mutation, consent or provider-specific switch.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cifereca.core.library_declared_links import _account_current, _installed_descriptor
from cifereca.models import Account
from cifereca.providers.base import ProviderCapabilities, ProviderDescriptor
from cifereca.providers.registry import ProviderRegistry


def _descriptor(provider, alias):
    return ProviderDescriptor(
        id=provider, display_name=provider, description="Synthetic",
        state="ready", capabilities=ProviderCapabilities(),
        existing_primary_loan_account_alias=alias,
    )


def _account(provider, borrower="", *, identity="alice", credential="card",
             primary=True):
    return Account(
        provider_id=provider, credential_id=credential, unique_id=borrower,
        identity_id=identity, is_primary=primary,
    )


def _scope(provider, borrower, *, identity="alice", credential="card"):
    return dict(provider_id=provider, credential_id=credential,
                account_id=borrower, identity_id=identity, item_id="book")


@pytest.mark.parametrize("provider,alias", [
    ("synthetic-a", "main"), ("synthetic-b", "primary"),
])
def test_declared_alias_requires_exact_installed_descriptor(provider, alias):
    primary = _account(provider)
    scope = _scope(provider, alias)
    assert not _account_current([primary], scope)
    assert _account_current(
        [primary], scope, descriptor=_descriptor(provider, alias),
    )
    assert not _account_current(
        [primary], scope, descriptor=_descriptor(provider, "different"),
    )
    assert not _account_current(
        [primary], scope, descriptor=_descriptor("another-provider", alias),
    )


@pytest.mark.parametrize("field,value", [
    ("identity_id", "bob"), ("provider_id", "another-provider"),
    ("credential_id", "another-card"), ("account_id", "other"),
])
def test_declared_alias_never_crosses_exact_owner_source_or_borrower(field, value):
    provider = "synthetic-a"
    scope = {**_scope(provider, "main"), field: value}
    assert not _account_current(
        [_account(provider)], scope,
        descriptor=_descriptor(provider, "main"),
    )


def test_primary_alias_is_never_a_wildcard_for_linked_or_ambiguous_accounts():
    provider = "synthetic-b"
    desc = _descriptor(provider, "primary")
    linked = _account(provider, borrower="linked", primary=False)
    primary = _account(provider)
    assert not _account_current([linked], _scope(provider, "primary"), descriptor=desc)
    assert _account_current([primary, linked], _scope(provider, "primary"), descriptor=desc)
    duplicate = _account(provider, borrower="primary", primary=False)
    assert not _account_current(
        [primary, linked, duplicate], _scope(provider, "primary"), descriptor=desc,
    )
    primary.is_primary = False
    assert not _account_current([primary], _scope(provider, "primary"), descriptor=desc)


def test_installed_descriptor_reads_static_core_registry_not_plugin_getter():
    provider = "synthetic-a"
    registry = ProviderRegistry()
    descriptor = _descriptor(provider, "main")
    registry.register(SimpleNamespace(descriptor=descriptor))
    service = SimpleNamespace(registry=registry)
    assert _installed_descriptor(service, provider) is descriptor
    assert _installed_descriptor(service, "other") is None

    attempted = []
    class Poison:
        @property
        def descriptor(self):
            attempted.append("property_called")
            raise AssertionError("private provider getter must stay inert")

    # A malicious plugin may replace the Core registry entry after startup.
    registry._plugins[provider] = Poison()
    assert _installed_descriptor(service, provider) is None
    assert attempted == []
