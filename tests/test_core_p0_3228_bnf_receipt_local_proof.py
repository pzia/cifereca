"""P0 #3228: a claimed BnF auto receipt is not an unchanged local book.

All source/user/file fixtures are synthetic. No real HTTP, ADEPT or DRM.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import cifereca.services as facade
from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.bnf_auto_materialization import materialize
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core


PDF = b'%PDF-1.4\n% synthetic book\n%%EOF\n'


def _enable(service, account):
    broker = SynchronizationBrokerImpl(service)
    assert broker.set_bnf_auto_download(broker._source_id(account), True)


def _ref(account, loan):
    return hashlib.sha256('\0'.join((
        account.identity_id, account.credential_id,
        account.unique_id, loan.id, loan.record_id,
    )).encode()).hexdigest()


def _forge(service, account, loan, *, path: Path, placement_identity,
           placement_provider, placement=True, expected_bytes=PDF):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(expected_bytes)
    digest = hashlib.sha256(expected_bytes).hexdigest()
    artifact_id = 'sha256:' + digest
    service.storage.artifact_put({
        'artifact_id': artifact_id, 'sha256': digest,
        'size': len(expected_bytes), 'media_format': 'pdf',
        'mime_type': 'application/pdf',
    })
    if placement:
        service.storage.placement_put({
            'artifact_id': artifact_id, 'path': str(path),
            'identity_id': placement_identity,
            'provider_id': placement_provider, 'source_item_id': loan.id,
            'state': 'present',
        })
    service.storage.acquisition_put({
        'acquisition_id': 'manual-acsm:synthetic-bnf-auto',
        'identity_id': account.identity_id, 'provider_id': 'manual',
        'credential_id': 'manual', 'item_id': 'acsm-test', 'title': 'Private',
        'status': 'complete', 'artifact_id': artifact_id,
        'metadata': {'path': str(path), 'bnf_auto_ref': _ref(account, loan)},
    })


@pytest.mark.parametrize('mismatch', [
    'no_placement', 'wrong_identity', 'wrong_provider', 'outside_root',
])
def test_same_auto_ref_does_not_accept_other_file_or_placement(
    connected_core, tmp_path, monkeypatch, mismatch,
):
    service, session, account, loan, state, downloaded = connected_core
    _enable(service, account)
    target = (service.library_identity_provider_dir(account.identity_id, 'bnf-pret')
              / 'acsm-synthetic' / 'book.pdf')
    if mismatch == 'outside_root':
        target = tmp_path / 'foreign-profile' / 'book.pdf'
    _forge(
        service, account, loan, path=target,
        placement_identity=('alex' if mismatch == 'wrong_identity'
                            else account.identity_id),
        placement_provider=('manual' if mismatch == 'wrong_provider'
                            else 'bnf-pret'),
        placement=mismatch != 'no_placement',
    )
    called = []
    monkeypatch.setattr(
        session, 'download_digital',
        lambda *_a, **_kw: called.append(True)
        or (_ for _ in ()).throw(AssertionError('forbidden supplier GET')),
    )
    before = service.storage.acquisitions()
    result = materialize(service, account, loan)
    assert result == {
        'status': 'attention_required', 'code': 'bnf_local_artifact_invalid',
    }
    assert not called and not downloaded
    assert service.storage.acquisitions() == before
    assert target.read_bytes() == PDF


def test_changed_valid_pdf_after_complete_is_not_marked_unchanged(
    connected_core, tmp_path, monkeypatch,
):
    service, session, account, loan, state, downloaded = connected_core
    _enable(service, account)
    monkeypatch.setattr(facade, 'data_dir', lambda: tmp_path / 'data')
    monkeypatch.setattr(
        service, 'resolve_adept_profile_for_identity',
        lambda _: ('synthetic', 'identity'),
    )
    monkeypatch.setattr(service, '_originating_adept_profile_id', lambda *a: None)

    def fulfill(request):
        path = Path(request['destination'])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PDF)
        return {'ok': True, 'artifact': {'path': str(path)}}
    monkeypatch.setattr(service, 'execute_acquisition_request', fulfill)
    assert materialize(service, account, loan)['status'] == 'complete'
    assert len(downloaded) == 1
    row, = service.storage.acquisitions(provider_id='manual')
    produced = Path(row['metadata']['path'])
    assert produced.read_bytes() == PDF
    replacement = PDF.replace(b'synthetic', b'SYNTHETIC')
    assert len(replacement) == len(PDF)
    produced.write_bytes(replacement)
    assert produced.is_file()
    calls = []
    monkeypatch.setattr(
        session, 'download_digital',
        lambda *_a, **_kw: calls.append(True)
        or (_ for _ in ()).throw(AssertionError('repeat GET')),
    )
    result = materialize(service, account, loan)
    assert result == {
        'status': 'attention_required', 'code': 'bnf_local_artifact_invalid',
    }
    assert calls == [] and len(downloaded) == 1
    assert service.storage.acquisition_get(row['acquisition_id'])['status'] == 'complete'


def test_symlinked_parent_never_counts_as_verified_local_bnf(
    connected_core, tmp_path, monkeypatch,
):
    service, session, account, loan, state, downloaded = connected_core
    _enable(service, account)
    root = service.library_identity_provider_dir(account.identity_id, 'bnf-pret')
    other = tmp_path / 'other-data'
    other.mkdir()
    fake_parent = root / 'acsm-synthetic'
    fake_parent.symlink_to(other, target_is_directory=True)
    target = fake_parent / 'book.pdf'
    _forge(
        service, account, loan, path=target,
        placement_identity=account.identity_id, placement_provider='bnf-pret',
    )
    calls = []
    monkeypatch.setattr(
        session, 'download_digital', lambda *_a, **_kw: calls.append(True),
    )
    assert materialize(service, account, loan) == {
        'status': 'attention_required', 'code': 'bnf_local_artifact_invalid',
    }
    assert not calls and not downloaded
    assert target.read_bytes() == PDF
