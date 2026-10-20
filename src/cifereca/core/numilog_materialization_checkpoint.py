"""Durable receipt for an already delivered exact Numilog purchase (#1905).

Created under the acquisition lock, before returning the provider's file.
No receipt is a new download permission. Recovery only indexes the exact
original bytes in their pinned owner's current provider root, without HTTP.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar
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
    evidence = service.storage.numilog_purchase_preflight_evidence(
        identity_id=identity_id, credential_id=item.source_credential,
        account_id=account_id, item_id=item.record_id,
    )
    _require(evidence.get('acquisitions') == []
             and evidence.get('placement_conflict') is False)
    # A placement might have appeared after the earlier locked admission
    # snapshot. Never persist an index-pending receipt over a competing local
    # Numilog placement, including one without an acquisition ledger row.
    # A durable local right can bind the receipt for the existing preview.
    # No local right is required for fresh provider delivery, and ambiguous,
    # stale or public evidence must never be guessed into an entitlement.
    entitlement_id = None
    rights = evidence.get('entitlements')
    if type(rights) is list and len(rights) == 1:
        right = rights[0]
        meta = right.get('metadata')
        if (type(right.get('entitlement_id')) is str and right['entitlement_id']
                and right.get('identity_id') == identity_id
                and right.get('credential_id') == item.source_credential
                and right.get('account_id') == account_id and right.get('item_id') == item.record_id
                and right.get('entitlement_type') == 'owned' and right.get('state') == 'active'
                and right.get('loan_id') is None and right.get('expires_at') is None
                and type(meta) is dict and meta.get('source') == 'numilog.authenticated_library'
                and all(meta.get(k) is None or type(meta[k]) is bool
                        for k in ('owned', 'downloadable', 'archived'))
                and meta.get('owned') is not False and meta.get('downloadable') is not False
                and meta.get('archived') is not True):
            entitlement_id = right['entitlement_id']
    service.storage.acquisition_put({
        'entitlement_id': entitlement_id,
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


def _checked_receipt(service, acquisition_id, *, expected=None, selected=False, verify_file=True):
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
    _require(type(metadata.get('path')) is str and bool(metadata['path'])
             and type(metadata.get('sha256')) is str
             and re.fullmatch(r'[0-9a-f]{64}', metadata['sha256'])
             and type(metadata.get('size')) is int and metadata['size'] > 0)
    if verify_file:
        fingerprint = _read_hash_within(metadata['path'], (root,))
        _require(fingerprint is not None and fingerprint ==
                 (metadata['sha256'], metadata['size'], metadata['path']))
    return row, Path(metadata['path'])


def index_delivered(service, acquisition, *, title=None, selected=False, expected_receipt=None):
    """Index only the verified receipt; keep it pending on any failure/crash."""
    try:
        acquisition_id = acquisition.metadata.get('numilog_checkpoint_id')
        with InterProcessLock(acquisition_lock_file()):
            row, path = _checked_receipt(service, acquisition_id, expected=acquisition, selected=selected)
            if expected_receipt is not None:
                _require(_resume_target(row) == expected_receipt)
            _require(acquisition.path == path)
            placement = service.storage.placement_for_path(path)
            if (placement is not None
                    and placement.get('identity_id') == row['identity_id']
                    and placement.get('provider_id') == 'numilog'
                    and placement.get('source_item_id') != row['item_id']):
                # ADEPT imported the original ACSM first, keyed by its sha.
                # Upgrade only if the verified owned receipt AND the original
                # same-owner manual-acsm ledger and local artifact all agree.
                # Never infer provenance from the folder name or a title.
                _require(service.storage.numilog_checkpoint_adopt_acsm_placement(
                    acquisition_id, expected_path=str(path),
                ))
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


def resume(service, acquisition_id, *, expected_receipt=None):
    """Explicit offline recovery API; never an automatic acquisition retry."""
    try:
        row, path = _checked_receipt(service, acquisition_id, selected=True)
        acquisition = Acquisition(
            provider_id='numilog', credential_id=row['credential_id'], account_id=row['account_id'],
            item_id=row['item_id'], loan_id=None, kind='already_local', source_url=None,
            local_path=path, artifact_path=path, target_dir=path.parent, mime_type=None, drm='unknown',
            metadata={'identity_id': row['identity_id'], 'numilog_checkpoint_id': acquisition_id},
        )
        return index_delivered(service, acquisition, selected=True, expected_receipt=expected_receipt)
    except Cancelled:
        raise
    except Exception:
        raise ProviderConfigurationError(_ERROR) from None


@dataclass(frozen=True, repr=False)
class NumilogIndexResumeTarget:
    provider_id: ClassVar[str] = 'numilog'
    operation: ClassVar[str] = 'resume_index'
    identity_id: str
    item_id: str
    acquisition_id: str
    scope: tuple


def _resume_target(row):
    meta = row['metadata']
    return NumilogIndexResumeTarget(row['identity_id'], row['item_id'], row['acquisition_id'],
        tuple(row[k] for k in ('credential_id', 'account_id', 'status')) +
        tuple(meta[k] for k in ('kind', 'phase', 'path', 'root', 'sha256', 'size')))


def target_for_entry(service, entry):
    """SQL/configuration only; never hash, scan, create directories or use HTTP."""
    try:
        owner = getattr(service.current_identity, 'identity_id', None)
        possessions = entry.get('possessions')
        _require(type(possessions) in (list, tuple) and owner is not None)
        candidates = [p for p in possessions if type(p) is dict
                      and p.get('identity_id') == owner and p.get('kind') == 'owned'
                      and p.get('state') == 'active' and p.get('provider_id') in {'kobo', 'numilog'}]
        _require(len(candidates) == 1 and candidates[0].get('provider_id') == 'numilog')
        possession = candidates[0]
        rights = service.storage.numilog_possession_rights(
            identity_id=owner, item_id=possession.get('item_id'),
            entitlement_id=possession.get('entitlement_id'),
        )
        _require(len(rights) == 1)
        right = rights[0]
        evidence = service.storage.numilog_purchase_preflight_evidence(
            identity_id=owner, credential_id=right['credential_id'],
            account_id=right['account_id'], item_id=right['item_id'])
        _require(len(evidence['entitlements']) == 1
                 and right.get('metadata', {}).get('source') == 'numilog.authenticated_library')
        aid = stable_id('numilog-materialization', owner, right['credential_id'],
                        right['account_id'], right['item_id'])
        row, _path = _checked_receipt(service, aid, selected=True, verify_file=False)
        _require(row['status'] == 'attention_required' and row['metadata']['phase'] == _PENDING)
        return _resume_target(row)
    except Exception:
        return None
