"""P0 #3228: older manual books are reconciliation candidates, not new loans.

Every scenario is local/synthetic. No BnF/Adobe network or private fixture.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.bnf_auto_materialization import materialize
from cifereca.core.local_candidate_screen import screen_local_candidates
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core
from tests.test_library_reconciliation import epub


@pytest.mark.parametrize("upstream", ["synthetic-alpha", "synthetic-beta"])
def test_generic_candidate_screen_treats_two_sources_identically(tmp_path, upstream):
    root = tmp_path / "EBooks" / "reader" / "manual"
    path = root / "acsm-old" / "Old Book.epub"
    record = {
        "provider_id": upstream, "identity_id": "identity-A",
        "title": "  OLD   BOOK  ", "status": "complete",
        "metadata": {"path": str(path)},
    }
    outcome = screen_local_candidates(
        identity_id="identity-A", title="old book", roots=(root,),
        acquisitions=[record], catalogue=[],
    )
    assert outcome.status == "needs_reconciliation"
    assert outcome.reason == "local_candidate_found"
    assert outcome.read_only and not outcome.acquisition_authorized
    assert str(path) not in repr(outcome)
    for identity, title in [("identity-B", "Old Book"), ("identity-A", "Other Book")]:
        different = screen_local_candidates(
            identity_id=identity, title=title, roots=(root,),
            acquisitions=[record], catalogue=[],
        )
        assert different.status == "none_observed"
        assert not different.acquisition_authorized


def test_generic_screen_recognizes_indexed_manual_book_without_import_receipt(tmp_path):
    root = tmp_path / "identity" / "manual"
    target = root / "legacy" / "same.epub"
    result = screen_local_candidates(
        identity_id="identity-A", title="Livre du prêt", roots=(root,),
        acquisitions=[], catalogue=[{
            "identity_id": "identity-A", "title": "Livre du prêt",
            "local_state": "local", "metadata": {"path": str(target)},
        }],
    )
    assert result.status == "needs_reconciliation"
    assert result.reason == "local_candidate_found"


@pytest.mark.parametrize("bad_path", [
    "/another-identity/manual/book.epub",
    "/invalid-root/../identity/manual/book.epub",
    "/identity/manual/book.acsm",
    "relative/book.epub",
])
def test_generic_screen_does_not_adopt_foreign_or_ambiguous_path(bad_path):
    result = screen_local_candidates(
        identity_id="A", title="Book", roots=(Path("/identity/manual"),),
        acquisitions=[{
            "identity_id": "A", "title": "Book", "status": "complete",
            "metadata": {"path": bad_path},
        }], catalogue=[],
    )
    assert result.status == "none_observed"
    assert result.acquisition_authorized is False


@pytest.mark.parametrize("replacement", ["identity", "title", "path"])
def test_generic_screen_never_inferrs_ownership_from_title_or_cross_identity(
    tmp_path, replacement,
):
    root = tmp_path / "identity-A" / "manual"
    record = {
        "identity_id": "identity-A", "title": "Book", "status": "complete",
        "metadata": {"path": str(root / "book.epub")},
    }
    if replacement == "identity":
        record["identity_id"] = "identity-B"
    elif replacement == "title":
        record["title"] = "Other edition"
    else:
        record["metadata"]["path"] = str(tmp_path / "identity-B" / "manual" / "book.epub")
    result = screen_local_candidates(
        identity_id="identity-A", title="Book", roots=(root,),
        acquisitions=[record], catalogue=[],
    )
    assert result.status == "none_observed"
    assert not result.acquisition_authorized


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
