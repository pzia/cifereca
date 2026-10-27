"""#3121: borrower loan-action preflight never runs provider getters.

Read-only synthetic Core/session fixtures. No Paris POST, ACSM, ADEPT, file
mutation, authentication, HTTP or other network operation.
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace as NS

from cifereca.plugin_api import AttentionRequired
from tests.test_core_0982_loan_return_reconciliation import (
    Session, _library, _loan,
)


@pytest.mark.parametrize("kind", ["property", "dynamic"])
def test_return_borrower_preflight_refuses_provider_getters_before_any_io(tmp_path, kind):
    touched = []
    if kind == "property":
        class HostileSession(Session):
            @property
            def connected(self):
                touched.append("provider connected GET")
                raise AssertionError("preflight may not read connected getter")
    else:
        class HostileSession(Session):
            def __getattribute__(self, attr):
                if attr == "connected":
                    touched.append("provider connected GET")
                    raise AssertionError("preflight may not read __getattribute__")
                return super().__getattribute__(attr)

    session = HostileSession([_loan()])
    service = _library(tmp_path, session=session)
    try:
        with pytest.raises(AttentionRequired) as denied:
            service._return_loan_identity(_loan())
        assert denied.value.code == "loan_return_source_unavailable"
        assert touched == []
        assert session.posts == [] and session.checks == []
    finally:
        service.close()


def test_plain_legacy_session_remains_available_to_existing_borrower_preflight(tmp_path):
    session = Session([_loan()])
    service = _library(tmp_path, session=session)
    try:
        account, resolved, key = service._return_loan_identity(_loan())
        assert resolved is session
        assert account.unique_id == "linked-b"
        assert account.credential_id == "paris-a"
        assert type(key) is str and key
        assert session.posts == [] and session.checks == []
    finally:
        service.close()


def test_simple_namespace_session_keeps_legacy_preflight_compatibility(tmp_path):
    service = _library(tmp_path, session=NS(connected=True))
    try:
        account, session, key = service._return_loan_identity(_loan())
        assert account.unique_id == "linked-b"
        assert session is service._sessions["paris::paris-a"]
        assert type(key) is str and key
    finally:
        service.close()
