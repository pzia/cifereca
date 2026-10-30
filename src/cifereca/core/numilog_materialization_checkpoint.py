"""Durable receipt for an already delivered exact Numilog purchase (#1905).

Created under the acquisition lock, before returning the provider's file.
No receipt is a new download permission. Recovery only indexes the exact
original bytes in their pinned owner's current provider root, without HTTP.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar
import re
import logging
import time

from ..acquisition import Acquisition
from ..acquisition_engine import stable_id
from ..locking import InterProcessLock, LockBusyError
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

# CoreUI translates these closed rejection codes. The local journal retains
# the observed failure, boundary, receipt and exception without transformation.
_ADOPTION_REFUSALS = frozenset({
    "invalid_target", "receipt_missing", "receipt_scope_invalid",
    "receipt_invalid", "receipt_root_invalid", "path_not_canonical",
    "manual_receipt_missing", "manual_receipt_mismatch",
    "artifact_mismatch", "placement_missing", "placement_conflict",
    "catalogue_conflict", "artifact_shared_elsewhere",
    "competing_receipts", "adoption_unavailable",
    "selected_identity_changed", "target_changed", "account_changed",
    "receipt_conflict", "directory_migration_pending", "root_changed",
    "file_changed", "acquisition_busy", "lock_unavailable", "receipt_verification_failed",
    "placement_validation_failed", "index_rejected", "indexing_failed",
    "finalization_failed",
})


class NumilogIndexResumeRejected(ProviderConfigurationError):
    """One closed, diagnostic-only rejection; never authorizes a retry."""

    def __init__(self, reason_code: str):
        self.reason_code = (reason_code if type(reason_code) is str
                            and reason_code in _ADOPTION_REFUSALS
                            else "adoption_unavailable")
        super().__init__(_ERROR)



_PHASE_FAILURES = {
    "lock": "lock_unavailable",
    "receipt": "receipt_verification_failed",
    "placement": "placement_validation_failed",
    "index": "indexing_failed",
    "revalidate": "receipt_verification_failed",
    "complete": "finalization_failed",
}


def _resume_failure(exc, phase, acquisition_id, indexed=None):
    """Record this local failure once, correlated to the actual worker scope."""
    rejection = (exc if isinstance(exc, NumilogIndexResumeRejected) else
                 NumilogIndexResumeRejected(
                     "acquisition_busy" if isinstance(exc, LockBusyError) else
                     _PHASE_FAILURES[phase]))
    try:
        from ..diagnostics import _core_journal_record
        from ..diagnostic_task_context import diagnostic_task_reference
        fields = dict(provider_id="numilog", task_kind="index", phase=phase,
                      code=rejection.reason_code, acquisition_id=acquisition_id)
        reference = diagnostic_task_reference()
        if reference is not None:
            fields["task_ref"] = reference
        if indexed is not None:
            fields["index_result"] = indexed
        message = "Numilog index resume " + repr(fields)
        logging.getLogger("cifereca").warning(
            "%s", message, exc_info=True,
            extra=_core_journal_record("NUMILOG", "index_resume", "error", message, fields),
        )
    except Exception:
        pass  # Optional diagnostics must not change recovery outcomes.
    return rejection


def _require(value):
    if not value:
        raise ProviderConfigurationError(_ERROR)


def _check_resume(value, code):
    """Refuse one known local verification gate without a private payload."""
    if not value:
        raise NumilogIndexResumeRejected(code)


def _record(service, item, target_dir, identity_id, account_id, fingerprint, *, recovery=False):
    """Caller holds the global acquisition lock and has rechecked admission."""
    digest, size, absolute = fingerprint
    from .numilog_recovery import recovery_id, recovery_gate
    acquisition_id = (recovery_id(identity_id, item.source_credential, account_id, item.record_id)
        if recovery else stable_id('numilog-materialization', identity_id,
                               item.source_credential, account_id, item.record_id))
    # Existing receipts (including failures/completed) are never overwritten.
    _require(service.storage.acquisition_get(acquisition_id) is None)
    evidence = service.storage.numilog_purchase_preflight_evidence(
        identity_id=identity_id, credential_id=item.source_credential,
        account_id=account_id, item_id=item.record_id,
    )
    if recovery:
        _require(recovery_gate(
            service.storage, identity_id=identity_id,
            credential_id=item.source_credential, account_id=account_id,
            item_id=item.record_id,
        ) == 'recoverable')
    else:
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


def record(service, item, path, target_dir, identity_id, account_id, *, validate, recovery=False):
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
        return _record(service, item, target_dir, identity_id, account_id, fingerprint, recovery=recovery)
    except Cancelled:
        raise
    except Exception:
        raise ProviderConfigurationError(_ERROR) from None


def _checked_receipt(service, acquisition_id, *, expected=None, selected=False, verify_file=True):
    _check_resume(type(acquisition_id) is str and re.fullmatch(
        r'numilog-materialization(?:-recovery)?:[0-9a-f]{32}', acquisition_id), "invalid_target")
    row = service.storage.acquisition_get(acquisition_id)
    _check_resume(type(row) is dict and row.get('provider_id') == 'numilog', "receipt_missing")
    identity_id, credential, account, item = (row.get(k) for k in
        ('identity_id', 'credential_id', 'account_id', 'item_id'))
    _check_resume(all(type(v) is str and v for v in (identity_id, credential, account, item)), "receipt_scope_invalid")
    _check_resume(_scoped_record_id(item, credential) is not None, "receipt_scope_invalid")
    from .numilog_recovery import recovery_id
    is_recovery = acquisition_id.startswith('numilog-materialization-recovery:')
    expected_id = (recovery_id(identity_id, credential, account, item)
                   if is_recovery else stable_id('numilog-materialization', identity_id, credential, account, item))
    _check_resume(acquisition_id == expected_id, "receipt_scope_invalid")
    if selected:
        _check_resume(getattr(service.current_identity, 'identity_id', None) == identity_id, "selected_identity_changed")
    if expected is not None:
        _check_resume(expected.provider_id == 'numilog' and expected.credential_id == credential
                 and expected.item_id == item and expected.account_id == account
                 and expected.metadata.get('identity_id') == identity_id, "target_changed")
    accounts = [a for a in service.accounts if a.provider_id == 'numilog' and a.credential_id == credential]
    _check_resume((selected and not accounts) or (len(accounts) == 1
             and accounts[0].identity_id == identity_id and accounts[0].unique_id == account), "account_changed")
    _check_resume(service.storage.identity_for_connection('numilog', credential) == identity_id, "account_changed")
    evidence = service.storage.numilog_purchase_preflight_evidence(
        identity_id=identity_id, credential_id=credential, account_id=account, item_id=item,
    )
    ledgers = evidence.get('acquisitions')
    _check_resume(type(ledgers) is list and len(ledgers) <= (2 if is_recovery else 1)
             and any(r.get('acquisition_id') == acquisition_id
                     and r.get('identity_id') == identity_id
                     and r.get('account_id') == account for r in ledgers)
             and all(r.get('identity_id') == identity_id
                     and r.get('account_id') == account
                     and r.get('item_id') == item
                     and (r.get('acquisition_id') == acquisition_id or
                          (is_recovery and r.get('status') == 'complete'))
                     for r in ledgers), "receipt_conflict")

    _check_resume(not service.storage.metadata_get('identity-directory-migration-v1'), "directory_migration_pending")
    roots = _identity_root(service, identity_id)
    _check_resume(roots is not None and len(roots) == 1, "root_changed")
    root = roots[0] / 'numilog'
    metadata = row.get('metadata')
    _check_resume(type(metadata) is dict and metadata.get('kind') == _KIND
             and metadata.get('phase') in {_PENDING, 'indexed'}
             and row.get('status') in {'acquiring', 'retryable_error', 'attention_required', 'complete'}, "receipt_invalid")
    _check_resume(metadata.get('root') == str(root), "root_changed")
    _check_resume(type(metadata.get('path')) is str and bool(metadata['path'])
             and type(metadata.get('sha256')) is str
             and re.fullmatch(r'[0-9a-f]{64}', metadata['sha256'])
             and type(metadata.get('size')) is int and metadata['size'] > 0, "receipt_invalid")
    if verify_file:
        fingerprint = _read_hash_within(metadata['path'], (root,))
        _check_resume(fingerprint is not None and fingerprint ==
                 (metadata['sha256'], metadata['size'], metadata['path']), "file_changed")
    return row, Path(metadata['path'])


def index_delivered(service, acquisition, *, title=None, selected=False, expected_receipt=None):
    """Index only the verified receipt; keep it pending on any failure/crash."""
    phase, acquisition_id, indexed = "receipt", None, None
    try:
        acquisition_id = acquisition.metadata.get('numilog_checkpoint_id')
        phase = "lock"
        with InterProcessLock(acquisition_lock_file()):
            phase = "receipt"
            row, path = _checked_receipt(service, acquisition_id, expected=acquisition, selected=selected)
            if expected_receipt is not None:
                _check_resume(_resume_target(row) == expected_receipt, "target_changed")
            _check_resume(acquisition.path == path, "target_changed")
            phase = "placement"
            placement = service.storage.placement_for_path(path)
            if (placement is not None
                    and placement.get('identity_id') == row['identity_id']
                    and placement.get('provider_id') == 'numilog'
                    and placement.get('source_item_id') != row['item_id']):
                # ADEPT imported the original ACSM first, keyed by its sha.
                # Upgrade only if the verified owned receipt AND the original
                # same-owner manual-acsm ledger and local artifact all agree.
                # Never infer provenance from the folder name or a title.
                # The storage transaction independently checks the exact
                # manual ACSM ledger, account, byte hash, placement, catalog
                # and same-owner/unknown-owner artifact collisions. A refusal carries only the
                # fixed failed-gate code; NEVER skip the atomic adoption.
                adoption = service.storage.numilog_checkpoint_adopt_acsm_placement(
                    acquisition_id, expected_path=str(path), diagnose=True,
                )
                if adoption != "adopted":
                    raise NumilogIndexResumeRejected(adoption)
                placement = service.storage.placement_for_path(path)
            _check_resume(placement is None or (placement.get('identity_id') == row['identity_id']
                     and placement.get('provider_id') == 'numilog'
                     and placement.get('source_item_id') == row['item_id']), "placement_conflict")
            phase = "index"
            indexed = service.catalog_index_path(
                path, title=title, item_id=row['item_id'],
                identity_id=row['identity_id'], provider_id='numilog',
            )
            _check_resume(type(indexed) is dict and indexed.get('status') == 'indexed', "index_rejected")
            phase = "revalidate"
            # Recheck source and bytes after the indexer before marking complete.
            refreshed, checked_path = _checked_receipt(service, acquisition_id, expected=acquisition, selected=selected)
            _check_resume(checked_path == path and refreshed['metadata'] == row['metadata'], "target_changed")
            artifact_id = indexed.get('artifact_id')
            _check_resume(type(artifact_id) is str and artifact_id == 'sha256:' + row['metadata']['sha256'], "artifact_mismatch")
            phase = "complete"
            _check_resume(service.storage.numilog_checkpoint_complete(
                acquisition_id, identity_id=row['identity_id'], credential_id=row['credential_id'],
                account_id=row['account_id'], item_id=row['item_id'], path=str(path),
                sha256=row['metadata']['sha256'], size=row['metadata']['size'], artifact_id=artifact_id,
            ), "finalization_failed")
            return MaterializationResult(acquisition, indexed, artifact_id, row['identity_id'])
    except Cancelled:
        raise
    except Exception as exc:
        # The dialog receives a closed reason; the journal keeps the raw cause.
        raise _resume_failure(exc, phase, acquisition_id, indexed) from None


def resume(service, acquisition_id, *, expected_receipt=None):
    """Explicit offline recovery API; never an automatic acquisition retry."""
    delegated = False
    try:
        row, path = _checked_receipt(service, acquisition_id, selected=True)
        acquisition = Acquisition(
            provider_id='numilog', credential_id=row['credential_id'], account_id=row['account_id'],
            item_id=row['item_id'], loan_id=None, kind='already_local', source_url=None,
            local_path=path, artifact_path=path, target_dir=path.parent, mime_type=None, drm='unknown',
            metadata={'identity_id': row['identity_id'], 'numilog_checkpoint_id': acquisition_id},
        )
        delegated = True
        return index_delivered(service, acquisition, selected=True, expected_receipt=expected_receipt)
    except Cancelled:
        raise
    except Exception as exc:
        if delegated:
            raise  # index_delivered already recorded this exact failure.
        raise _resume_failure(exc, "receipt", acquisition_id) from None


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
        # Recovery keeps the legacy complete receipt intact. Its own
        # index-pending receipt is the actionable offline resume target.
        from .numilog_recovery import recovery_id
        recovery = recovery_id(
            owner, right['credential_id'], right['account_id'], right['item_id'],
        )
        aid = recovery if service.storage.acquisition_get(recovery) is not None else (
            stable_id('numilog-materialization', owner, right['credential_id'],
                      right['account_id'], right['item_id'])
        )
        row, _path = _checked_receipt(service, aid, selected=True, verify_file=False)
        _require(row['status'] == 'attention_required' and row['metadata']['phase'] == _PENDING)
        return _resume_target(row)
    except Exception:
        return None
