"""Private Core declarations, distinct from provider evidence (#2797).

One explicit local right/file selection, never an entitlement, receipt or
attested placement. Metadata records survive provider sync without entering
its evidence tables. This primitive is not exposed to plugins or Diagnostics;
the caller must present the private diff and obtain explicit confirmation.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
import hashlib
import json
import re
import sqlite3
import time
import uuid

from ..locking import InterProcessLock, LockBusyError
from ..paths import acquisition_lock_file
from ..storage import CURRENT_SCHEMA_VERSION, Storage
from .library_audit_roots import configured_identity_roots
from .library_fresh_file_check import revalidate_observed_file
from .library_inventory import FileObservation

_PREFIX = 'core.library.user-declaration.v1.'
_MAX_RECORDS = 4096
_MAX_RECORD_BYTES = 32768
_MAX_STORE_BYTES = 8 * 1024 * 1024
_PROVIDERS = {'kobo', 'numilog', 'paris'}
_SCOPE_FIELDS = ('identity_id', 'provider_id', 'credential_id', 'account_id', 'item_id')


class DeclarationRejected(ValueError):
    """Closed code only; never a private path or SQLite exception."""


def _require(condition, code):
    if not condition:
        raise DeclarationRejected(code)


def _text(value):
    return (type(value) is str and 0 < len(value) <= 1024
            and not any(c in value for c in ('\0', '\n', '\r')))


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


def _token(value):
    return hashlib.sha256(('local-user-declaration/1\0' + _json(value)).encode()).hexdigest()


def _file(observation):
    return {'path': observation.path, 'sha256': observation.sha256,
            'size': observation.size, 'fingerprint': list(observation.fingerprint)}


def _scope(service, scope):
    _require(all(_text(scope.get(key)) for key in _SCOPE_FIELDS)
             and scope['provider_id'] in _PROVIDERS, 'invalid_scope')
    current = service.current_identity
    _require(current is not None and current.identity_id == scope['identity_id'],
             'identity_not_active')
    config, storage = service.config, service.storage
    accounts, identities = service.accounts, service.identities
    epoch, data = service._account_context_epoch, config.data
    roots = configured_identity_roots(config, scope['identity_id'])[scope['identity_id']]

    def account_matches():
        matches = [row for row in service.accounts
                   if row.provider_id == scope['provider_id']
                   and row.credential_id == scope['credential_id']]
        return (len(matches) == 1 and matches[0].identity_id == scope['identity_id']
                and matches[0].unique_id == scope['account_id'])

    _require(account_matches(), 'account_scope_changed')

    def valid():
        try:
            return (service.current_identity is current
                    and current.identity_id == scope['identity_id']
                    and service.config is config and config.data is data
                    and service.storage is storage
                    and service.accounts is accounts and service.identities is identities
                    and service._account_context_epoch == epoch
                    and account_matches()
                    and configured_identity_roots(config, scope['identity_id'])[
                        scope['identity_id']] == roots)
        except Exception:
            return False

    return roots, valid, epoch


@contextmanager
def _transaction(storage, write, after_commit=None):
    with storage._lock:
        db = storage._connect()
        _require(not db.in_transaction, 'storage_busy')
        previous = db.execute('PRAGMA synchronous').fetchone()[0]
        try:
            if write:
                db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            _require(Storage._read_schema_version(db) == CURRENT_SCHEMA_VERSION,
                     'schema_upgrade_required')
            pending = db.execute("SELECT value FROM metadata WHERE key=?",
                                 ('identity-directory-migration-v1',)).fetchone()
            _require(not pending or not pending[0], 'directory_migration_pending')
            yield db
            if write:
                db.commit()
                if after_commit is not None:
                    # Observe this exact durable image while both writer
                    # locks are held. Presentation failure cannot undo it.
                    try:
                        after_commit()
                    except Exception:
                        pass
            else:
                db.rollback()
        except BaseException:
            db.rollback()
            raise
        finally:
            if write:
                db.execute('PRAGMA synchronous=' + str(int(previous)))


def _rows(db, query, params):
    cursor = db.execute(query, params)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _records(db):
    # The namespace is private, bounded and versioned. Unknown/malformed data
    # must never be ignored when proving absence of conflicting declarations.
    rows = db.execute('SELECT key,value FROM metadata WHERE key GLOB ? '
                      'ORDER BY key LIMIT ?', (_PREFIX + '*', _MAX_RECORDS + 1)).fetchall()
    _require(len(rows) <= _MAX_RECORDS, 'declaration_budget')
    result = []
    total = 0
    for key, raw in rows:
        _require(type(raw) is str and len(raw) <= _MAX_RECORD_BYTES,
                 'declaration_store_invalid')
        total += len(raw)
        _require(total <= _MAX_STORE_BYTES, 'declaration_budget')
        record = json.loads(raw)
        _require(type(record) is dict and set(record) == {
            'version', 'relation_id', 'scope', 'file', 'right_state', 'state',
            'origin', 'created_at', 'revoked_at',
        }, 'declaration_store_invalid')
        context, file = record['scope'], record['file']
        _require(record['version'] == 1 and type(record['version']) is int
                 and type(record['relation_id']) is str
                 and re.fullmatch('[0-9a-f]{32}', record['relation_id']) is not None
                 and key == _PREFIX + record['relation_id']
                 and record['state'] in ('active', 'revoked')
                 and record['origin'] == 'user_declaration'
                 and type(context) is dict and set(context) == set(_SCOPE_FIELDS)
                 and all(_text(context[field]) for field in _SCOPE_FIELDS)
                 and context['provider_id'] in _PROVIDERS
                 and type(file) is dict and set(file) == {
                     'path', 'sha256', 'size', 'fingerprint'}
                 and type(file['path']) is str and 0 < len(file['path']) <= 4096
                 and type(file['sha256']) is str
                 and re.fullmatch('[0-9a-f]{64}', file['sha256']) is not None
                 and type(file['size']) is int and file['size'] > 0
                 and type(file['fingerprint']) is list and len(file['fingerprint']) == 5
                 and all(type(n) is int for n in file['fingerprint'])
                 and type(record['right_state']) is dict
                 and set(record['right_state']) == {
                     'entitlement_id', 'entitlement_type', 'state', 'loan_id', 'expires_at'}
                 and type(record['created_at']) is int
                 and (record['revoked_at'] is None if record['state'] == 'active'
                      else type(record['revoked_at']) is int), 'declaration_store_invalid')
        result.append(record)
    return result


def _context(db, scope):
    connections = _rows(db, 'SELECT connection_id,identity_id,provider_id,credential_id,enabled '
                        'FROM provider_connections '
                        'WHERE provider_id=? AND credential_id=? LIMIT 3',
                        (scope['provider_id'], scope['credential_id']))
    _require(len(connections) == 1
             and connections[0]['identity_id'] == scope['identity_id'],
             'source_scope_changed')
    # No authenticated-library receipt requirement: this only binds an
    # EXISTING selected row to its actual account. It never creates a right.
    rights = _rows(db, 'SELECT entitlement_id,identity_id,provider_id,credential_id,account_id,'
                   'item_id,entitlement_type,state,loan_id,expires_at FROM entitlements WHERE provider_id=? '
                   'AND credential_id=? AND item_id=? LIMIT 3',
                   (scope['provider_id'], scope['credential_id'], scope['item_id']))
    _require(len(rights) == 1 and all(rights[0].get(k) == v for k, v in scope.items()),
             'right_scope_changed')
    context = {'connection': connections[0], 'right': rights[0]}
    _require(len(_json(context)) <= _MAX_RECORD_BYTES, 'invalid_local_context')
    return context


def _right_state(context):
    # A changed loan/ownership state is shown for review after sync. Keep the
    # declaration intact; never infer permanence or modify a library licence.
    return {k: context['right'].get(k) for k in (
        'entitlement_id', 'entitlement_type', 'state', 'loan_id', 'expires_at')}


def _collisions(db, scope, observation, records):
    for row in records:
        if row['state'] != 'active':
            continue
        same_file = row['file']['path'] == observation.path
        same_right = row['scope'] == scope
        _require(not (same_file or same_right)
                 or (same_file and same_right and row['file'] == _file(observation)),
                 'declaration_conflict')
    placements = _rows(db, 'SELECT placement_id,path,artifact_id,identity_id,provider_id,'
                       'source_item_id,state FROM placements WHERE path=? OR '
                       '(identity_id=? AND provider_id=? AND source_item_id=?) LIMIT 4',
                       (observation.path, scope['identity_id'], scope['provider_id'],
                        scope['item_id']))
    _require(len(placements) < 4, 'placement_coverage_incomplete')
    for row in placements:
        _require(row['path'] == observation.path
                 and row['identity_id'] == scope['identity_id']
                 and row['artifact_id'] == 'sha256:' + observation.sha256
                 and (row.get('provider_id') in (None, '', 'local')
                      or (row['provider_id'] == scope['provider_id']
                          and row.get('source_item_id') == scope['item_id'])),
                 'placement_conflict')
        if row.get('provider_id') == scope['provider_id']:
            # Placements have no account column. Do not reuse a source/item
            # coordinate which is shared by multiple credential contexts.
            rights = _rows(db, 'SELECT credential_id,account_id,identity_id FROM entitlements '
                          'WHERE provider_id=? AND item_id=? LIMIT 3',
                          (scope['provider_id'], scope['item_id']))
            _require(len(rights) == 1 and all(rights[0][k] == scope[k] for k in (
                'credential_id', 'account_id', 'identity_id')), 'placement_account_ambiguous')
        catalog = _rows(db, 'SELECT identity_id,provider_id,item_id FROM catalog_entries '
                        'WHERE placement_id=? LIMIT 4', (row['placement_id'],))
        _require(len(catalog) < 4 and all(
            r['identity_id'] == scope['identity_id']
            and (r['provider_id'] in (None, '', 'local') or (
                r['provider_id'] == scope['provider_id'] and r['item_id'] == scope['item_id']))
            for r in catalog), 'catalogue_conflict')
    return placements


@dataclass(frozen=True, repr=False)
class DeclarationResult:
    status: str
    plan_token: str | None = None
    relation_id: str | None = None
    changed: int = 0
    # Private diff; never serialize into a report, diagnostic or plugin result.
    before: dict | None = None
    after: dict | None = None
    trust: str = 'user_declaration'
    rights_mutations: int = 0
    file_mutations: int = 0
    placement_mutations: int = 0


def declare_local_link(service, *, observation: FileObservation, identity_id: str,
                       provider_id: str, credential_id: str, account_id: str,
                       item_id: str, confirmation=None, revoke=None,
                       cancelled=lambda: False, on_committed=None):
    """Private preview/confirm, or preview/confirm revoking our own declaration.

    ``confirmation`` is the exact token of the private diff shown to the user.
    Revocation changes only this record and retains its origin and history.
    Missing receipts are allowed; cross-account selections and stale files
    are not. This API does not attach a provider placement or enable download.
    """
    try:
        _require(type(observation) is FileObservation, 'invalid_observation')
        _require(confirmation is None or (type(confirmation) is str
                 and re.fullmatch('[0-9a-f]{64}', confirmation) is not None),
                 'invalid_confirmation')
        _require(revoke is None or (type(revoke) is str
                 and re.fullmatch('[0-9a-f]{32}', revoke) is not None), 'invalid_relation')
        _require(callable(cancelled), 'invalid_cancellation')
        _require(on_committed is None or callable(on_committed), 'invalid_commit_hook')
        scope = dict(zip(_SCOPE_FIELDS, (identity_id, provider_id, credential_id,
                                        account_id, item_id)))
        roots, valid, epoch = _scope(service, scope)
        _require(observation.identity_id == identity_id, 'file_scope_changed')
        write = confirmation is not None
        lock = InterProcessLock(acquisition_lock_file()) if write else nullcontext()
        committed = []

        def notify_commit():
            if committed and on_committed is not None:
                on_committed()

        with lock, _transaction(service.storage, write, notify_commit) as db:
            _require(valid() and not cancelled(), 'source_scope_changed')
            # Revocation withdraws a statement only. A removed right or a
            # missing/replaced book must not trap an obsolete declaration.
            context = _context(db, scope) if revoke is None else {}
            records = _records(db)
            placements = _collisions(db, scope, observation, records) if revoke is None else []
            if revoke is None:
                fresh = revalidate_observed_file(observation, roots, identity_id=identity_id,
                                                 cancelled=lambda: cancelled() or not valid())
                _require(fresh.verified and valid() and not cancelled(), 'file_revalidation_failed')
            if revoke is not None:
                matches = [r for r in records if r['relation_id'] == revoke]
                _require(len(matches) == 1 and matches[0]['scope'] == scope
                         and matches[0]['file']['path'] == observation.path, 'relation_scope_changed')
                before = matches[0]
                if before['state'] == 'revoked':
                    return DeclarationResult('already_revoked', relation_id=revoke)
                # Timestamp is assigned only at commit, not part of the diff.
                after = {**before, 'state': 'revoked'}
                status = 'revoke_preview'
            else:
                matches = [r for r in records if r['state'] == 'active'
                           and r['scope'] == scope and r['file'] == _file(observation)]
                if matches:
                    return DeclarationResult('already_declared', relation_id=matches[0]['relation_id'])
                _require(len(records) < _MAX_RECORDS, 'declaration_budget')
                before = None
                after = {'version': 1, 'origin': 'user_declaration', 'scope': scope,
                         'file': _file(observation), 'right_state': _right_state(context),
                         'state': 'active', 'revoked_at': None}
                status = 'preview'
            token = _token({'scope': scope, 'roots': list(map(str, roots)),
                            'database': str(service.storage.path), 'epoch': epoch,
                            'context': context, 'placements': placements,
                            'records': records, 'before': before, 'after': after})
            if not write:
                return DeclarationResult(status, token, revoke, before=before, after=after)
            _require(confirmation == token, 'preview_changed')
            now = int(time.time())
            if revoke is None:
                record = {**after, 'relation_id': uuid.uuid4().hex, 'created_at': now}
                db.execute('INSERT INTO metadata(key,value) VALUES(?,?)',
                           (_PREFIX + record['relation_id'], _json(record)))
            else:
                record = {**after, 'revoked_at': now}
                db.execute('UPDATE metadata SET value=? WHERE key=?',
                           (_json(record), _PREFIX + revoke))
                _require(db.execute('SELECT changes()').fetchone()[0] == 1,
                         'relation_scope_changed')
            _require(len(_json(record)) <= _MAX_RECORD_BYTES, 'declaration_budget')
            _require(sum(len(_json(r)) for r in records) + len(_json(record))
                     <= _MAX_STORE_BYTES, 'declaration_budget')
            if revoke is None:
                final = revalidate_observed_file(observation, roots, identity_id=identity_id,
                                                 cancelled=lambda: cancelled() or not valid())
                _require(final.verified, 'file_changed_before_commit')
            _require(valid() and not cancelled(), 'source_scope_changed')
            committed.append(True)
            return DeclarationResult('declared' if revoke is None else 'revoked',
                                     relation_id=record['relation_id'], changed=1)
    except DeclarationRejected:
        raise
    except LockBusyError:
        raise DeclarationRejected('acquisition_busy') from None
    except (sqlite3.Error, OSError):
        raise DeclarationRejected('local_storage_unavailable') from None
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, UnicodeError):
        raise DeclarationRejected('invalid_local_context') from None


def inspect_declared_link(service, *, relation_id=None, observation, identity_id,
                          provider_id, credential_id, account_id, item_id,
                          cancelled=lambda: False):
    """Private read-only review after sync; never promote or silently erase.

    The original declaration is returned only to its exact active context.
    No receipt or relationship here can be consumed by the attested writers.
    """
    try:
        _require(relation_id is None or (type(relation_id) is str
                 and re.fullmatch('[0-9a-f]{32}', relation_id) is not None), 'invalid_relation')
        _require(type(observation) is FileObservation, 'invalid_observation')
        _require(callable(cancelled), 'invalid_cancellation')
        scope = dict(zip(_SCOPE_FIELDS, (identity_id, provider_id, credential_id,
                                        account_id, item_id)))
        roots, valid, _epoch = _scope(service, scope)
        with _transaction(service.storage, False) as db:
            records = [r for r in _records(db) if (
                r['relation_id'] == relation_id if relation_id is not None else
                r['state'] == 'active' and r['scope'] == scope
                and r['file']['path'] == observation.path)]
            _require(len(records) == 1 and records[0]['scope'] == scope,
                     'relation_scope_changed')
            record = records[0]
            relation_id = record['relation_id']
            context = _context(db, scope)
            fresh = revalidate_observed_file(observation, roots, identity_id=identity_id,
                                             cancelled=lambda: cancelled() or not valid())
            _require(valid() and not cancelled(), 'source_scope_changed')
            # A disabled source connection remains historically recorded,
            # never a currently reviewable supplier context. Even if file
            # bytes and the stored loan state are unchanged, the statement
            # must be shown as needing review rather than "current".
            matches = (record['file'] == _file(observation)
                       and fresh.verified
                       and context['connection']['enabled'] == 1)
            try:
                _collisions(db, scope, observation, _records(db))
            except DeclarationRejected:
                matches = False
            status = ('revoked' if record['state'] == 'revoked' else
                      'declared_current' if matches and record['right_state'] == _right_state(context)
                      else 'needs_review')
            return DeclarationResult(status, relation_id=relation_id, before=record)
    except DeclarationRejected:
        raise
    except (sqlite3.Error, OSError):
        raise DeclarationRejected('local_storage_unavailable') from None
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, UnicodeError):
        raise DeclarationRejected('invalid_local_context') from None


@dataclass(frozen=True, repr=False)
class DeclaredLinkView:
    """Private descriptive record, never evidence that a supplier link exists."""

    relation_id: str
    provider_id: str
    identity_id: str
    account_id: str
    item_id: str
    file_path: str
    created_at: int
    state: str
    review: str

    def __repr__(self):
        return '<DeclaredLinkView private>'


@dataclass(frozen=True, repr=False)
class DeclaredLinksPage:
    """Identity-scoped, bounded and explicitly non-attested consultation."""

    rows: tuple[DeclaredLinkView, ...]
    total: int
    offset: int
    limited: bool

    def __repr__(self):
        return '<DeclaredLinksPage private>'


def list_declared_links(service, *, identity_id, offset=0, limit=50,
                        include_revoked=False):
    """Read persisted declarations after sync without granting any authority.

    A fresh file check is *not* performed here. Even an unchanged right can
    only be presented as recorded; a paired file must be revalidated via the
    explicit inspection workflow before any consequential action.

    A disappeared connection/right remains visible as needing review so
    subsequent sync cannot silently erase the history of a user statement.
    """
    try:
        _require(_text(identity_id), 'invalid_identity')
        _require(type(offset) is int and 0 <= offset <= _MAX_RECORDS
                 and type(limit) is int and 1 <= limit <= 100
                 and type(include_revoked) is bool, 'invalid_page')
        current = service.current_identity
        _require(current is not None and
                 current.identity_id == identity_id, 'identity_not_active')
        storage, config = service.storage, service.config
        accounts, identities = service.accounts, service.identities
        epoch, data = service._account_context_epoch, config.data

        def valid():
            return (service.current_identity is current
                    and current.identity_id == identity_id
                    and service.storage is storage and service.config is config
                    and service.accounts is accounts and service.identities is identities
                    and service._account_context_epoch == epoch and config.data is data)

        with _transaction(storage, False) as db:
            _require(valid(), 'identity_not_active')
            result = []
            for record in _records(db):
                scope = record['scope']
                if scope['identity_id'] != identity_id:
                    continue
                if record['state'] == 'revoked':
                    if not include_revoked:
                        continue
                    review = 'revoked'
                else:
                    # A persisted right alone is not a live account: the user
                    # may have disconnected this credential while old SQLite
                    # provider_connections and entitlements remain intact.
                    # Mark that declaration as needing review, not as a
                    # current supplier association. Never delete it.
                    live_accounts = [
                        account for account in service.accounts
                        if account.provider_id == scope['provider_id']
                        and account.credential_id == scope['credential_id']
                    ]
                    live_account_matches = (
                        len(live_accounts) == 1
                        and live_accounts[0].identity_id == scope['identity_id']
                        and live_accounts[0].unique_id == scope['account_id']
                    )
                    if not live_account_matches:
                        review = 'source_or_right_changed'
                    else:
                        # This checks the current SQLite account/right only;
                        # neither supplier login nor file SHA is revalidated.
                        try:
                            context = _context(db, scope)
                            connection_enabled = context['connection']['enabled'] == 1
                            review = ('recorded_unverified_file'
                                      if connection_enabled
                                      and record['right_state'] == _right_state(context)
                                      else 'source_or_right_changed')
                        except DeclarationRejected:
                            review = 'source_or_right_changed'
                result.append(DeclaredLinkView(
                    relation_id=record['relation_id'],
                    provider_id=scope['provider_id'],
                    identity_id=scope['identity_id'],
                    account_id=scope['account_id'],
                    item_id=scope['item_id'],
                    file_path=record['file']['path'],
                    created_at=record['created_at'],
                    state=record['state'],
                    review=review,
                ))
            _require(valid(), 'identity_not_active')
        result.sort(key=lambda row: (row.created_at, row.relation_id), reverse=True)
        return DeclaredLinksPage(tuple(result[offset:offset + limit]),
                                 len(result), offset, len(result) > offset + limit)
    except DeclarationRejected:
        raise
    except (sqlite3.Error, OSError):
        raise DeclarationRejected('local_storage_unavailable') from None
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError,
            UnicodeError):
        raise DeclarationRejected('invalid_local_context') from None


def withdraw_declared_link(service, *, identity_id, relation_id,
                           confirmation=None, cancelled=lambda: False,
                           on_committed=None):
    """Explicitly withdraw one *user* statement, even when its book is gone.

    This is deliberately NOT an attested-placement undo, entitlement removal,
    orphan cleanup or source operation. The active profile may retract its
    own previously recorded assertion after a file was moved/deleted or the
    provider connection/right expired, without rebuilding a fictitious file
    observation or reactivating the provider account.

    Preview and confirmation run the same snapshot checks under the normal
    SQLite writer and acquisition locks; no account, file or proof mutation.
    """
    try:
        _require(_text(identity_id), 'invalid_identity')
        _require(type(relation_id) is str
                 and re.fullmatch('[0-9a-f]{32}', relation_id) is not None,
                 'invalid_relation')
        _require(confirmation is None or (type(confirmation) is str
                 and re.fullmatch('[0-9a-f]{64}', confirmation) is not None),
                 'invalid_confirmation')
        _require(callable(cancelled), 'invalid_cancellation')
        _require(on_committed is None or callable(on_committed),
                 'invalid_commit_hook')
        current = service.current_identity
        _require(current is not None and current.identity_id == identity_id,
                 'identity_not_active')
        config, storage = service.config, service.storage
        accounts, identities = service.accounts, service.identities
        epoch, data = service._account_context_epoch, config.data

        def valid():
            try:
                return (service.current_identity is current
                        and current.identity_id == identity_id
                        and service.config is config and config.data is data
                        and service.storage is storage
                        and service.accounts is accounts
                        and service.identities is identities
                        and service._account_context_epoch == epoch)
            except Exception:
                return False

        write = confirmation is not None
        lock = InterProcessLock(acquisition_lock_file()) if write else nullcontext()
        committed = []

        def notify_commit():
            if committed and on_committed is not None:
                on_committed()

        with lock, _transaction(storage, write, notify_commit) as db:
            _require(valid() and not cancelled(), 'source_scope_changed')
            records = _records(db)
            matches = [r for r in records if (
                r['relation_id'] == relation_id
                and r['scope']['identity_id'] == identity_id
            )]
            _require(len(matches) == 1, 'relation_scope_changed')
            before = matches[0]
            if before['state'] == 'revoked':
                return DeclarationResult('already_revoked',
                                         relation_id=relation_id)
            after = {**before, 'state': 'revoked'}
            token = _token({
                'operation': 'withdraw-user-declaration/1',
                'identity': identity_id, 'relation': relation_id,
                'database': str(storage.path), 'epoch': epoch,
                'records': records, 'before': before, 'after': after,
            })
            if not write:
                return DeclarationResult('revoke_preview', token,
                                         relation_id, before=before, after=after)
            _require(confirmation == token, 'preview_changed')
            final = {**after, 'revoked_at': int(time.time())}
            _require(len(_json(final)) <= _MAX_RECORD_BYTES,
                     'declaration_budget')
            db.execute('UPDATE metadata SET value=? WHERE key=?',
                       (_json(final), _PREFIX + relation_id))
            _require(db.execute('SELECT changes()').fetchone()[0] == 1,
                     'relation_scope_changed')
            _require(valid() and not cancelled(), 'source_scope_changed')
            committed.append(True)
            return DeclarationResult('revoked', relation_id=relation_id,
                                     changed=1)
    except DeclarationRejected:
        raise
    except LockBusyError:
        raise DeclarationRejected('acquisition_busy') from None
    except (sqlite3.Error, OSError):
        raise DeclarationRejected('local_storage_unavailable') from None
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError,
            UnicodeError):
        raise DeclarationRejected('invalid_local_context') from None
