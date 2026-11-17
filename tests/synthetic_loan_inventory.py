"""Offline explicit loan observations for opening Sync safety tests.

This fixture registers exact account/session ownership and supplies only the
producer observation. Core read, validation, source guards, cache and rights
persistence are real; no network or automatic acquisition is enabled.
"""
from types import SimpleNamespace

from cifereca.plugin_api import LoanInventoryScope, LoanInventorySnapshot
from cifereca.providers.base import ProviderCapabilities


def attach_opening_loan_reader(service, read, *, typed=True):
    """Set a synthetic reader on every existing account's exact source.

    Success with no coverage claims is not negative evidence. Use typed=False
    for tests specifically exercising the installed positive-only v1 ABI.
    Never retrofit a row's provenance or overwrite a supplied descriptor.
    """
    sources = {}
    for account in service.accounts:
        key = service._session_key(account.provider_id, account.credential_id)
        sources.setdefault(key, []).append(account)
    for key, accounts in sources.items():
        session = service._sessions.get(key)
        if session is None:
            session = SimpleNamespace(
                provider_id=accounts[0].provider_id,
                credential_id=accounts[0].credential_id,
                connected=True,
                capabilities=ProviderCapabilities(loans=True),
                close=lambda: None,
            )
            service._sessions[key] = session
        session.accounts = accounts
        if typed:
            def observe(account, bound=session):
                return LoanInventorySnapshot(
                    scope=LoanInventoryScope.for_account(
                        account, descriptor=getattr(bound, "descriptor", None),
                    ),
                    loans=tuple(read(account, force=True)),
                    status="success",
                )
            session.loan_inventory_snapshot = observe
            def forbidden(_account):
                raise AssertionError("typed provider must never fall back to v1")
            session.loans = forbidden
        else:
            session.loans = lambda account: list(read(account, force=True))
