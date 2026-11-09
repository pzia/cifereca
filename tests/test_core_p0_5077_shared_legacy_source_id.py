"""P0 #5077: Sync broker and legacy consent MUST hash identical source bytes."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.legacy_source_identifier import legacy_source_id
from cifereca.core.source_auto_consent_bridge import _source_id
from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope


def expected(identity, provider, credential, account):
    normalized = account.strip() or provider + ":" + credential + ":primary"
    coordinates = "\0".join((identity, provider, credential, normalized))
    return "sync:" + hashlib.sha256(coordinates.encode("utf-8")).hexdigest()[:24]


@pytest.mark.parametrize("provider", ["kobo", "bnf-pret", "numilog", "future-provider"])
@pytest.mark.parametrize("account", ["", "borrower-a", " borrower-a ", "account:secondary"])
def test_stable_source_id_is_exact_across_broker_consent_and_encoder(provider, account):
    identity, credential = "alice", "card-a"
    broker_account = NS(identity_id=identity, provider_id=provider,
                        credential_id=credential, unique_id=account)
    scope = ProviderRightsScope(
        identity_id=identity, provider_id=provider,
        credential_id=credential, account_id=account,
    )
    stable = expected(identity, provider, credential, account)
    assert legacy_source_id(identity, provider, credential, account) == stable
    assert _source_id(scope) == stable
    assert SynchronizationBrokerImpl._source_id(broker_account) == stable


def test_primary_alias_is_stable_but_not_a_consent_lease():
    from cifereca.core.legacy_source_account_id import ambiguous_legacy_source_account_id
    identity, provider, credential = "alice", "kobo", "card-a"
    fallback = provider + ":" + credential + ":primary"
    assert legacy_source_id(identity, provider, credential, "") == (
        legacy_source_id(identity, provider, credential, fallback)
    )
    # The historical digest is *ambiguous*. Negative policy admission
    # still refuses a literal primary alias, not silently granting a right.
    assert ambiguous_legacy_source_account_id(provider, credential, fallback)
    assert not ambiguous_legacy_source_account_id(provider, credential, "")


def test_identity_provider_and_credential_are_separate_hash_coordinates():
    source = legacy_source_id("alice", "numilog", "card-a", "buyer")
    for different in (
        ("bob", "numilog", "card-a", "buyer"),
        ("alice", "kobo", "card-a", "buyer"),
        ("alice", "numilog", "card-b", "buyer"),
        ("alice", "numilog", "card-a", "second-buyer"),
    ):
        assert legacy_source_id(*different) != source
