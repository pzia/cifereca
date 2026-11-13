"""An explicitly scoped ACSM import must not inherit GTK navigation state."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.models import Loan
from cifereca.services import LibraryService
from cifereca.core.diagnostic_explicit_loan_action import LoanActionError


class _Observation:
    def __init__(self, *args, **kwargs):
        pass

    def at(self, *args):
        pass

    def emit(self, *args):
        pass


def _facade(tmp_path, owner="alice"):
    account = SimpleNamespace(
        provider_id="bnf-pret", unique_id="reader",
        credential_id="credential", identity_id="alice",
    )
    calls = []
    facade = SimpleNamespace(
        current_identity=SimpleNamespace(identity_id="bob"),
        storage=SimpleNamespace(identity_for_connection=lambda *args: owner),
        _account_for_loan=lambda loan: account,
        _acquisition_work_dir=lambda *args: tmp_path,
        import_acsm=lambda *args, **kwargs: (
            calls.append(kwargs) or {"status": "complete"}
        ),
    )
    return facade, calls


def _loan():
    return Loan(
        id="loan", provider="bnf-pret", medium="digital",
        record_id="book", title="Synthetic book",
        state="active", source_provider="bnf-pret",
        source_credential="credential", source_account_id="reader",
        identity_id="alice",
    )


def test_import_uses_explicit_identity_not_current_gtk_selection(
    tmp_path, monkeypatch,
):
    from cifereca.core import diagnostic_explicit_loan_action
    from cifereca.core import observed_loan_scope, observed_import_provenance

    monkeypatch.setattr(diagnostic_explicit_loan_action, "ActionObservation", _Observation)
    monkeypatch.setattr(observed_loan_scope, "observed_loan", lambda loan: {})
    monkeypatch.setattr(observed_import_provenance, "remember_observed_import", lambda *a, **k: None)
    facade, calls = _facade(tmp_path)
    candidate = tmp_path / "bnf-loan-synthetic.acsm"
    candidate.write_text("synthetic", encoding="utf-8")

    result = LibraryService.import_bnf_loan_acsm(
        facade, candidate, expected_identity_id="alice", loan=_loan(),
    )

    assert result["status"] == "complete"
    assert calls[0]["identity_id"] == "alice"


def test_import_rejects_changed_persisted_connection_owner(tmp_path, monkeypatch):
    from cifereca.core import diagnostic_explicit_loan_action
    monkeypatch.setattr(diagnostic_explicit_loan_action, "ActionObservation", _Observation)
    facade, calls = _facade(tmp_path, owner="bob")
    candidate = tmp_path / "bnf-loan-synthetic.acsm"

    with pytest.raises(LoanActionError):
        LibraryService.import_bnf_loan_acsm(
            facade, candidate, expected_identity_id="alice", loan=_loan(),
        )

    assert not calls
