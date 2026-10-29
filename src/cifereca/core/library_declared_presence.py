"""Fresh local presence of a declared pair, never a provider receipt (#3171).

Reuses declaration conflicts and no-follow bounded file revalidation. Checks
one already selected file under the acquisition lock; no global scan, network,
file movement, rights write or currently selected GUI identity is involved.
"""
from dataclasses import dataclass
from .source_context import source_context_epoch
import logging
import sqlite3
from contextlib import nullcontext

from ..locking import InterProcessLock, LockBusyError
from ..paths import acquisition_lock_file
from .library_audit_roots import configured_identity_roots
from .library_declared_links import (
    DeclarationRejected, _SCOPE_FIELDS, _account_current,
    _collisions, _context, _records, _require, _right_state, _text, _transaction,
)
from .library_fresh_file_check import revalidate_observed_file
from .library_inventory import FileObservation


_TRACE_LOG = logging.getLogger("cifereca")


@dataclass(frozen=True)
class DeclaredPresence:
    status: str
    reason: str
    trust: str = 'user_declaration'
    read_only: bool = True


def verify_declared_presence(service, *, identity_id, provider_id, credential_id,
                             account_id, item_id, source_current,
                             cancelled=lambda: False, seconds=8,
                             max_bytes=64 * 1024 * 1024,
                             expected_loan_id=None,
                             _acquisition_lock_held=False):
    """Verify one source-bound statement without granting acquisition authority.

    No pathname or SHA is returned. A moved/unavailable/changed file remains
    for review, not a missing-file authorization. Callers retain their own
    consent/session guard and decide how to report a scoped observed presence.

    Internal callers may set _acquisition_lock_held ONLY while holding the
    same global acquisition lock, avoiding a non-reentrant nested acquire.
    """
    scope = dict(zip(_SCOPE_FIELDS, (identity_id, provider_id, credential_id,
                                    account_id, item_id)))
    try:
        _require(all(_text(scope.get(key)) for key in _SCOPE_FIELDS), 'invalid_scope')
        _require(callable(source_current) and callable(cancelled), 'invalid_guard')
        _require(expected_loan_id is None or _text(expected_loan_id),
                 'invalid_loan_scope')
        _require(type(seconds) in (int, float) and 0 < seconds <= 8
                 and type(max_bytes) is int and 0 < max_bytes <= 64 * 1024 * 1024,
                 'invalid_budget')
        storage, config = service.storage, service.config
        data = config.data
        accounts, identities = service.accounts, service.identities
        epoch = source_context_epoch(service)
        identity = service.identity_by_id(identity_id)
        roots = configured_identity_roots(config, identity_id)[identity_id]

        def valid():
            return (service.storage is storage and service.config is config
                    and config.data is data
                    and service.accounts is accounts and service.identities is identities
                    and source_context_epoch(service) == epoch
                    and identity is not None
                    and service.identity_by_id(identity_id) is identity
                    and _account_current(service.accounts, scope)
                    and configured_identity_roots(config, identity_id)[identity_id] == roots
                    and not cancelled())

        _require(valid() and source_current() is True, 'source_scope_changed')

        def read_pair():
            with _transaction(storage, False) as db:
                _require(valid(), 'source_scope_changed')
                records = _records(db)
                selected = [record for record in records
                            if record['state'] == 'active' and record['scope'] == scope]
                _require(len(selected) == 1, 'declaration_changed')
                record = selected[0]
                context = _context(db, scope)
                # A declared local file for an earlier loan of the same
                # edition cannot satisfy a NEW loan transaction. When an
                # exact loan is requested, verify it inside both consistent
                # SQLite reads, before and after file revalidation.
                if expected_loan_id is not None:
                    _require(context['right']['entitlement_type'] == 'loan'
                             and context['right']['loan_id'] == expected_loan_id,
                             'loan_scope_changed')
                _require(context['connection']['enabled'] == 1
                         and record['right_state'] == _right_state(context)
                         and context['right']['state'] == 'active', 'right_state_changed')
                file = record['file']
                # The declaration writer only accepts CRC-checked EPUB
                # observations. Its pinned hash/stamp preserves that statement;
                # revalidate the same bytes, not a new global CRC inventory.
                observation = FileObservation(
                    identity_id=identity_id, path=file['path'], size=file['size'],
                    media_format='epub', sha256=file['sha256'], validation='ok',
                    fingerprint=tuple(file['fingerprint']),
                    structure_validation='epub_structure_crc_ok',
                )
                _collisions(db, scope, observation, records)
                return record, context, observation

        lock = (nullcontext() if _acquisition_lock_held
                else InterProcessLock(acquisition_lock_file()))
        with lock:
            _require(valid() and source_current() is True, 'source_scope_changed')
            record, context, observation = read_pair()
            # Do not call source guards or hash a book with Storage._lock held.
            # Guards may read SQLite; the mutex is intentionally non-reentrant.
            fresh = revalidate_observed_file(
                observation, roots, identity_id=identity_id,
                cancelled=lambda: not valid() or source_current() is not True,
                seconds=seconds, max_bytes=max_bytes,
            )
            _require(valid() and source_current() is True, 'source_scope_changed')
            if not fresh.verified:
                return DeclaredPresence('needs_review', fresh.reason)
            current_record, current_context, _ = read_pair()
            _require(record == current_record and context == current_context,
                     'declaration_changed')
            _require(valid() and source_current() is True, 'source_scope_changed')
        return DeclaredPresence('declared_current', 'unchanged')
    except DeclarationRejected as error:
        return DeclaredPresence('needs_review', str(error))
    except LockBusyError:
        return DeclaredPresence('needs_review', 'acquisition_busy')
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        # A closed reconciliation outcome does not replace the original
        # SQLite/filesystem failure. Keep the full observed cause (including
        # chained exceptions) in the canonical raw journal; do not inspect
        # anything else, issue a GET, or change the action's admission.
        _TRACE_LOG.warning(
            "TRACE declared presence check failed identity_id=%s provider_id=%s "
            "credential_id=%s account_id=%s item_id=%s expected_loan_id=%s",
            identity_id, provider_id, credential_id, account_id, item_id,
            expected_loan_id, exc_info=True,
        )
        return DeclaredPresence('needs_review', str(error))
