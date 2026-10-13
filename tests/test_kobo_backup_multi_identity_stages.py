"""Cached artifacts stay scoped and partial manifests never authorize pruning."""
from dataclasses import replace

import pytest

from cifereca.kobo.vault import KoboVaultError
from cifereca.providers import ProviderConfigurationError
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
        # Credential vault-1 has a complete authoritative manifest despite
        # vault-2 failing: only the successful credential may be pruned.
        assert failed['legacy_pruned'] == 1 and failed['attempted'] == 0
        assert service.storage.metadata_get('kobo_backup_last_success_at') is None
        assert service.storage.backup_get('kobo', 'vault-1', 'stale') is None
        assert service.storage.backup_get('kobo', 'vault-2', 'stale') is not None
        second._manifest_error = None
        recovered = service.sync_kobo_backup()
        assert recovered['errors'] == 0 and recovered['unchanged'] == 2
        assert recovered['attempted'] == 0 and recovered['legacy_pruned'] == 1
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


def test_explicit_account_sync_does_not_touch_other_credential_or_global_cache(tmp_path, monkeypatch):
    service = _service(tmp_path, manifest=[_manifest_row()])
    first = service._sessions[service._session_key("kobo", "vault-1")]
    other_identity = replace(service.identities[0], identity_id="second", label="Second", directory_name="second")
    service.identities.append(other_identity)
    service.storage.identity_put(other_identity)
    service.storage.provider_connection_put({
        "identity_id": "second", "provider_id": "kobo",
        "credential_id": "vault-2", "label": "Second",
    })
    other = BackupSession(
        descriptor=first.descriptor, credential_id="vault-2", label="Second",
        credentials={}, options={}, manifest=[_manifest_row(item_id="other-book")],
    )
    other.connect()
    service._sessions[service._session_key("kobo", "vault-2")] = other
    monkeypatch.setattr(other, "backup_manifest", lambda: pytest.fail("another credential must not be fetched"))
    monkeypatch.setattr(service, "_execute_kobo_acquisition", lambda *_a, **_kw: pytest.fail("no file may download"))
    saved = service._kobo_backup_target_dir("default") / "saved.epub"
    _write_epub(saved)
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "item_id": "book-1",
        "title": "Book One", "author": "Author", "format": "EPUB",
        "revision_id": "rev-1", "status": "complete", "local_path": str(saved),
    })
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-2",
        "identity_id": "second", "item_id": "other-book",
        "title": "Other", "format": "EPUB", "status": "acquiring",
    })
    service.storage.metadata_set("kobo_backup_last_success_at", 100)
    service.storage.metadata_set("kobo_backup_last_attempt_at", 101)
    service.storage.metadata_set("kobo_backup_last_batch_status", "partial")
    try:
        summary = service.sync_kobo_backup(credential_id="vault-1")
        assert summary["scope"] == "account"
        assert summary["sessions"] == 1
        assert summary["unchanged"] == 1
        assert summary["attempted"] == 0
        assert summary["errors"] == 0
        assert len(summary["sources"]) == 1
        assert service.storage.backup_get("kobo", "vault-2", "other-book")["status"] == "acquiring"
        assert service.storage.metadata_get("kobo_backup_last_success_at") == "100"
        assert service.storage.metadata_get("kobo_backup_last_attempt_at") == "101"
        assert service.storage.metadata_get("kobo_backup_last_batch_status") == "partial"
    finally:
        service.close()


def test_explicit_account_sync_rejects_unknown_credential(tmp_path):
    service = _service(tmp_path, manifest=[])
    try:
        with pytest.raises(ProviderConfigurationError, match="Compte Kobo"):
            service.sync_kobo_backup(credential_id="not-connected")
        assert service.storage.metadata_get("kobo_backup_last_success_at") is None
    finally:
        service.close()


def test_malformed_kobo_manifest_does_not_prune_historical_rows(tmp_path, monkeypatch):
    service = _service(
        tmp_path, manifest=[_manifest_row(), {"title": "entry without product id"}],
    )
    path = service._kobo_backup_target_dir("default") / "already-here.epub"
    _write_epub(path)
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "item_id": "book-1",
        "revision_id": "rev-1", "title": "Book One",
        "format": "EPUB", "status": "complete", "local_path": str(path),
    })
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "item_id": "historical",
        "title": "Historical", "status": "pending",
    })
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("existing Kobo file must not redownload"),
    )
    try:
        result = service.sync_kobo_backup(credential_id="vault-1")
        assert result["errors"] == 0
        assert result["skipped"] == 1
        assert result["malformed_manifest_items"] == 1
        assert result["status"] == "partial"
        assert result["sources"][0]["status"] == "partial"
        assert result["legacy_pruned"] == 0
        assert service.storage.metadata_get("kobo_backup_last_success_at") is None
        assert service.storage.backup_get("kobo", "vault-1", "historical") is not None
        assert path.exists()
    finally:
        service.close()
