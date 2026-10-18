"""ADEPT publication keeps the existing path/content and replacement boundary."""
from pathlib import Path
from types import SimpleNamespace as NS
import uuid
import zipfile

import pytest
from cifereca.artifact_inspection import inspect_artifact
from cifereca.core import adept_acsm_plan
from cifereca.core.adept_artifact_publication import publish
from cifereca.services import ProviderConfigurationError
from tests.test_artifact_inspection import _write_epub


def _publish(staged, destination, previous, *, result=None, inspector=inspect_artifact, repair=True):
    return publish(result=result or {'artifact': {'path': str(staged)}},
                   adept_destination=staged, acsm_format='epub', loan=NS(id='synthetic-loan'),
                   repair_mode=repair, destination=destination, previous_path=previous,
                   Path=Path, ProviderConfigurationError=ProviderConfigurationError,
                   inspect_artifact=inspector, adept_acsm_plan=adept_acsm_plan,
                   uuid=uuid, _TRACE_LOG=NS(info=lambda *a: None))


@pytest.mark.parametrize('failure', ['invalid', 'protected', 'second_inspection'])
def test_validation_refusal_preserves_previous_and_destination(tmp_path, failure):
    previous = tmp_path / 'previous.epub'
    destination = tmp_path / 'destination.epub'
    staged = tmp_path / 'staged.epub'
    previous.write_bytes(b'previous loan')
    destination.write_bytes(b'existing destination')
    if failure == 'invalid':
        staged.write_bytes(b'invalid staged data')
    else:
        _write_epub(staged)
        if failure == 'protected':
            with zipfile.ZipFile(staged, 'a') as z:
                z.writestr('META-INF/encryption.xml', '<EncryptedData Algorithm="aes"/>')
    staged_bytes = staged.read_bytes()
    calls = []
    def inspect(path, expected=None):
        calls.append((path, expected))
        if failure == 'second_inspection' and expected:
            return NS(valid=False, reason='synthetic second inspection refusal')
        return inspect_artifact(path, expected)
    with pytest.raises(ProviderConfigurationError):
        _publish(staged, destination, previous, inspector=inspect)
    assert previous.read_bytes() == b'previous loan'
    assert destination.read_bytes() == b'existing destination'
    assert staged.read_bytes() == staged_bytes
    expected_calls = [(staged, None)] if failure == 'invalid' else [(staged, None), (staged, 'epub')]
    assert calls == expected_calls


def test_unexpected_runtime_path_is_rejected_before_inspection(tmp_path):
    staged = tmp_path / 'expected.epub'
    outside = tmp_path / 'other.epub'
    previous = tmp_path / 'previous.epub'
    for path in (staged, outside, previous): path.write_bytes(path.name.encode())
    before = {p: p.read_bytes() for p in (staged, outside, previous)}
    def forbidden(*a): pytest.fail('unexpected runtime path must not be inspected')
    with pytest.raises(ProviderConfigurationError, match='inattendu'):
        _publish(staged, tmp_path / 'destination.epub', previous,
                 result={'artifact': {'path': str(outside)}}, inspector=forbidden)
    assert {p: p.read_bytes() for p in before} == before
    assert not (tmp_path / 'destination.epub').exists()


@pytest.mark.parametrize('repair', [False, True])
def test_actual_pdf_format_and_hook_survive_publication(tmp_path, repair):
    staged = tmp_path / 'staged.epub'
    destination = tmp_path / 'book.epub'
    previous = tmp_path / 'previous.epub'
    previous.write_bytes(b'previous loan')
    pdf = b'%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n'
    staged.write_bytes(pdf)
    hook = {'ok': True}
    path, drm, format_, protected, reported_hook = _publish(
        staged, destination, previous, repair=repair,
        result={'artifact': {'path': str(staged), 'format': 'epub', 'drm': 'none'}, 'hook': hook})
    assert path == tmp_path / 'book.pdf'
    assert path.read_bytes() == pdf
    assert not staged.exists() and not (tmp_path / 'staged.pdf').exists()
    assert format_ == 'pdf' and drm == 'none' and protected is None
    assert reported_hook is hook
    if repair:
        assert not previous.exists()
    else:
        assert previous.read_bytes() == b'previous loan'
