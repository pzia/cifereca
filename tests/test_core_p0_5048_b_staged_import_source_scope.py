"""#5048 / Agent B: one staged-import path, exact source and borrower, no provider dispatch.

Synthetic only: no network, GTK, ADEPT runtime, entitlement or provider I/O.
"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.diagnostic_explicit_loan_action import LoanActionError
from cifereca.models import Loan
from cifereca.services import LibraryService


def _loan(provider, identity, credential, account):
    return Loan(
        id="identical-upstream-loan", provider="synthetic", medium="digital",
        record_id="same-edition", title="Same title", state="active",
        source_provider=provider, source_credential=credential,
        source_account_id=account, identity_id=identity,
    )


def _host(tmp_path, monkeypatch, *, provider, identity, credential, account):
    exact = (provider, identity, credential, account)
    invocations = []
    links = []
    from cifereca.core import observed_import_provenance
    monkeypatch.setattr(
        observed_import_provenance, "remember_observed_import",
        lambda _service, _result, scope, **kwargs: links.append((scope, kwargs)),
    )

    def stage(owner, source, loan_id):
        directory = tmp_path / owner / source / credential / loan_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def account_for_loan(_loan):
        return NS(
            provider_id=provider, identity_id=identity,
            credential_id=credential, unique_id=account,
        )

    host = NS(
        # A deliberately unrelated GTK navigation selection must never
        # provide authorization or change exact source coordinates.
        current_identity=NS(identity_id="another-profile"),
        storage=NS(
            identity_for_connection=lambda source, token:
                identity if (source, token) == (provider, credential) else None,
        ),
        _account_for_loan=account_for_loan,
        _acquisition_work_dir=stage,
        import_acsm=lambda *args, **kwargs: (
            invocations.append((args, kwargs)) or {"status": "complete"}
        ),
    )
    return host, stage, invocations, links


@pytest.mark.parametrize("source", ["bnf-pret", "synthetic-lender"])
def test_staged_loan_import_consumes_exact_provider_source_not_a_core_vendor_branch(
    tmp_path, monkeypatch, source,
):
    host, stage, calls, links = _host(
        tmp_path, monkeypatch, provider=source,
        identity="alice", credential="credential-a", account="reader-a",
    )
    loan = _loan(source, "alice", "credential-a", "reader-a")
    prefix = source.split("-", 1)[0]
    candidate = stage(loan.identity_id, loan.source_provider, loan.id) / (
        prefix + "-loan-unique.acsm"
    )
    candidate.write_bytes(b"synthetic-private-staged-token")

    result = LibraryService.import_bnf_loan_acsm(
        host, candidate, expected_identity_id="alice", loan=loan,
    )
    assert result["status"] == "complete"
    assert len(calls) == 1
    assert calls[0][0] == (candidate,)
    assert calls[0][1]["identity_id"] == "alice"
    assert calls[0][1]["provider_id"] == "manual"
    assert calls[0][1]["placement_provider_id"] == source
    assert links[0][1] == {
        "provider_id": source, "provenance_field": prefix + "_observed_loan",
    }
    assert candidate.read_bytes() == b"synthetic-private-staged-token"


@pytest.mark.parametrize("change", ["borrower", "credential", "identity", "foreign_stage"])
def test_staged_import_never_rebinds_a_loan_to_another_source_or_account(
    tmp_path, monkeypatch, change,
):
    host, stage, calls, links = _host(
        tmp_path, monkeypatch, provider="synthetic-lender",
        identity="alice", credential="credential-a", account="reader-a",
    )
    loan = _loan("synthetic-lender", "alice", "credential-a", "reader-a")
    candidate = stage("alice", loan.source_provider, loan.id) / "synthetic-loan-unique.acsm"
    candidate.write_bytes(b"synthetic")
    if change == "borrower":
        loan = replace(loan, source_account_id="reader-b")
    elif change == "credential":
        loan = replace(loan, source_credential="credential-b")
    elif change == "identity":
        loan = replace(loan, identity_id="bob")
    else:
        foreign_dir = tmp_path / "bob" / loan.source_provider / "credential-b" / loan.id
        foreign_dir.mkdir(parents=True)
        candidate = foreign_dir / candidate.name
        candidate.write_bytes(b"other-profile")
    with pytest.raises(LoanActionError):
        LibraryService.import_bnf_loan_acsm(
            host, candidate, expected_identity_id="alice", loan=loan,
        )
    assert calls == [] and links == []
