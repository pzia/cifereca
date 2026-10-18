"""Manual ACSM recovery preserves originals and never replays a published file."""
from pathlib import Path
import pytest
from cifereca.providers import ProviderConfigurationError
from tests.test_acsm_import_preservation import importer


def test_failure_before_publication_keeps_staging_for_explicit_retry(tmp_path, importer, monkeypatch):
    service, requests = importer
    source = tmp_path/'retry.acsm'
    original = b'<fulfillment><id>synthetic-retry</id><format>application/pdf</format></fulfillment>'
    source.write_bytes(original)
    runtime = service.execute_acquisition_request
    attempts = []
    def interrupted(request):
        attempts.append(True)
        if len(attempts) == 1: raise RuntimeError('synthetic interruption before artifact')
        return runtime(request)
    monkeypatch.setattr(service, 'execute_acquisition_request', interrupted)
    with pytest.raises(RuntimeError): service.import_acsm(source)
    rows = service.storage.acquisitions()
    assert len(rows) == 1 and rows[0]['status'] == 'retryable_error'
    staging = Path(rows[0]['metadata']['staging'])
    assert staging.read_bytes() == source.read_bytes() == original
    result = service.import_acsm(source)
    assert result['status'] == 'complete' and len(attempts) == 2 and len(requests) == 1
    assert service.import_acsm(source)['status'] == 'unchanged' and len(attempts) == 2
    assert source.read_bytes() == original and not staging.exists()


def test_failure_after_publication_does_not_fulfill_again(tmp_path, importer, monkeypatch):
    service, requests = importer
    source = tmp_path/'published.acsm'
    original = b'<fulfillment><id>synthetic-published</id><format>application/pdf</format></fulfillment>'
    source.write_bytes(original)
    def interrupted(*_a, **_kw): raise RuntimeError('synthetic interruption before index')
    monkeypatch.setattr(service, 'catalog_index_path', interrupted)
    with pytest.raises(RuntimeError): service.import_acsm(source)
    path = Path(requests[0]['destination'])
    published = path.read_bytes()
    staging = Path(service.storage.acquisitions()[0]['metadata']['staging'])
    with pytest.raises(ProviderConfigurationError, match='réconciliation'):
        service.import_acsm(source)
    assert len(requests) == 1 and not service.storage.artifacts()
    assert path.read_bytes() == published
    assert source.read_bytes() == staging.read_bytes() == original
