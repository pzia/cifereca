"""P0 #5730 — descriptor cardinality and bootstrap identity normalization."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core.provider_account_limits import (
    ProviderAccountLimitError, validate_provider_account_limits,
)


def descriptor(provider, limit):
    return NS(id=provider, max_accounts_per_identity=limit)


def row(provider, identity, credential, *, enabled=True):
    return dict(provider=provider, identity_id=identity, id=credential, enabled=enabled)


def test_declared_limits_are_provider_and_identity_scoped():
    rows = [
        row("store", "a", "first"),
        row("store", "b", "second"),
        row("library", "a", "first"),
        row("library", "a", "second"),
        row("unbounded", "a", "one"),
        row("unbounded", "a", "two"),
    ]
    validate_provider_account_limits(rows, [
        descriptor("store", 1), descriptor("library", 2),
        descriptor("unbounded", None),
    ])
    with pytest.raises(ProviderAccountLimitError, match="limit exceeded"):
        validate_provider_account_limits(
            rows + [row("store", "a", "third")], [descriptor("store", 1)]
        )


def test_disabled_credentials_count_and_repeated_same_credential_does_not():
    rows = [row("store", "a", "same"), row("store", "a", "same", enabled=False)]
    validate_provider_account_limits(rows, [descriptor("store", 1)])
    with pytest.raises(ProviderAccountLimitError):
        validate_provider_account_limits(
            rows + [row("store", "a", "different", enabled=False)],
            [descriptor("store", 1)],
        )


def test_unknown_identity_matches_bootstrap_default_and_remains_private():
    rows = [row("store", "unknown", "secret-credential-A"),
            row("store", "", "secret-credential-B")]
    with pytest.raises(ProviderAccountLimitError) as raised:
        validate_provider_account_limits(
            rows, [descriptor("store", 1)],
            default_identity="alice", known_identities=("alice", "bob"),
        )
    assert "secret" not in str(raised.value)
    assert "alice" not in str(raised.value)


@pytest.mark.parametrize("limit", [0, -1, False, "1", 1.0])
def test_invalid_declared_limit_is_refused(limit):
    with pytest.raises(ProviderAccountLimitError, match="Invalid provider account limit"):
        validate_provider_account_limits([], [descriptor("store", limit)])


def test_missing_descriptor_does_not_guess_cardinality():
    validate_provider_account_limits(
        [row("absent", "a", "one"), row("absent", "a", "two")], []
    )


def test_malformed_account_container_fails_closed():
    with pytest.raises(ProviderAccountLimitError):
        validate_provider_account_limits({}, [descriptor("store", 1)])
