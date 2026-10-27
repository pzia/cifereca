"""#2888 / #3228: preserve observed BnF refusal causes in the RAW journal.

No production HTTP, downloads, ADEPT or private tokens. Public outcome codes
and acquisition admission remain closed, independent of logging detail.
"""
from __future__ import annotations

import logging

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.bnf_auto_materialization import materialize
from cifereca.plugin_api import Cancelled
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core


SENTINEL = "SYNTHETIC_BNF_ORIGINAL_EXCEPTION_REFERENCE"


def _ready(connected_core):
    service, session, account, loan, _state, downloaded = connected_core
    broker = SynchronizationBrokerImpl(service)
    assert broker.set_bnf_auto_download(broker._source_id(account), True)
    return service, session, account, loan, downloaded


def test_bnf_download_failure_logs_original_cause_without_new_attempt(
    connected_core, monkeypatch, caplog,
):
    service, session, account, loan, downloaded = _ready(connected_core)
    calls = []

    def failed_before_get(*_args, **_kwargs):
        calls.append(True)
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(session, "download_digital", failed_before_get)
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        outcome = materialize(service, account, loan)
    assert outcome == {
        "status": "attention_required", "code": "bnf_auto_acquisition_failed",
    }
    assert calls == [True] and downloaded == []
    assert "stage=delivery_or_import" in caplog.text
    assert "provider_id=bnf-pret" in caplog.text
    assert loan.id in caplog.text and SENTINEL in caplog.text
    assert SENTINEL not in repr(outcome)
    assert service.storage.acquisitions(provider_id="manual") == []


@pytest.mark.parametrize("stage,patch_path", [
    ("indexed_candidates", "cifereca.core.local_candidate_screen"),
    ("physical_candidates", "cifereca.core.local_physical_title_candidates"),
])
def test_bnf_local_preflight_failure_logs_raw_cause_without_get(
    connected_core, monkeypatch, caplog, stage, patch_path,
):
    import importlib
    service, session, account, loan, downloaded = _ready(connected_core)
    module = importlib.import_module(patch_path)
    symbol = (
        "screen_local_candidates" if stage == "indexed_candidates"
        else "observe_physical_local_titles"
    )

    def broken_read(**_kw):
        raise OSError(SENTINEL)

    monkeypatch.setattr(module, symbol, broken_read)
    attempted = []
    monkeypatch.setattr(
        session, "download_digital",
        lambda *_a, **_kw: attempted.append(True),
    )
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        outcome = materialize(service, account, loan)
    assert outcome == {
        "status": "attention_required", "code": "bnf_local_evidence_unavailable",
    }
    assert f"stage={stage}" in caplog.text
    assert SENTINEL in caplog.text and loan.id in caplog.text
    assert not attempted and not downloaded
    assert SENTINEL not in repr(outcome)


def test_cancelled_bnf_local_screen_stays_cancelled_not_provider_error(
    connected_core, monkeypatch,
):
    from cifereca.core import local_candidate_screen
    service, session, account, loan, downloaded = _ready(connected_core)
    calls = []

    def cancelled(**_kwargs):
        calls.append(True)
        raise Cancelled("synthetic cancellation")

    monkeypatch.setattr(local_candidate_screen, "screen_local_candidates", cancelled)
    with pytest.raises(Cancelled):
        materialize(service, account, loan)
    assert calls == [True] and downloaded == []
