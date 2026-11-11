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
    assert account.credential_id not in caplog.text
    assert account.identity_id not in caplog.text
    assert account.unique_id not in caplog.text
    assert SENTINEL not in repr(outcome)
    assert service.storage.acquisitions(provider_id="manual") == []



def test_cancelled_bnf_delivery_before_signed_get_remains_cancelled(
    connected_core, monkeypatch,
):
    from types import SimpleNamespace as NS

    service, session, account, loan, downloaded = _ready(connected_core)
    called = []
    monkeypatch.setattr(
        session, "download_digital",
        lambda *_a, **_kw: called.append(True),
    )

    def stop():
        raise Cancelled("synthetic cancellation before signed GET")

    with pytest.raises(Cancelled):
        materialize(service, account, loan, control=NS(check_cancelled=stop))
    assert called == [] and downloaded == []
