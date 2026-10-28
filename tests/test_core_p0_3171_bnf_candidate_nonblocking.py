"""#3171/#3228: a title is a hint, not a supplier receipt or permanent GET veto.

Real Core + BnF synthetic session and EPUB, no external HTTP/ADEPT.
The existing opt-in remains the only authorization for a signed provider GET;
a similar title does not create or revoke rights and is never adopted.
"""
from __future__ import annotations

from pathlib import Path

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.bnf_auto_materialization import materialize
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core
from tests.test_library_reconciliation import epub


def make_manual_same_title(tmp_path, loan):
    path = tmp_path / "EBooks" / "hugues" / "manual" / "old" / "same.epub"
    path.parent.mkdir(parents=True, exist_ok=True)
    epub(path, title=loan.title)
    return path


def test_title_candidate_never_enables_get_without_existing_account_opt_in(
    connected_core, tmp_path, monkeypatch,
):
    service, session, account, loan, state, downloaded = connected_core
    candidate = make_manual_same_title(tmp_path, loan)
    old = candidate.read_bytes()
    attempts = []
    monkeypatch.setattr(
        session, "download_digital",
        lambda *_a, **_kw: attempts.append(True),
    )
    assert materialize(service, account, loan)["status"] == "skipped"
    assert attempts == downloaded == []
    assert candidate.read_bytes() == old
    assert service.storage.entitlements(provider_id="bnf-pret") == []

    broker = SynchronizationBrokerImpl(service)
    assert broker.set_bnf_auto_download(broker._source_id(account), True)
    first = materialize(service, account, loan)
    assert first["status"] == "attention_required"
    assert first["code"] == "bnf_auto_acquisition_failed"
    assert attempts == [True] and downloaded == []
    assert candidate.read_bytes() == old
    assert service.storage.acquisitions(provider_id="manual") == []

    assert broker.set_bnf_auto_download(broker._source_id(account), False) is False
    assert materialize(service, account, loan)["status"] == "skipped"
    assert attempts == [True] and downloaded == []
    assert candidate.read_bytes() == old


def test_exact_same_title_other_profile_never_becomes_local_attestation(
    connected_core, tmp_path, monkeypatch,
):
    service, session, account, loan, state, downloaded = connected_core
    foreign = tmp_path / "EBooks" / "alex" / "manual" / "other" / "same.epub"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    epub(foreign, title=loan.title)
    old = foreign.read_bytes()
    broker = SynchronizationBrokerImpl(service)
    assert broker.set_bnf_auto_download(broker._source_id(account), True)
    attempts = []
    def stop(*_a, **_kw):
        attempts.append(True)
        raise RuntimeError("synthetic supplier GET stopped")
    monkeypatch.setattr(session, "download_digital", stop)
    outcome = materialize(service, account, loan)
    assert outcome == {
        "status": "attention_required", "code": "bnf_auto_acquisition_failed",
    }
    assert attempts == [True] and downloaded == []
    assert foreign.read_bytes() == old
    assert service.storage.acquisitions(provider_id="manual") == []
