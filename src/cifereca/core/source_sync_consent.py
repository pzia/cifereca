"""#3719: explicit, revocable acquisition consent for one whole account-source.

No right/resource/title/SHA is part of the policy key. One atomic metadata row
contains scope, ON/OFF and a generation, so revocation/re-opt-in cannot revive
an older GET. Missing configuration never migrates a legacy grant implicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
from inspect import getattr_static
import hashlib
import json
import logging
import re
import secrets

from ..plugin_api.provider_rights_observation import ProviderRightsScope

_SCHEMA = 'cifereca.source-sync-consent/1'
_PREFIX = 'sync.source.acquisition-policy.v1.'
_HEX = re.compile(r'[0-9a-f]{32}\Z')
_LOG = logging.getLogger('cifereca')


@dataclass(frozen=True)
class Policy:
    mode: str
    revision: str | None

    @property
    def configured(self):
        return self.mode != 'unset'


def scope_for_account(account):
    return ProviderRightsScope(account.identity_id, account.provider_id,
                               account.credential_id, account.unique_id)


def policy_key(scope):
    if type(scope) is not ProviderRightsScope:
        raise ValueError('invalid_source_sync_consent_scope')
    coords = [scope.identity_id, scope.provider_id, scope.credential_id, scope.account_id]
    encoded = json.dumps(coords, ensure_ascii=True, separators=(',', ':'))
    return _PREFIX + hashlib.sha256(encoded.encode('ascii')).hexdigest()


def _decode(raw, scope):
    if raw is None:
        return Policy('unset', None)
    # An opaque fingerprint makes a corrupt record explicitly repairable via
    # CAS without treating it as a missing preference or implicit permission.
    invalid = Policy('invalid', 'invalid:' + hashlib.sha256(str(raw).encode()).hexdigest())
    if type(raw) is not str or len(raw) > 65536:
        return invalid
    try:
        row = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        return invalid
    if (type(row) is not dict
            or set(row) != {'schema', 'scope', 'enabled', 'revision'}
            or row['schema'] != _SCHEMA
            or row['scope'] != [scope.identity_id, scope.provider_id,
                                scope.credential_id, scope.account_id]
            or type(row['enabled']) is not bool
            or type(row['revision']) is not str
            or not _HEX.fullmatch(row['revision'])):
        return invalid
    return Policy('on' if row['enabled'] else 'off', row['revision'])


def policy_in_transaction(db, scope):
    row = db.execute('SELECT value FROM metadata WHERE key=?', (policy_key(scope),)).fetchone()
    return _decode(row[0] if row else None, scope)


def read_policy(storage, scope):
    return _decode(storage.metadata_get(policy_key(scope)), scope)


def configured(service, account):
    """Fail closed for legacy dispatch if its authoritative policy is unreadable."""
    try:
        return read_policy(service.storage, scope_for_account(account)).configured
    except Exception:
        _LOG.warning('Source acquisition policy read failed; legacy acquisition disabled', exc_info=True)
        return True


def supported(service, account):
    """Local optional ABI discovery; never inventories or contacts a provider."""
    try:
        session = service._session_for_account(account)
        return (session.provider_id == account.provider_id
                and session.credential_id == account.credential_id
                and all(callable(getattr_static(session, name, None)) for name in (
                    'downloadable_resource_snapshot', 'downloadable_selected_offer_snapshot',
                    'downloadable_selected_delivery_candidate',
                )))
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def source_sync_consent(service, account):
    scope = scope_for_account(account)
    state = read_policy(service.storage, scope)
    return {'supported': supported(service, account), 'configured': state.configured,
            'enabled': state.mode == 'on', 'revision': state.revision,
            'valid': state.mode != 'invalid'}


def set_source_sync_consent(service, account, *, enabled, expected_revision):
    """Explicit source action, CAS, no provider I/O, no SourceReference prerequisite.

    OFF remains possible when disconnected/disabled or after ABI withdrawal.
    No old grant is expanded: only this call can create a generic source policy.
    """
    from .source_acquisition_consent import _context, _bound

    if type(enabled) is not bool or (expected_revision is not None and type(expected_revision) is not str):
        raise ValueError('invalid_source_sync_consent_request')
    storage, coords, current = _context(service, account)
    scope = scope_for_account(account)
    if enabled and (not supported(service, account) or service.sync_source_enabled(account) is not True):
        raise ValueError('source_sync_consent_not_supported')
    with storage._lock, storage._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if not current() or not _bound(db, coords, enabled=enabled):
            raise ValueError('source_sync_consent_scope_changed')
        if enabled:
            from .source_auto_consent_bridge import _source_id
            from .source_enablement import source_enabled
            row = db.execute('SELECT value FROM metadata WHERE key=?',
                             ('sync.source.enabled.' + _source_id(scope),)).fetchone()
            if not source_enabled(row[0] if row else None):
                raise ValueError('source_sync_consent_source_disabled')
        state = policy_in_transaction(db, scope)
        if state.revision != expected_revision:
            raise ValueError('source_sync_consent_changed')
        payload = json.dumps({
            'schema': _SCHEMA, 'scope': list(coords), 'enabled': enabled,
            'revision': secrets.token_hex(16),
        }, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
        db.execute('INSERT INTO metadata(key,value) VALUES(?,?) '
                   'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   (policy_key(scope), payload))
        if not current():
            raise ValueError('source_sync_consent_scope_changed')
    return source_sync_consent(service, account)


def set_source_enabled(service, account, enabled, preference_key):
    """Atomic source disable + consent invalidation for configured sources.

    Re-enabling inventory never resurrects a revoked acquisition permission.
    Even OFF->ON inventory toggles advance the same source generation.
    """
    from .source_acquisition_consent import _context, _bound
    from .source_enablement import source_enabled
    storage, coords, current = _context(service, account)
    scope = scope_for_account(account)
    with storage._lock, storage._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if not current() or not _bound(db, coords, enabled=False):
            raise ValueError('source_sync_consent_scope_changed')
        state = policy_in_transaction(db, scope)
        if not state.configured:
            raise ValueError('source_sync_consent_unconfigured')
        row = db.execute('SELECT value FROM metadata WHERE key=?', (preference_key,)).fetchone()
        previous_enabled = source_enabled(row[0] if row else None)
        payload = json.dumps({
            'schema': _SCHEMA, 'scope': list(coords),
            'enabled': bool(enabled and previous_enabled and state.mode == 'on'),
            'revision': secrets.token_hex(16),
        }, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
        for key, value in ((preference_key, '1' if enabled else '0'),
                           (policy_key(scope), payload)):
            db.execute('INSERT INTO metadata(key,value) VALUES(?,?) '
                       'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))
        if not current():
            raise ValueError('source_sync_consent_scope_changed')
