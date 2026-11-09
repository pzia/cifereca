"""P0 #5055: selected offer scope is exact for any provider, never authority."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core.selected_offer_scope import selected_offer_scope_matches


def _selection(provider="future-library", credential="cred-a", account="reader-a",
               identity="alice", branch="private-loans"):
    return NS(
        branch=branch,
        scope=NS(identity_id=identity, provider_id=provider,
                 credential_id=credential, account_id=account),
    )


def _check(selected):
    return selected_offer_scope_matches(
        selected, identity_id="alice", provider_id="future-library",
        credential_id="cred-a", account_id="reader-a", branch="private-loans",
    )


def test_future_provider_and_bnf_use_the_same_exact_scope_predicate():
    assert _check(_selection())
    assert selected_offer_scope_matches(
        _selection(provider="bnf-pret", branch="bnf_html"),
        identity_id="alice", provider_id="bnf-pret",
        credential_id="cred-a", account_id="reader-a", branch="bnf_html",
    )


@pytest.mark.parametrize("field,value", [
    ("provider", "other-library"),
    ("credential", "cred-b"),
    ("account", "reader-b"),
    ("identity", "bob"),
    ("branch", "other-branch"),
])
def test_cross_source_owner_or_branch_fails_closed(field, value):
    assert not _check(_selection(**{field: value}))


def test_incomplete_or_malformed_selected_scope_fails_closed():
    for selected in (None, NS(), NS(branch="private-loans"),
                     NS(branch="private-loans", scope=None),
                     NS(branch="private-loans", scope=NS(provider_id="future-library"))):
        assert not _check(selected)


def test_predicate_does_not_observe_rights_or_trigger_delivery():
    class Selection:
        branch = "private-loans"
        scope = NS(identity_id="alice", provider_id="future-library",
                   credential_id="cred-a", account_id="reader-a")

        def exact(self, *_args):
            raise AssertionError("right lookup is caller-owned")

        def deliver(self, *_args):
            raise AssertionError("GET is caller-owned")

    assert _check(Selection())
