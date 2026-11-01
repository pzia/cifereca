"""#4047: digital and non-Paris loans must never reach a renewal mutation.

Synthetic Core fixture only: no supplier HTTP or ADEPT call. The public
facade and private renewal executor must both reject before SQLite claims.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.provider_loan_renewal import renew as scoped_renew
from tests.test_core_p1_3121_paris_renewal_one_post import loan, setup


@pytest.mark.parametrize(("provider", "branch", "medium"), [
    ("paris", "DILICOM", "digital"),
    ("bnf-pret", "SYRACUSE", "physical"),
    ("other.future", "SYRACUSE", "physical"),
    ("paris", "SYRACUSE", "digital"),
])
def test_direct_core_and_public_renew_reject_unsupported_loan_before_any_io(
    tmp_path, provider, branch, medium,
):
    # Even an explicitly renewable upstream card is not authorization.
    service, account, _selected, session = setup(tmp_path)
    selected = replace(
        loan(account, branch=branch, can_renew=True), medium=medium,
        source_provider=provider,
    )
    session.inventory = [replace(
        loan(account, branch=branch, raw=True, can_renew=True),
        medium=medium, source_provider=provider,
    )]
    try:
        public = service.renew(selected, confirmed=True)
        direct = scoped_renew(service, selected)
        for result in (public, direct):
            assert result["status"] == "blocked"
            assert result["code"] == "loan_scope_unverified"
            assert result["provider_request_accepted"] is False
        assert session.reads == []
        assert session.posts == []
    finally:
        service.close()


def test_positive_physical_paris_remains_single_submitted_post(tmp_path):
    service, _account, selected, session = setup(tmp_path)
    try:
        outcome = service.renew(selected, confirmed=True)
        assert outcome["status"] == "submitted"
        assert outcome["provider_request_accepted"] is True
        assert len(session.reads) == 1
        assert len(session.posts) == 1
        assert service.renew(selected, confirmed=True)["status"] == "submitted"
        assert len(session.posts) == 1
    finally:
        service.close()
