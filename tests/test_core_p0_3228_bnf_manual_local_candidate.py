"""P0 #5055: unrelated manual books never authorize reuse or veto delivery.

Every scenario is local/synthetic. No BnF/Adobe network or private fixture.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.bnf_auto_materialization import materialize
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core
from tests.test_library_reconciliation import epub



def _enable(core):
    service, session, account, loan, _state, downloaded = core
    broker = SynchronizationBrokerImpl(service)
    assert broker.set_bnf_auto_download(broker._source_id(account), True)
    return service, session, account, loan, downloaded


def _manual_receipt(service, tmp_path, title, identity="default", directory="manual"):
    root = tmp_path / "EBooks" / ("hugues" if identity == "default" else "alex") / directory
    path = root / "acsm-old" / "Previously Imported.epub"
    path.parent.mkdir(parents=True, exist_ok=True)
    epub(path, title=title)
    service.storage.acquisition_put({
        "acquisition_id": "old-import-" + identity, "identity_id": identity,
        "provider_id": "manual", "credential_id": "manual",
        "item_id": "old-acsm-" + identity, "title": title,
        "status": "complete", "strategy": "manual_acsm",
        "metadata": {"path": str(path)},
    })
    return path


@pytest.mark.parametrize("directory", ["manual", "acsm-import"])
def test_bnf_existing_manual_epub_does_not_veto_independently_authorized_get(
    connected_core, tmp_path, monkeypatch, directory,
):
    service, session, account, loan, downloaded = _enable(connected_core)
    manual = _manual_receipt(service, tmp_path, loan.title, directory=directory)
    original = manual.read_bytes()
    errors = []
    monkeypatch.setattr(session, "download_digital", lambda *_a, **_kw: errors.append("called"))
    before = service.storage.entitlements()
    for _ in range(2):
        outcome = materialize(service, account, loan)
        assert outcome == {
            "status": "attention_required",
            "code": "bnf_auto_acquisition_failed",
        }
    assert errors == ["called", "called"] and downloaded == []
    assert manual.read_bytes() == original
    assert service.storage.entitlements() == before
    assert len(service.storage.acquisitions(provider_id="manual")) == 1


@pytest.mark.parametrize("directory", ["manual", "acsm-import"])
def test_manual_same_title_in_another_identity_does_not_block_bnf_get(
    connected_core, tmp_path, monkeypatch, directory,
):
    service, session, account, loan, downloaded = _enable(connected_core)
    foreign = _manual_receipt(
        service, tmp_path, loan.title, identity="alex", directory=directory,
    )
    attempted = []
    def stop_before_get(*_args, **_kwargs):
        attempted.append(True)
        raise RuntimeError("synthetic stop")
    monkeypatch.setattr(session, "download_digital", stop_before_get)
    outcome = materialize(service, account, loan)
    assert outcome["code"] == "bnf_auto_acquisition_failed"
    assert attempted == [True]
    assert downloaded == []
    assert foreign.is_file()


def test_unrelated_second_loan_still_reaches_provider_after_manual_screen(
    connected_core, tmp_path, monkeypatch,
):
    service, session, account, loan, downloaded = _enable(connected_core)
    first = _manual_receipt(service, tmp_path, loan.title)
    second = replace(loan, title="Distinct second title")
    attempted = []
    def stop_before_get(*_args, **_kwargs):
        attempted.append(True)
        raise RuntimeError("synthetic stop")
    monkeypatch.setattr(session, "download_digital", stop_before_get)
    assert materialize(service, account, loan)["code"] == "bnf_auto_acquisition_failed"
    assert materialize(service, account, second)["code"] == "bnf_auto_acquisition_failed"
    assert attempted == [True, True] and not downloaded
    assert first.is_file()


@pytest.mark.parametrize("directory", ["manual", "acsm-import"])
def test_old_manual_catalogue_entry_blocks_even_without_manual_receipt(
    connected_core, tmp_path, monkeypatch, directory,
):
    service, session, account, loan, downloaded = _enable(connected_core)
    manual = tmp_path / "EBooks" / "hugues" / directory / "historical.epub"
    manual.parent.mkdir(parents=True, exist_ok=True)
    epub(manual, title=loan.title)
    service.storage.catalog_entry_put({
        "catalog_entry_id": "manual-index", "identity_id": "default",
        "provider_id": "local", "title": loan.title, "local_state": "local",
        "media_format": "epub", "metadata": {"path": str(manual)},
    })
    attempted = []
    monkeypatch.setattr(session, "download_digital", lambda *a, **k: attempted.append(True))
    assert materialize(service, account, loan)["code"] == "bnf_auto_acquisition_failed"
    assert attempted == [True] and downloaded == []
    assert manual.exists()


def test_source_sync_title_hint_does_not_require_manual_reconciliation(
    connected_core, tmp_path, monkeypatch,
):
    service, _session, account, loan, downloaded = _enable(connected_core)
    attempted = []
    def synthetic_stop(*_a, **_kw):
        attempted.append(True)
        raise RuntimeError("synthetic stop before HTTP")
    monkeypatch.setattr(_session, "download_digital", synthetic_stop)
    old = _manual_receipt(service, tmp_path, loan.title)
    sid = SynchronizationBrokerImpl._source_id(account)
    before = old.read_bytes()
    outcome = SynchronizationBrokerImpl(service).synchronize_source(sid)
    assert outcome.facts["status"] == "partial"
    assert outcome.facts["files_reconciliation_required"] == 0
    assert outcome.facts["reason"] == "source_partial"
    # No file was changed merely because the title raised a review candidate.
    assert str(old) not in repr(outcome.facts)
    assert loan.title not in repr(outcome.facts)
    assert downloaded == [] and attempted == [True]
    assert old.read_bytes() == before
