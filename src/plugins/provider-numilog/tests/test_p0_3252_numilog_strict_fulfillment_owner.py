"""Numilog ADEPT preflight: exact Core account-owner, never a deduplicated label.

All owner refs synthetic. No HTTP, disk read, ACSM import, ADEPT, SQLite.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_numilog_owned_enrichment_identity_race_p0 import make_session


def account_refs(session, owners):
    account = "numilog:" + session.scope
    return [
        NS(provider_id="numilog", account_id=account, identity_id=owner)
        for owner in owners
    ]


@pytest.mark.parametrize("owners", [
    [], ["identity-alice", "identity-alice"],
    ["identity-alice", "identity-bob"], [True], [123], [""],
])
def test_ambiguous_or_untyped_numilog_adept_owner_never_imports(owners):
    session, broker, _row = make_session()
    broker.accounts = lambda identity_id=None: account_refs(session, owners)
    calls = []
    session.acquisition_broker = NS(import_acsm=lambda *_a, **_kw: calls.append("ADEPT"))
    try:
        with pytest.raises(ProviderCapabilityError, match="non résolue"):
            session._fulfill_acsm("synthetic.acsm")
        assert calls == []
    finally:
        session.close()


def test_unique_attested_owner_still_resolves_for_adept_preflight():
    session, broker, _row = make_session()
    try:
        assert session._identity_id() == "identity-alice"
        assert broker.calls == 1
    finally:
        session.close()


def test_foreign_provider_account_ref_never_becomes_numilog_owner():
    session, broker, _row = make_session()
    broker.accounts = lambda identity_id=None: [
        NS(provider_id="kobo", account_id="numilog:" + session.scope,
           identity_id="identity-alice"),
    ]
    try:
        with pytest.raises(ProviderCapabilityError, match="non résolue"):
            session._identity_id()
    finally:
        session.close()
