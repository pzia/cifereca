"""Cached artifacts stay scoped and partial manifests never authorize pruning."""
from dataclasses import replace

import pytest
import json

from cifereca.core.kobo_backup import _kobo_checkpoint_key

from cifereca.kobo.vault import KoboVaultError
from tests.test_kobo_backup_reconcile_prune import _service, _manifest_row, _write_epub, BackupSession


def test_two_identity_cache_partial_recovery_is_idempotent_without_download(tmp_path, monkeypatch):
    service = _service(tmp_path, manifest=[_manifest_row()])
    first = service._sessions[service._session_key('kobo', 'vault-1')]
    second_identity = replace(service.identities[0], identity_id='alex', label='Alex', directory_name='alex')
    service.identities.append(second_identity)
    service.storage.identity_put(second_identity)
    service.storage.provider_connection_put({'identity_id': 'alex', 'provider_id': 'kobo',
                                            'credential_id': 'vault-2', 'label': 'Second'})
    second = BackupSession(descriptor=first.descriptor, credential_id='vault-2', label='Second',
                           credentials={}, options={}, manifest=[_manifest_row(title='Second book')],
                           manifest_error=KoboVaultError('temporary', code='temporary', status_code=503))
    second.connect()
    service._sessions[service._session_key('kobo', 'vault-2')] = second
    paths = []
    for identity, credential in [('default', 'vault-1'), ('alex', 'vault-2')]:
        path = service._kobo_backup_target_dir(identity) / 'cached.epub'
        _write_epub(path, marker=identity.encode())
        paths.append(path)
        service.storage.backup_put({'provider_id': 'kobo', 'credential_id': credential,
                                    'identity_id': identity, 'item_id': 'book-1', 'revision_id': 'rev-1',
                                    'title': 'Cached', 'format': 'EPUB', 'status': 'complete', 'local_path': str(path)})
        service.storage.backup_put({'provider_id': 'kobo', 'credential_id': credential,
                                    'identity_id': identity, 'item_id': 'stale', 'title': 'Stale', 'status': 'pending'})
    original = [p.read_bytes() for p in paths]
    monkeypatch.setattr(service, '_execute_kobo_acquisition', lambda *_a, **_kw: pytest.fail('cache must not download'))
    try:
        failed = service.sync_kobo_backup()
        assert failed['errors'] == 1 and failed['unchanged'] == 1
        assert failed['legacy_pruned'] == 0 and failed['attempted'] == 0
        by_credential = {row["credential_id"]: row for row in failed["sources"]}
        assert by_credential["vault-1"]["status"] == "complete"
        assert by_credential["vault-1"]["unchanged"] == 1
        assert by_credential["vault-2"]["status"] == "failed"
        assert by_credential["vault-2"]["errors"] == 1
        checkpoint_first = json.loads(service.storage.metadata_get(
            _kobo_checkpoint_key("default", "vault-1")
        ))
        checkpoint_second = json.loads(service.storage.metadata_get(
            _kobo_checkpoint_key("alex", "vault-2")
        ))
        assert checkpoint_first["status"] == "complete" and checkpoint_first["processed"] == 1
        assert checkpoint_second["status"] == "failed" and checkpoint_second["processed"] == 0
        assert service.storage.metadata_get('kobo_backup_last_success_at') is None
        for credential in ['vault-1', 'vault-2']:
            assert service.storage.backup_get('kobo', credential, 'stale') is not None
        second._manifest_error = None
        recovered = service.sync_kobo_backup()
        assert recovered['errors'] == 0 and recovered['unchanged'] == 2
        assert recovered['attempted'] == 0 and recovered['legacy_pruned'] == 2
        assert {row["credential_id"]: row["status"] for row in recovered["sources"]} == {
            "vault-1": "complete", "vault-2": "complete"
        }
        assert json.loads(service.storage.metadata_get(
            _kobo_checkpoint_key("alex", "vault-2")
        ))["processed"] == 1
        assert service.storage.metadata_get('kobo_backup_last_success_at') is not None
        repeated = service.sync_kobo_backup()
        assert repeated['errors'] == 0 and repeated['unchanged'] == 2
        assert repeated['attempted'] == 0 and repeated['legacy_pruned'] == 0
        assert [p.read_bytes() for p in paths] == original
        for identity, credential, path in zip(['default', 'alex'], ['vault-1', 'vault-2'], paths):
            row = service.storage.backup_get('kobo', credential, 'book-1')
            assert row['identity_id'] == identity and row['local_path'] == str(path)
            assert row['status'] == 'complete'
    finally:
        service.close()
