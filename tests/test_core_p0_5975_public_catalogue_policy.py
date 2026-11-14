"""#5975: provider-neutral, read-only public navigation with legacy option data.

Only installed descriptor declarations may select an account-specific base URL.
No session, credential getter, catalogue HTTP or download right is consulted.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.providers.base import (
    AccountOptionField, ProviderCapabilities, ProviderDescriptor,
)
from cifereca.services import LibraryService


def descriptor(*, key="public_portal", legacy="previous_portal", secret=False):
    return ProviderDescriptor(
        id="future-library", display_name="Synthetic", description="Public link",
        state="ready", capabilities=ProviderCapabilities(),
        homepage="https://default.example/",
        public_catalogue_base_option_key=key,
        account_options=(AccountOptionField(
            "public_portal", "Public portal", secret=secret,
            legacy_config_key=legacy,
        ),),
    )


class ReadOnlyConfig:
    def __init__(self, options=None, legacy=""):
        self.options = options or {}
        self.legacy = legacy
        self.calls = []

    def provider_option(self, provider, key, default, *, credential_id):
        self.calls.append(("option", provider, key, credential_id))
        return self.options.get((provider, key, credential_id), default)

    def get(self, key, default):
        self.calls.append(("config", key))
        if key == "previous_portal":
            return self.legacy
        return default


def target(config, plugin, *, credential="alice", url="/notice/43"):
    host = NS(
        config=config, provider_descriptors=lambda: [plugin],
        current_identity=NS(identity_id="foreign-ui-profile"),
    )
    return LibraryService.public_catalogue_target(host, {
        "provider_id": "future-library", "source_credential": credential,
        "friendly_url": url,
    })


def test_non_paris_plugin_can_opt_into_exact_public_portal_without_core_dispatch():
    config = ReadOnlyConfig(options={
        ("future-library", "catalogue_url", "alice"): "https://generic.example/",
        ("future-library", "public_portal", "alice"): "https://alice.example/",
        ("future-library", "public_portal", "bob"): "https://bob.example/",
    })
    plugin = descriptor()
    assert target(config, plugin) == ("notice", "https://alice.example/notice/43")
    assert target(config, plugin, credential="bob") == (
        "notice", "https://bob.example/notice/43")
    assert ("option", "future-library", "public_portal", "alice") in config.calls
    assert ("option", "future-library", "public_portal", "bob") in config.calls
    assert all(entry[0] != "config" for entry in config.calls)


def test_read_only_legacy_config_fallback_is_declared_and_none_only():
    config = ReadOnlyConfig(legacy="https://previous.example/")
    assert target(config, descriptor()) == (
        "notice", "https://previous.example/notice/43")
    config = ReadOnlyConfig(
        options={("future-library", "public_portal", "alice"): ""},
        legacy="https://previous.example/",
    )
    assert target(config, descriptor()) == (
        "notice", "https://default.example/notice/43")
    assert ("config", "previous_portal") not in config.calls


@pytest.mark.parametrize("policy,secret", [
    ("unadvertised", False), (None, False), ("public_portal", True),
])
def test_undeclared_or_secret_navigation_preference_cannot_read_legacy_configuration(
    policy, secret,
):
    config = ReadOnlyConfig(legacy="https://secret.example/")
    assert target(config, descriptor(key=policy, secret=secret)) == (
        "notice", "https://default.example/notice/43")
    assert ("config", "previous_portal") not in config.calls


def test_invalid_configured_delivery_url_never_becomes_public_notice_or_catalogue():
    config = ReadOnlyConfig(options={
        ("future-library", "public_portal", "alice"):
            "https://delivery.example/loan/book.acsm?token=secret",
    })
    assert target(config, descriptor(), url="") is None


def test_none_descriptor_falls_back_to_generic_url_without_implicit_account():
    config = ReadOnlyConfig(options={
        ("future-library", "catalogue_url", "alice"): "https://catalogue.example/",
    }, legacy="https://not-declared.example/")
    # A non-opted descriptor does not read the provider-specific legacy option.
    plugin = descriptor(key=None)
    assert target(config, plugin, url="") == (
        "catalogue", "https://catalogue.example/")
    assert ("config", "previous_portal") not in config.calls
