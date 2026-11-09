"""P0 #5055: provider-neutral explicit ACSM staging, synthetic source.

The real BnF suite exercises token inspection and lease/SQLite semantics.
This suite proves the extracted dispatcher has no hard-coded provider or
branch and does not use a second download path.
"""
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Loan
from cifereca.plugin_api import ProviderConfigurationError
from cifereca.core import explicit_loan_acsm as core


CODES = {
    "confirmation": "confirmation_required",
    "scope": "source_scope_unproven",
    "unavailable": "delivery_abi_unavailable",
    "invalid": "token_invalid",
    "busy": "acquisition_busy",
}


def _rig(tmp_path, monkeypatch, *, provider="future-library", branch="loan-private"):
    observed = []
    account = NS(identity_id="alice", provider_id=provider,
                 credential_id="cred", unique_id="reader")
    loan = Loan(id="loan-1", provider="paris", medium="digital",
                record_id="edition-1", title="Synthetic", state="active",
                source_provider=provider, source_credential="cred",
                source_account_id="reader", identity_id="alice")
    work = tmp_path / "stage"
    work.mkdir()
    candidate = work / "received.acsm"
    candidate.write_text("synthetic", encoding="utf-8")
    right = NS(provider_attested=True, delivery_attested=True,
               delivery_offer_id="offer-1")

    def snapshot(_account, *, edition_id, right_id, kind, branch, explicit_user_action):
        observed.append(("snapshot", edition_id, right_id, kind, branch, explicit_user_action))
        return NS(scope=NS(identity_id="alice", provider_id=provider,
                           credential_id="cred", account_id="reader"),
                  branch=branch,
                  exact=lambda *_args: right)

    def deliver(_account, *, edition_id, right_id, kind, branch,
                delivery_offer_id, target_dir, explicit_user_action, admission_current):
        assert admission_current()
        observed.append(("delivery", edition_id, right_id, kind, branch,
                         delivery_offer_id, explicit_user_action))
        return candidate, ""

    session = NS(rights_selected_delivery_offer_snapshot=snapshot,
                 rights_selected_delivery_candidate=deliver)
    service = NS(
        current_identity=NS(identity_id="alice"),
        storage=NS(identity_for_connection=lambda p, c:
                   "alice" if (p, c) == (provider, "cred") else None),
        _account_for_loan=lambda l: account,
        _session_for_source=lambda p, c: session,
        _acquisition_work_dir=lambda *args: work,
    )
    monkeypatch.setattr(core, "capture_loan_action_scope",
                        lambda *_args, **_kwargs: NS(current=lambda service: True))
    # Token parser itself is covered by the existing BnF and Core tests.
    monkeypatch.setattr(core, "_valid_loan_token", lambda *_args: True)

    def run(current_loan=loan, *, confirmed=True):
        return core.stage_explicit_loan_acsm(
            service, current_loan, confirmed=confirmed, expected_identity_id="alice",
            interprocess_lock=lambda *_: nullcontext(), lock_path=lambda: tmp_path,
            busy_error=RuntimeError, inspect_artifact=lambda _: {},
            expected_provider_id=provider, selected_branch=branch,
            action_error_type=ProviderConfigurationError, error_codes=CODES,
            classify_activity_error=lambda _: "activity_unavailable",
            classify_candidate_error=lambda _: "token_unavailable",
        )

    return run, observed, candidate


def test_future_provider_selected_branch_and_exact_candidate(tmp_path, monkeypatch):
    run, observed, candidate = _rig(tmp_path, monkeypatch)
    assert run() == {"status": "candidate_acsm", "path": str(candidate)}
    assert observed == [
        ("snapshot", "edition-1", "loan-1", "loan", "loan-private", True),
        ("delivery", "edition-1", "loan-1", "loan", "loan-private", "offer-1", True),
    ]


def test_no_consent_or_wrong_provider_never_resolves_offer(tmp_path, monkeypatch):
    run, observed, _ = _rig(tmp_path, monkeypatch)
    with pytest.raises(ProviderConfigurationError, match="confirmation_required"):
        run(confirmed=False)
    other = Loan(id="loan-1", provider="paris", medium="digital",
                 record_id="edition-1", title="Synthetic", state="active",
                 source_provider="unrelated-library", source_credential="cred",
                 source_account_id="reader", identity_id="alice")
    with pytest.raises(ProviderConfigurationError, match="source_scope_unproven"):
        run(other)
    assert observed == []
