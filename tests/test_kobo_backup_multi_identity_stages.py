"""Cached artifacts stay scoped and partial manifests never authorize pruning."""
from dataclasses import replace

import pytest
import json
import time

from cifereca.core.kobo_backup import _kobo_checkpoint_key
from cifereca.providers import ProviderConfigurationError

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
        assert failed['status'] == 'partial'
        assert [(row['credential_id'], row['status']) for row in failed['sources']] == [
            ('vault-1', 'complete'), ('vault-2', 'failed'),
        ]
        assert failed['legacy_pruned'] == 1 and failed['attempted'] == 0
        assert service.storage.metadata_get('kobo_backup_last_success_at') is None
        assert service.storage.backup_get('kobo', 'vault-1', 'stale') is None
        assert service.storage.backup_get('kobo', 'vault-2', 'stale') is not None
        assert json.loads(service.storage.metadata_get(
            _kobo_checkpoint_key('default', 'vault-1')
        ))['status'] == 'complete'
        assert json.loads(service.storage.metadata_get(
            _kobo_checkpoint_key('alex', 'vault-2')
        ))['status'] == 'failed'
        second._manifest_error = None
        recovered = service.sync_kobo_backup()
        assert recovered['errors'] == 0 and recovered['unchanged'] == 2
        assert recovered['status'] == 'complete'
        assert {row['status'] for row in recovered['sources']} == {'complete'}
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


def test_scoped_kobo_backup_excludes_other_identity_and_preserves_global_cache(tmp_path, monkeypatch):
    service = _service(tmp_path, manifest=[_manifest_row()])
    first = service._sessions[service._session_key("kobo", "vault-1")]
    second_identity = replace(
        service.identities[0], identity_id="second", label="Second", directory_name="second"
    )
    service.identities.append(second_identity)
    service.storage.identity_put(second_identity)
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
    monkeypatch.setattr(
        other, "backup_manifest",
        lambda: pytest.fail("another credential manifest must not be fetched"),
    )
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("no local file may be redownloaded"),
    )
    path = service._kobo_backup_target_dir("default") / "existing.epub"
    _write_epub(path)
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "item_id": "book-1",
        "title": "Book One", "author": "Author", "format": "EPUB",
        "revision_id": "rev-1", "status": "complete", "local_path": str(path),
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
        result = service.sync_kobo_backup_account("vault-1")
        assert result["scope"] == "account"
        assert result["sessions"] == 1
        assert result["unchanged"] == 1
        assert result["attempted"] == 0
        assert result["errors"] == 0
        assert len(result["sources"]) == 1
        assert service.storage.backup_get("kobo", "vault-2", "other-book")["status"] == "acquiring"
        assert service.storage.metadata_get("kobo_backup_last_success_at") == "100"
        assert service.storage.metadata_get("kobo_backup_last_attempt_at") == "101"
        assert service.storage.metadata_get("kobo_backup_last_batch_status") == "partial"
        assert path.is_file()
    finally:
        service.close()


def test_unknown_kobo_credential_rejected_without_transfer(tmp_path):
    service = _service(tmp_path, manifest=[])
    try:
        with pytest.raises(ProviderConfigurationError, match="Compte Kobo"):
            service.sync_kobo_backup_account("not-connected")
    finally:
        service.close()


def test_malformed_kobo_manifest_never_prunes_old_rights(tmp_path, monkeypatch):
    service = _service(
        tmp_path, manifest=[_manifest_row(), {"title": "invalid item without ID"}],
    )
    path = service._kobo_backup_target_dir("default") / "saved.epub"
    _write_epub(path)
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "item_id": "book-1", "revision_id": "rev-1",
        "title": "Book One", "format": "EPUB",
        "status": "complete", "local_path": str(path),
    })
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "item_id": "historical",
        "title": "Old", "status": "pending",
    })
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("must not download during malformed manifest"),
    )
    try:
        result = service.sync_kobo_backup_account("vault-1")
        assert result["status"] == "partial"
        assert result["errors"] == 0
        assert result["malformed_manifest_items"] == 1
        assert result["skipped"] == 1
        assert result["legacy_pruned"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "historical") is not None
        assert path.is_file()
    finally:
        service.close()


def test_due_source_uses_v1_checkpoint_and_does_not_inherit_other_account_cache(tmp_path):
    service = _service(tmp_path, manifest=[])
    try:
        now = int(time.time())
        key = _kobo_checkpoint_key("default", "vault-1")
        service.storage.metadata_set("kobo_backup_last_success_at", now)
        service.storage.metadata_set(
            key, json.dumps({"schema_version": 1, "updated_at": now - 7200,
                             "status": "partial"}),
        )
        assert service._kobo_backup_account_due("vault-1") is True
        service.storage.metadata_set(
            key, json.dumps({"schema_version": 1, "updated_at": now,
                             "status": "partial"}),
        )
        assert service._kobo_backup_account_due("vault-1") is False
        service.storage.metadata_set(key, json.dumps({
            "schema_version": 1, "updated_at": now - 7200,
            "status": "attention_required",
        }))
        assert service._kobo_backup_account_due("vault-1") is False
        # An account without a checkpoint must be selected even when a
        # different source's cache timestamp is fresh.
        other_identity = replace(service.identities[0], identity_id="next",
                                 label="Next", directory_name="next")
        service.identities.append(other_identity)
        service.storage.identity_put(other_identity)
        service.storage.provider_connection_put({
            "identity_id": "next", "provider_id": "kobo",
            "credential_id": "vault-next", "label": "Next",
        })
        first = service._sessions[service._session_key("kobo", "vault-1")]
        other = BackupSession(
            descriptor=first.descriptor, credential_id="vault-next", label="Next",
            credentials={}, options={}, manifest=[],
        )
        other.connect()
        service._sessions[service._session_key("kobo", "vault-next")] = other
        assert service._kobo_backup_account_due("vault-next") is True
    finally:
        service.close()


def test_checkpoint_diagnostic_excludes_raw_book_and_credential(tmp_path):
    service = _service(tmp_path, manifest=[])
    try:
        key = _kobo_checkpoint_key("default", "vault-1")
        service.storage.metadata_set(key, json.dumps({
            "schema_version": 1, "total": 3, "processed": 2,
            "status": "attention_required", "updated_at": int(time.time()),
            "last_item_digest": "abcd1234", "counts": {"pending": 2},
        }))
        report = service.kobo_backup_diagnostic()
        assert len(report["sources"]) == 1
        assert report["sources"][0]["status"] == "attention_required"
        assert report["sources"][0]["pending"] == 2
        assert "vault-1" not in json.dumps(report["sources"])
        assert "item_id" not in json.dumps(report["sources"])
    finally:
        service.close()


@pytest.mark.parametrize("checkpoint_state", ["running", "interrupted", "manifest_fetch"])
def test_incomplete_checkpoint_is_due_even_when_recent(tmp_path, checkpoint_state):
    """A crash must not delay safe recovery by a full cache TTL."""
    service = _service(tmp_path, manifest=[])
    try:
        now = int(time.time())
        key = _kobo_checkpoint_key("default", "vault-1")
        service.storage.metadata_set("kobo_backup_last_attempt_at", now)
        service.storage.metadata_set("kobo_backup_last_success_at", now)
        service.storage.metadata_set(
            key, json.dumps({
                "schema_version": 1, "status": checkpoint_state,
                "total": 100, "processed": 50, "updated_at": now,
                "counts": {"downloaded": 10},
            }),
        )
        assert service._kobo_backup_due() is False
        assert service._kobo_backup_account_due("vault-1") is True
        for state in ("complete", "attention_required", "partial", "failed", "aborted"):
            service.storage.metadata_set(
                key, json.dumps({
                    "schema_version": 1, "status": state,
                    "updated_at": now, "total": 100, "processed": 50,
                }),
            )
            assert service._kobo_backup_account_due("vault-1") is False
    finally:
        service.close()
