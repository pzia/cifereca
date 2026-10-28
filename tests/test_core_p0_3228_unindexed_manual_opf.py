"""P0 #3228: unindexed historical EPUB never becomes a phantom new BnF loan.

Real Core + SQLite, synthetic BnF HTTPX and EPUB; no external network/DRM.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.bnf_auto_materialization import materialize
from cifereca.core.local_physical_title_candidates import observe_physical_local_titles
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core
from tests.test_library_reconciliation import epub


def active(core):
    service, session, account, loan, _state, downloaded = core
    broker = SynchronizationBrokerImpl(service)
    assert broker.set_bnf_auto_download(broker._source_id(account), True)
    return service, session, account, loan, downloaded


def manual_epub(tmp_path, title, *, identity='hugues', directory='manual', filename='old.epub'):
    path = tmp_path / 'EBooks' / identity / directory / 'acsm-old' / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    epub(path, title=title)
    return path


@pytest.mark.parametrize('id_', ['provider-alpha', 'provider-beta'])
def test_provider_neutral_unindexed_epub_title_hint(tmp_path, id_):
    path = manual_epub(tmp_path, 'Same Book')
    original = path.read_bytes()
    root = path.parents[1]
    evidence = observe_physical_local_titles(
        identity_id=id_, roots=(root,),
    )
    assert evidence.complete
    candidate = evidence.screen(identity_id=id_, title='  SAME  book ')
    assert candidate.status == 'needs_reconciliation'
    assert candidate.reason == 'local_candidate_found'
    assert not candidate.acquisition_authorized
    assert evidence.screen(identity_id=id_, title='Different book').status == 'none_observed'
    assert evidence.screen(identity_id='different-identity', title='Same Book').status == 'needs_reconciliation'
    assert str(path) not in repr(evidence)
    assert path.read_bytes() == original


def test_missing_manual_root_does_not_disable_a_new_loan(tmp_path):
    evidence = observe_physical_local_titles(
        identity_id='A', roots=(tmp_path / 'EBooks' / 'A' / 'manual',),
    )
    assert evidence.complete
    assert evidence.screen(identity_id='A', title='New book').status == 'none_observed'


def test_symlinked_manual_root_is_inconclusive(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    epub(outside / 'book.epub', title='Book')
    root = tmp_path / 'EBooks' / 'A' / 'manual'
    root.parent.mkdir(parents=True)
    root.symlink_to(outside, target_is_directory=True)
    evidence = observe_physical_local_titles(identity_id='A', roots=(root,))
    assert not evidence.complete
    assert evidence.screen(identity_id='A', title='Book').reason == 'local_evidence_unavailable'


def test_invalid_epub_makes_scan_inconclusive_without_permission_to_download(tmp_path):
    root = tmp_path / 'A' / 'manual'
    root.mkdir(parents=True)
    (root / 'broken.epub').write_bytes(b'invalid EPUB')
    evidence = observe_physical_local_titles(identity_id='A', roots=(root,))
    assert not evidence.complete
    assert evidence.screen(identity_id='A', title='Book').status == 'needs_reconciliation'


@pytest.mark.parametrize('directory', ['manual', 'acsm-import'])
def test_unindexed_same_opf_title_is_a_hint_not_an_automatic_get_veto(
    connected_core, tmp_path, monkeypatch, directory,
):
    service, session, account, loan, downloaded = active(connected_core)
    path = manual_epub(tmp_path, loan.title, directory=directory)
    before_bytes = path.read_bytes()
    before_receipts = service.storage.acquisitions(provider_id='manual')
    before_rights = service.storage.entitlements()
    invoked = []
    monkeypatch.setattr(session, 'download_digital', lambda *_a, **_kw: invoked.append(True))
    for _ in range(2):
        assert materialize(service, account, loan) == {
            'status': 'attention_required',
            'code': 'bnf_auto_acquisition_failed',
        }
    assert invoked == [True, True] and downloaded == []
    assert path.read_bytes() == before_bytes
    assert service.storage.acquisitions(provider_id='manual') == before_receipts
    assert service.storage.entitlements() == before_rights


@pytest.mark.parametrize('directory', ['manual', 'acsm-import'])
def test_other_identity_same_opf_title_does_not_block_get(
    connected_core, tmp_path, monkeypatch, directory,
):
    service, session, account, loan, downloaded = active(connected_core)
    foreign = manual_epub(tmp_path, loan.title, identity='alex', directory=directory)
    invoked = []
    def no_network(*_a, **_kw):
        invoked.append(True)
        raise RuntimeError('synthetic stop before network')
    monkeypatch.setattr(session, 'download_digital', no_network)
    assert materialize(service, account, loan)['code'] == 'bnf_auto_acquisition_failed'
    assert invoked == [True] and downloaded == []
    assert foreign.exists()


def test_second_unrelated_loan_remains_eligible_without_false_reconciliation(
    connected_core, tmp_path, monkeypatch,
):
    service, session, account, loan, downloaded = active(connected_core)
    first = manual_epub(tmp_path, loan.title)
    invoked = []
    def no_network(*_a, **_kw):
        invoked.append(True)
        raise RuntimeError('synthetic stop before network')
    monkeypatch.setattr(session, 'download_digital', no_network)
    other = replace(loan, title='Another distinct book')
    cache = {}
    assert materialize(service, account, loan, physical_scan_cache=cache)['code'] == 'bnf_auto_acquisition_failed'
    assert materialize(service, account, other, physical_scan_cache=cache)['code'] == 'bnf_auto_acquisition_failed'
    assert len(cache) == 1 and invoked == [True, True] and not downloaded
    assert first.is_file()


@pytest.mark.parametrize('directory', ['manual', 'acsm-import'])
def test_unindexed_symlinked_root_never_triggers_signed_get(
    connected_core, tmp_path, monkeypatch, directory,
):
    service, session, account, loan, downloaded = active(connected_core)
    outside = tmp_path / 'outside'
    outside.mkdir()
    epub(outside / 'book.epub', title=loan.title)
    root = tmp_path / 'EBooks' / 'hugues' / directory
    root.parent.mkdir(parents=True, exist_ok=True)
    root.symlink_to(outside, target_is_directory=True)
    invoked = []
    monkeypatch.setattr(session, 'download_digital', lambda *_a, **_kw: invoked.append(True))
    assert materialize(service, account, loan)['code'] == 'bnf_local_evidence_unavailable'
    assert not invoked and not downloaded
    assert (outside / 'book.epub').exists()


def test_source_sync_unindexed_title_hint_no_longer_requires_manual_review(
    connected_core, tmp_path, monkeypatch,
):
    service, session, account, loan, downloaded = active(connected_core)
    path = manual_epub(tmp_path, loan.title)
    before = path.read_bytes()
    attempted = []

    def stop_before_signed_get(*_a, **_kw):
        attempted.append(True)
        raise RuntimeError("synthetic stop before HTTP/ACSM/ADEPT")

    monkeypatch.setattr(session, 'download_digital', stop_before_signed_get)
    result = SynchronizationBrokerImpl(service).synchronize_source(
        SynchronizationBrokerImpl._source_id(account),
    )
    assert result.facts['status'] == 'partial'
    assert result.facts['files_reconciliation_required'] == 0
    assert attempted == [True] and not downloaded
    assert path.read_bytes() == before
    assert not service.storage.acquisitions(provider_id='manual')
