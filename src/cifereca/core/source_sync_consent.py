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


def _unique_policy_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate_source_sync_consent_field')
        value[key] = item
    return value


def _decode(raw, scope):
    if raw is None:
        return Policy('unset', None)
    # An opaque fingerprint makes a corrupt record explicitly repairable via
    # CAS without treating it as a missing preference or implicit permission.
    invalid = Policy('invalid', 'invalid:' + hashlib.sha256(str(raw).encode()).hexdigest())
    if type(raw) is not str or len(raw) > 65536:
        return invalid
    try:
        row = json.loads(raw, object_pairs_hook=_unique_policy_object)
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
        _LOG.warning('Source acquisition policy read failed code=policy_unavailable')
        return True


def supported(service, account):
    """Local optional ABI discovery; never inventories or contacts a provider."""
    try:
        session = service._session_for_account(account)
        return (session.provider_id == account.provider_id
                and session.credential_id == account.credential_id
                and all(callable(getattr_static(session, name, None)) for name in (
                    'downloadable_resource_snapshot', 'downloadable_selected_offer_snapshot',
                ))
                # A guarded-only v2 producer needs no unguarded GET entry point:
                # Core supplies its live, source-revision-pinned admission lease.
                and any(callable(getattr_static(session, name, None)) for name in (
                    'downloadable_selected_delivery_candidate_runtime_guarded',
                    'downloadable_selected_delivery_candidate_guarded',
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


def source_enablement_key(scope):
    """Stable read-only inventory toggle key; never acquisition permission.

    The exact historical source digest is retained for persisted source
    enablement, independently from the retired acquisition-consent bridge.
    """
    from .legacy_source_identifier import legacy_source_id
    return "sync.source.enabled." + legacy_source_id(
        scope.identity_id, scope.provider_id,
        scope.credential_id, scope.account_id,
    )


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
            from .source_enablement import source_enabled
            row = db.execute('SELECT value FROM metadata WHERE key=?',
                             (source_enablement_key(scope),)).fetchone()
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



def retire_legacy_acquisition_on_source_disable(service, account, preference_key):
    """Atomically migrate a *user-requested source OFF* to canonical CAS OFF.

    A legacy ON must not survive an inventory OFF -> ON roundtrip. This never
    promotes historic ON into canonical consent, creates no source right and
    never rewrites a historical policy/epoch. Existing legacy readers observe
    canonical OFF before any further supplier I/O.
    """
    from .source_acquisition_consent import _context, _bound

    scope = scope_for_account(account)
    if type(preference_key) is not str or preference_key != source_enablement_key(scope):
        raise ValueError('source_sync_toggle_scope_mismatch')
    storage, coords, current = _context(service, account)
    with storage._lock, storage._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if not current() or not _bound(db, coords, enabled=False):
            raise ValueError('source_sync_consent_scope_changed')
        state = policy_in_transaction(db, scope)
        if state.configured:
            # The broker resolves the canonical policy separately. A stale
            # legacy writer must never overwrite a concurrent ON/OFF/invalid
            # revision or erase its CAS history.
            raise ValueError('source_sync_consent_changed')
        payload = json.dumps({
            'schema': _SCHEMA, 'scope': list(coords), 'enabled': False,
            'revision': secrets.token_hex(16),
        }, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
        for key, value in ((preference_key, '0'), (policy_key(scope), payload)):
            db.execute('INSERT INTO metadata(key,value) VALUES(?,?) '
                       'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (key, value))
        if not current():
            raise ValueError('source_sync_consent_scope_changed')



def set_source_enabled(service, account, enabled, preference_key):
    """Atomic source disable + consent invalidation for configured sources.

    Re-enabling inventory never resurrects a revoked acquisition permission.
    Even OFF->ON inventory toggles advance the same source generation.
    """
    # This internal write path is also callable without the public broker.
    # Never coerce a string like "false" to True or write a preference row
    # belonging to another borrower, credential or identity.
    if type(enabled) is not bool or type(preference_key) is not str:
        raise ValueError('invalid_source_sync_toggle')
    scope = scope_for_account(account)
    if preference_key != source_enablement_key(scope):
        raise ValueError('source_sync_toggle_scope_mismatch')
    from .source_acquisition_consent import _context, _bound
    from .source_enablement import source_enabled
    storage, coords, current = _context(service, account)
    with storage._lock, storage._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if not current() or not _bound(db, coords, enabled=False):
            raise ValueError('source_sync_consent_scope_changed')
        state = policy_in_transaction(db, scope)
        if not state.configured:
            raise ValueError('source_sync_consent_unconfigured')
        # Inventory ON is not an explicit repair of a corrupt acquisition
        # policy. Keep the opaque invalid CAS fingerprint until the user
        # explicitly revokes with OFF at the acquisition-consent boundary.
        if enabled and state.mode == 'invalid':
            raise ValueError('source_sync_consent_changed')
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



def _atomic_legacy_inventory_toggle(service, account, enabled, preference_key, scope):
    """Serialize legacy switch, CAS admission and revision into one SQL write.

    In-flight legacy operations retain the exact historical generation fence.
    A newly installed CAS policy, including invalid or OFF, always wins over
    a concurrent legacy inventory writer. Never migrate historic ON to ON.
    """
    from .source_acquisition_consent import _context, _bound
    from .legacy_source_consent_metadata import (
        _POLICIES, _source_id, _consent_generation_key,
    )

    storage, coords, current = _context(service, account)
    if not current():
        raise ValueError('source_sync_consent_scope_changed')
    specs = [
        spec for (provider, _kind), spec in _POLICIES.items()
        if provider == scope.provider_id
    ]
    legacy_toggle = (
        specs[0][0] + _source_id(scope) if len(specs) == 1 else None
    )
    revision_key = _consent_generation_key(scope)
    with storage._lock, storage._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        # A live in-memory Account is not proof that its persisted identity,
        # connection and account binding still belong to this source. Check
        # the authoritative SQLite binding in the same transaction as the
        # legacy policy and preference write; OFF remains allowed on a
        # disabled (but still registered) connection.
        if not current() or not _bound(db, coords, enabled=False):
            raise ValueError('source_sync_consent_scope_changed')
        # The caller's earlier read may be stale: re-admit under the same
        # SQLite writer lock that protects the outgoing preference/epoch.
        if policy_in_transaction(db, scope).configured:
            raise ValueError('source_sync_consent_changed')
        legacy_present = (
            legacy_toggle is not None
            and db.execute(
                'SELECT 1 FROM metadata WHERE key=?', (legacy_toggle,),
            ).fetchone() is not None
        )
        writes = [(preference_key, '1' if enabled else '0')]
        if not enabled and legacy_present:
            payload = json.dumps({
                'schema': _SCHEMA, 'scope': list(coords),
                'enabled': False, 'revision': secrets.token_hex(16),
            }, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
            writes.append((policy_key(scope), payload))
        elif revision_key is not None:
            writes.append((revision_key, secrets.token_hex(16)))
        for key, value in writes:
            db.execute(
                'INSERT INTO metadata(key,value) VALUES(?,?) '
                'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (key, value),
            )
        if not current():
            raise ValueError('source_sync_consent_scope_changed')


def set_inventory_source_enabled(service, account, enabled, preference_key):
    """Provider-neutral inventory toggle with a fail-closed legacy cutover.

    Only an explicit OFF of a persisted historical acquisition switch
    creates CAS OFF. ON never migrates an old preference to canonical ON.
    Legacy epoch writing remains temporary until #5077 can remove the
    compatibility readers without breaking already-started operations.
    """
    if type(enabled) is not bool:
        raise ValueError("invalid source enabled preference")
    scope = scope_for_account(account)
    # The legacy source digest aliases an empty primary account ID with the
    # literal upstream ID "provider:credential:primary". Unlike the new CAS
    # policy this inventory preference cannot distinguish those two owners.
    # Reject the ambiguous writer before it can mutate either account's
    # inventory preference, even when the supplied digest looks exact.
    from .legacy_source_account_id import ambiguous_legacy_source_account_id
    if ambiguous_legacy_source_account_id(
        scope.provider_id, scope.credential_id, scope.account_id,
    ):
        raise ValueError('source_sync_toggle_scope_mismatch')
    if preference_key != source_enablement_key(scope):
        raise ValueError('source_sync_toggle_scope_mismatch')
    # The public broker supplies its exact account object. An equal-but-
    # detached copy must not write source metadata on any compatibility path.
    # Production accounts additionally pin Core registry and context epoch.
    from ..models import Account
    if type(account) is Account:
        from .source_acquisition_consent import _context
        _storage, _coords, current = _context(service, account)
        if not current():
            raise ValueError('source_sync_consent_scope_changed')
    elif not any(row is account for row in service.accounts):
        raise ValueError('source_sync_consent_scope_changed')
    if configured(service, account):
        return set_source_enabled(service, account, enabled, preference_key)

    # Real Core accounts require one SQLite transaction for both the legacy
    # preference and its epoch/CAS cutover. Keep lightweight broker contract
    # fakes on their existing in-memory seam (no SQLite connection or Account).
    from ..models import Account
    if type(account) is Account:
        return _atomic_legacy_inventory_toggle(
            service, account, enabled, preference_key, scope,
        )
    from .source_acquisition_consent import legacy_inventory_enablement_toggle
    return legacy_inventory_enablement_toggle(
        service, account, enabled, preference_key, scope,
    )
