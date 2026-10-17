"""Importing one ACSM must never replace another local document."""
from pathlib import Path

import pytest
import cifereca.services as facade
from tests.test_core043_progress_cache_stats import _service


@pytest.fixture
def importer(tmp_path, monkeypatch):
    service = _service(tmp_path)
    monkeypatch.setattr(facade, 'data_dir', lambda: tmp_path / 'data')
    monkeypatch.setattr(service, 'resolve_adept_profile_for_identity', lambda identity: ('synthetic', 'identity'))
    monkeypatch.setattr(service, '_originating_adept_profile_id', lambda *a: None)
    requests = []
    def fulfill(request):
        requests.append(request)
        path = Path(request['destination'])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'%PDF-1.4\n% synthetic document ' + str(len(requests)).encode() + b'\n%%EOF\n')
        return {'ok': True, 'artifact': {'path': str(path)}}
    monkeypatch.setattr(service, 'execute_acquisition_request', fulfill)
    yield service, requests
    service.close()


def test_distinct_homonymous_acsms_preserve_both_artifacts(tmp_path, importer):
    service, requests = importer
    first_source = tmp_path / 'first.acsm'
    second_source = tmp_path / 'second.acsm'
    first_source.write_text('<fulfillment><id>one</id><format>application/pdf</format></fulfillment>')
    second_source.write_text('<fulfillment><id>two</id><format>application/pdf</format></fulfillment>')
    first = service.import_acsm(first_source, title='Même titre')
    original = Path(first['path']).read_bytes()
    second = service.import_acsm(second_source, title='Même titre')
    assert Path(first['path']).read_bytes() == original
    assert first['path'] != second['path']
    assert len(service.storage.artifacts()) == 2
    assert service.import_acsm(first_source, title='Même titre')['status'] == 'unchanged'
    assert len(requests) == 2
    assert first_source.is_file() and second_source.is_file()


def test_inferred_extension_correction_preserves_existing_pdf(tmp_path, importer):
    service, requests = importer
    source = tmp_path / 'unknown-format.acsm'
    source.write_text('<fulfillment><id>unknown</id></fulfillment>')
    root = service.library_identity_provider_dir(service.current_identity.identity_id, 'manual')
    existing = root / 'Même titre.pdf'
    existing.write_bytes(b'%PDF-1.4\n% already present\n%%EOF\n')
    original = existing.read_bytes()
    imported = service.import_acsm(source, title='Même titre')
    assert existing.read_bytes() == original
    assert Path(imported['path']) != existing
    assert Path(imported['path']).suffix == '.pdf'


def test_unindexed_residue_blocks_before_another_drm_call(tmp_path, importer):
    service, requests = importer
    source = tmp_path / 'residue.acsm'
    source.write_text('<fulfillment><id>residue</id></fulfillment>')
    from cifereca.library_files import sha256_file
    root = service.library_identity_provider_dir(service.current_identity.identity_id, 'manual')
    area = root / ('acsm-' + sha256_file(source))
    area.mkdir()
    residue = area / 'Titre.pdf'
    residue.write_bytes(b'preserved partial output')
    from cifereca.providers import ProviderConfigurationError
    with pytest.raises(ProviderConfigurationError, match='réconciliation'):
        service.import_acsm(source, title='Titre')
    assert residue.read_bytes() == b'preserved partial output'
    assert not requests


def test_existing_corrected_target_created_during_runtime_is_never_deleted(tmp_path, importer, monkeypatch):
    service, requests = importer
    source = tmp_path / 'race.acsm'
    source.write_text('<fulfillment><id>race</id></fulfillment>')
    original_runtime = service.execute_acquisition_request
    def runtime(request):
        result = original_runtime(request)
        Path(request['destination']).with_suffix('.pdf').write_bytes(b'preserve competing artifact')
        return result
    monkeypatch.setattr(service, 'execute_acquisition_request', runtime)
    with pytest.raises(FileExistsError):
        service.import_acsm(source, title='Titre')
    destination = Path(requests[0]['destination'])
    assert destination.with_suffix('.pdf').read_bytes() == b'preserve competing artifact'
    assert destination.is_file() and source.is_file()


def test_same_acsm_concurrent_import_is_rejected_without_second_runtime(tmp_path, importer, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from cifereca.providers import ProviderConfigurationError
    service, requests = importer
    source = tmp_path / 'concurrent.acsm'
    source.write_text('<fulfillment><id>concurrent</id></fulfillment>')
    entered, release = Event(), Event()
    original_runtime = service.execute_acquisition_request
    def runtime(request):
        entered.set()
        assert release.wait(5)
        return original_runtime(request)
    monkeypatch.setattr(service, 'execute_acquisition_request', runtime)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.import_acsm, source, title='Titre')
        try:
            assert entered.wait(5)
            with pytest.raises(ProviderConfigurationError, match='déjà en cours'):
                service.import_acsm(source, title='Titre')
        finally:
            release.set()
        result = future.result(timeout=5)
    assert result['status'] == 'complete' and len(requests) == 1
    assert service.import_acsm(source, title='Titre')['status'] == 'unchanged'


def test_symlinked_import_namespace_is_rejected_before_runtime(tmp_path, importer):
    from cifereca.library_files import sha256_file
    from cifereca.providers import ProviderConfigurationError
    service, requests = importer
    source = tmp_path / 'symlink.acsm'
    source.write_text('<fulfillment><id>symlink</id></fulfillment>')
    root = service.library_identity_provider_dir(service.current_identity.identity_id, 'manual')
    outside = tmp_path / 'other-books'
    outside.mkdir()
    (root / ('acsm-' + sha256_file(source))).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProviderConfigurationError, match='ambigu'):
        service.import_acsm(source, title='Titre')
    assert list(outside.iterdir()) == [] and not requests


def test_runtime_output_symlink_is_not_indexed_or_removed(tmp_path, importer, monkeypatch):
    from cifereca.providers import ProviderConfigurationError
    service, requests = importer
    source = tmp_path / 'output-symlink.acsm'
    source.write_text('<fulfillment><id>output-symlink</id></fulfillment>')
    existing = tmp_path / 'other.pdf'
    existing.write_bytes(b'%PDF-1.4\n% other book\n%%EOF\n')
    def runtime(request):
        requests.append(request)
        destination = Path(request['destination'])
        destination.symlink_to(existing)
        return {'ok': True, 'artifact': {'path': str(destination)}}
    monkeypatch.setattr(service, 'execute_acquisition_request', runtime)
    with pytest.raises(ProviderConfigurationError, match='inattendu'):
        service.import_acsm(source, title='Titre')
    assert existing.read_bytes() == b'%PDF-1.4\n% other book\n%%EOF\n'
    assert Path(requests[0]['destination']).is_symlink()
    assert not service.storage.artifacts()
