"""Durable receipt for an already delivered exact Numilog purchase (#1905).

Created under the acquisition lock, before returning the provider's file.
No receipt is a new download permission. Recovery only indexes the exact
original bytes in their pinned owner's current provider root, without HTTP.
"""
from pathlib import Path
import re
import time

from ..acquisition import Acquisition
from ..acquisition_engine import stable_id
from ..locking import InterProcessLock
from ..paths import acquisition_lock_file
from ..providers import ProviderConfigurationError
from ..plugin_api import Cancelled
from .kobo_local_audit import _read_hash_within
from .materialization import MaterializationResult
from .numilog_local_preview import _identity_root
from .numilog_manifest_rights import _scoped_record_id

_KIND = 'numilog_delivered_artifact_v1'
_PENDING = 'index_pending'
_ERROR = 'Numilog : fichier livré à rapprocher ; aucun nouveau téléchargement autorisé'


def _require(value):
    if not value:
        raise ProviderConfigurationError(_ERROR)


def _record(service, item, target_dir, identity_id, account_id, fingerprint):
    """Caller holds the global acquisition lock and has rechecked admission."""
    digest, size, absolute = fingerprint
    acquisition_id = stable_id('numilog-materialization', identity_id,
                               item.source_credential, account_id, item.record_id)
    # Existing receipts (including failures/completed) are never overwritten.
    _require(service.storage.acquisition_get(acquisition_id) is None)
    service.storage.acquisition_put({
        'acquisition_id': acquisition_id, 'provider_id': 'numilog',
        'credential_id': item.source_credential, 'account_id': account_id,
        'identity_id': identity_id, 'item_id': item.record_id,
        'status': 'attention_required', 'attention_code': 'numilog_index_pending',
        'error_code': 'numilog_index_pending', 'error_message': 'Fichier livré ; indexation à confirmer',
        'attempt_count': 1, 'last_attempt_at': int(time.time()),
        'metadata': {'kind': _KIND, 'phase': _PENDING, 'path': absolute,
                     'root': str(target_dir.absolute()), 'sha256': digest, 'size': size},
    }, insert_only=True)
    row = service.storage.acquisition_get(acquisition_id)
    _require(row is not None and row['identity_id'] == identity_id
             and row['credential_id'] == item.source_credential
             and row['account_id'] == account_id and row['item_id'] == item.record_id
             and row['metadata'].get('sha256') == digest)
    return acquisition_id


def record(service, item, path, target_dir, identity_id, account_id, *, validate):
    try:
        fingerprint = _read_hash_within(str(path), (target_dir,))
        _require(fingerprint is not None)
    except Cancelled:
        raise
    except Exception:
        raise ProviderConfigurationError(_ERROR) from None
    # This internal callback already emits fixed public admission codes.
    # Recheck source/rights after hashing, without replacing their precise code.
    validate()
    try:
        return _record(service, item, target_dir, identity_id, account_id, fingerprint)
    except Cancelled:
        raise
    except Exception:
        raise ProviderConfigurationError(_ERROR) from None


def _checked_receipt(service, acquisition_id, *, expected=None, selected=False):
    _require(type(acquisition_id) is str and re.fullmatch(r'numilog-materialization:[0-9a-f]{32}', acquisition_id))
    row = service.storage.acquisition_get(acquisition_id)
    _require(type(row) is dict and row.get('provider_id') == 'numilog')
    identity_id, credential, account, item = (row.get(k) for k in
        ('identity_id', 'credential_id', 'account_id', 'item_id'))
    _require(all(type(v) is str and v for v in (identity_id, credential, account, item)))
    _require(_scoped_record_id(item, credential) is not None)
    _require(acquisition_id == stable_id('numilog-materialization', identity_id, credential, account, item))
    if selected:
        _require(getattr(service.current_identity, 'identity_id', None) == identity_id)
    if expected is not None:
        _require(expected.provider_id == 'numilog' and expected.credential_id == credential
                 and expected.item_id == item and expected.account_id == account
                 and expected.metadata.get('identity_id') == identity_id)
    accounts = [a for a in service.accounts if a.provider_id == 'numilog' and a.credential_id == credential]
    _require((selected and not accounts) or (len(accounts) == 1
             and accounts[0].identity_id == identity_id and accounts[0].unique_id == account))
    _require(service.storage.identity_for_connection('numilog', credential) == identity_id)
    evidence = service.storage.numilog_purchase_preflight_evidence(
        identity_id=identity_id, credential_id=credential, account_id=account, item_id=item,
    )
    ledgers = evidence.get('acquisitions')
    _require(type(ledgers) is list and len(ledgers) == 1
             and ledgers[0].get('acquisition_id') == acquisition_id
             and ledgers[0].get('identity_id') == identity_id
             and ledgers[0].get('account_id') == account)

    _require(not service.storage.metadata_get('identity-directory-migration-v1'))
    roots = _identity_root(service, identity_id)
    _require(roots is not None and len(roots) == 1)
    root = roots[0] / 'numilog'
    metadata = row.get('metadata')
    _require(type(metadata) is dict and metadata.get('kind') == _KIND
             and metadata.get('phase') in {_PENDING, 'indexed'}
             and metadata.get('root') == str(root)
             and row.get('status') in {'acquiring', 'retryable_error', 'attention_required', 'complete'})
    fingerprint = _read_hash_within(metadata.get('path'), (root,))
    _require(fingerprint is not None and fingerprint ==
             (metadata.get('sha256'), metadata.get('size'), metadata.get('path')))
    _require(type(metadata.get('size')) is int and metadata['size'] > 0)
    return row, Path(metadata['path'])


def index_delivered(service, acquisition, *, title=None, selected=False):
    """Index only the verified receipt; keep it pending on any failure/crash."""
    try:
        acquisition_id = acquisition.metadata.get('numilog_checkpoint_id')
        with InterProcessLock(acquisition_lock_file()):
            row, path = _checked_receipt(service, acquisition_id, expected=acquisition, selected=selected)
            _require(acquisition.path == path)
            placement = service.storage.placement_for_path(path)
            _require(placement is None or (placement.get('identity_id') == row['identity_id']
                     and placement.get('provider_id') == 'numilog'
                     and placement.get('source_item_id') == row['item_id']))
            indexed = service.catalog_index_path(
                path, title=title, item_id=row['item_id'],
                identity_id=row['identity_id'], provider_id='numilog',
            )
            _require(type(indexed) is dict and indexed.get('status') == 'indexed')
            # Recheck source and bytes after the indexer before marking complete.
            refreshed, checked_path = _checked_receipt(service, acquisition_id, expected=acquisition, selected=selected)
            _require(checked_path == path and refreshed['metadata'] == row['metadata'])
            artifact_id = indexed.get('artifact_id')
            _require(type(artifact_id) is str and artifact_id == 'sha256:' + row['metadata']['sha256'])
            _require(service.storage.numilog_checkpoint_complete(
                acquisition_id, identity_id=row['identity_id'], credential_id=row['credential_id'],
                account_id=row['account_id'], item_id=row['item_id'], path=str(path),
                sha256=row['metadata']['sha256'], size=row['metadata']['size'], artifact_id=artifact_id,
            ))
            return MaterializationResult(acquisition, indexed, artifact_id, row['identity_id'])
    except Cancelled:
        raise
    except Exception:
        # Paths, provider payloads, private IDs and raw index failures stay out
        # of UI errors. The durable pending receipt is deliberately retained.
        raise ProviderConfigurationError(_ERROR) from None


def resume(service, acquisition_id):
    """Explicit offline recovery API; never an automatic acquisition retry."""
    try:
        row, path = _checked_receipt(service, acquisition_id, selected=True)
        acquisition = Acquisition(
            provider_id='numilog', credential_id=row['credential_id'], account_id=row['account_id'],
            item_id=row['item_id'], loan_id=None, kind='already_local', source_url=None,
            local_path=path, artifact_path=path, target_dir=path.parent, mime_type=None, drm='unknown',
            metadata={'identity_id': row['identity_id'], 'numilog_checkpoint_id': acquisition_id},
        )
        return index_delivered(service, acquisition, selected=True)
    except Cancelled:
        raise
    except Exception:
        raise ProviderConfigurationError(_ERROR) from None
