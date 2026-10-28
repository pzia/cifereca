"""#3228: typography differences must not cause a second BnF ADEPT download.

Local candidate titles are low-trust *review* hints, not supplier evidence.
All reads use synthetic BnF sessions and EPUBs; no external HTTP/DRM.
"""
from __future__ import annotations

from dataclasses import replace

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.bnf_auto_materialization import materialize
from cifereca.core.local_candidate_screen import (
    _title, screen_local_candidates,
)
from cifereca.core.local_physical_title_candidates import observe_physical_local_titles
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core
from tests.test_library_reconciliation import epub


_LOAN_TITLE = "L'ÎLE — secrète"
_MANUAL_TITLE = "L’ile - secrete"


def _ready(connected_core):
    service, session, account, loan, _state, downloaded = connected_core
    broker = SynchronizationBrokerImpl(service)
    assert broker.set_bnf_auto_download(broker._source_id(account), True)
    return service, session, account, replace(loan, title=_LOAN_TITLE), downloaded


def test_bibliographic_review_title_normalization_is_exact_tokens_only(tmp_path):
    root = tmp_path / "reader" / "manual"
    path = root / "old.epub"
    assert _title(_LOAN_TITLE) == _title(_MANUAL_TITLE) == "l ile secrete"
    assert _title("La mer, le vent") == _title("La mer - le VENT")
    assert _title("L'île secrète - volume 2") != _title("L'île secrète")
    assert _title("Île") != _title("Il")
    assert _title("    ") == ""
    original = {
        "identity_id": "reader", "title": _MANUAL_TITLE,
        "status": "complete", "metadata": {"path": str(path)},
    }
    hint = screen_local_candidates(
        identity_id="reader", title=_LOAN_TITLE, roots=(root,),
        acquisitions=[original], catalogue=[],
    )
    assert hint.status == "needs_reconciliation"
    assert hint.reason == "local_candidate_found"
    assert not hint.acquisition_authorized
    assert screen_local_candidates(
        identity_id="reader", title=_LOAN_TITLE + " volume 2", roots=(root,),
        acquisitions=[original], catalogue=[],
    ).status == "none_observed"


def test_bnf_indexed_manual_typographic_title_does_not_veto_signed_get(
    connected_core, tmp_path, monkeypatch,
):
    service, session, account, loan, downloaded = _ready(connected_core)
    path = tmp_path / "EBooks" / "hugues" / "manual" / "old" / "book.epub"
    path.parent.mkdir(parents=True, exist_ok=True)
    epub(path, title=_MANUAL_TITLE)
    service.storage.acquisition_put({
        "acquisition_id": "old-manual-ref",
        "identity_id": account.identity_id, "provider_id": "manual",
        "credential_id": "manual", "item_id": "old-acsm",
        "title": _MANUAL_TITLE, "status": "complete",
        "metadata": {"path": str(path)},
    })
    before = path.read_bytes()
    attempts = []
    monkeypatch.setattr(
        session, "download_digital",
        lambda *_a, **_kw: attempts.append("would-get"),
    )
    for _ in range(2):
        outcome = materialize(service, account, loan)
        assert outcome == {
            "status": "attention_required",
            "code": "bnf_auto_acquisition_failed",
        }
    assert attempts == ["would-get", "would-get"] and not downloaded
    assert path.read_bytes() == before


def test_bnf_unindexed_opf_typographic_title_does_not_veto_signed_get(
    connected_core, tmp_path, monkeypatch,
):
    service, session, account, loan, downloaded = _ready(connected_core)
    path = tmp_path / "EBooks" / "hugues" / "acsm-import" / "old" / "book.epub"
    path.parent.mkdir(parents=True, exist_ok=True)
    epub(path, title=_MANUAL_TITLE)
    evidence = observe_physical_local_titles(
        identity_id=account.identity_id, roots=(path.parents[1],),
    )
    assert evidence.complete
    assert evidence.screen(
        identity_id=account.identity_id, title=_LOAN_TITLE,
    ).status == "needs_reconciliation"
    attempts = []
    monkeypatch.setattr(
        session, "download_digital",
        lambda *_a, **_kw: attempts.append("would-get"),
    )
    for _ in range(2):
        assert materialize(service, account, loan)["code"] == (
            "bnf_auto_acquisition_failed"
        )
    assert attempts == ["would-get", "would-get"] and not downloaded


def test_distinct_extra_volume_remains_eligible_without_reusing_old_title(
    connected_core, tmp_path, monkeypatch,
):
    service, session, account, loan, downloaded = _ready(connected_core)
    path = tmp_path / "EBooks" / "hugues" / "manual" / "old" / "book.epub"
    path.parent.mkdir(parents=True, exist_ok=True)
    epub(path, title=_MANUAL_TITLE)
    attempts = []

    def stop_before_http(*_args, **_kwargs):
        attempts.append(True)
        raise RuntimeError("synthetic stop")

    monkeypatch.setattr(session, "download_digital", stop_before_http)
    distinct = replace(loan, title=_LOAN_TITLE + " - tome 2")
    outcome = materialize(service, account, distinct)
    assert outcome["code"] == "bnf_auto_acquisition_failed"
    assert attempts == [True] and not downloaded
    assert path.is_file()
