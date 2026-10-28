"""#3171: Core-owned durable opt-in for one exact observed SourceKey.

Consent is neither a provider right nor an offer/receipt. No legacy global,
provider, title, SHA or transfer preference is promoted to this permission.
The existing executor must still check the current offer, session and bytes.
"""
from __future__ import annotations

import hashlib
import json

from ..models import Account
from .canonical_publication import _stable
from .source_convergence import SourceKey
from ..plugin_api.provider_rights_observation import (
    ProviderRightObservation, ProviderRightsScope,
)

_SCHEMA = "cifereca.source.acquisition-consent/1"
_PREFIX = "sync.canonical.acquisition-consent."
_LIMIT = 100_000


def _coordinates(source: SourceKey) -> tuple[str, ...]:
    scope = source.scope
    return (scope.identity_id, scope.provider_id, scope.credential_id,
            scope.account_id, source.branch, source.edition_id,
            source.right_id, source.kind)


def consent_key(source: SourceKey) -> str:
    if type(source) is not SourceKey:
        raise ValueError("invalid_acquisition_consent_source")
    # JSON preserves boundaries even when opaque IDs contain separators.
    encoded = json.dumps(_coordinates(source), ensure_ascii=True, separators=(",", ":"))
    return _PREFIX + hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _attested(source, evidence_ref, observed_at):
    try:
        return ProviderRightObservation(
            source.edition_id, source.right_id, source.kind, "active",
            "provider_response", evidence_ref, observed_at,
        ).provider_attested
    except ValueError:
        return False


def _context(service, account):
    if type(account) is not Account:
        raise ValueError("invalid_acquisition_consent_account")
    storage, accounts, epoch = service.storage, service.accounts, service._account_context_epoch
    scope = (account.identity_id, account.provider_id, account.credential_id, account.unique_id)

    def current():
        try:
            matching = [a for a in service.accounts if type(a) is Account and (
                a.identity_id, a.provider_id, a.credential_id, a.unique_id,
            ) == scope]
            return (service.storage is storage and service.accounts is accounts
                    and service._account_context_epoch == epoch
                    and len(matching) == 1 and matching[0] is account
                    and (account.identity_id, account.provider_id,
                         account.credential_id, account.unique_id) == scope)
        except (AttributeError, TypeError):
            return False

    return storage, scope, current


def _bound(db, scope, *, enabled):
    identity, provider, credential, account = scope
    binding = db.execute(
        "SELECT identity_id,enabled FROM provider_connections "
        "WHERE provider_id=? AND credential_id=?", (provider, credential),
    ).fetchone()
    return (binding is not None and binding[0] == identity
            and (not enabled or binding[1] == 1)
            and db.execute("SELECT 1 FROM identities WHERE identity_id=?", (identity,)).fetchone() is not None
            and db.execute(
                "SELECT count(*) FROM provider_accounts WHERE identity_id=? "
                "AND provider_id=? AND credential_id=? AND account_id=?", scope,
            ).fetchone()[0] == 1)


def set_source_acquisition_consent(service, account: Account, source: SourceKey, *, allowed: bool) -> None:
    """Persist an explicitly requested grant/revocation, without remote I/O.

    Grant only a known positive synchronizable reference on an enabled Core
    binding. Revocation needs no current right or enabled source. No caller
    may treat a successful grant as an acquisition offer or a completed GET.
    """
    if type(source) is not SourceKey or type(allowed) is not bool:
        raise ValueError("invalid_acquisition_consent_request")
    storage, scope, current = _context(service, account)
    coordinates = _coordinates(source)
    if coordinates[:4] != scope or not current():
        raise ValueError("acquisition_consent_scope_changed")
    if allowed and service.sync_source_enabled(account) is not True:
        raise ValueError("acquisition_consent_source_disabled")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not current() or not _bound(db, scope, enabled=allowed):
            raise ValueError("acquisition_consent_scope_changed")
        if allowed:
            row = db.execute(
                "SELECT identity_id,provider_id,credential_id,account_id,branch_id,"
                "edition_id,right_id,reference_kind,state,synchronizable,evidence_kind,evidence_ref,observed_at "
                "FROM source_references WHERE source_reference_id=?",
                (_stable("source", *coordinates),),
            ).fetchone()
            if (row is None or row[:11] != (*coordinates, "active", 1, "provider_response")
                    or not _attested(source, row[11], row[12])):
                raise ValueError("acquisition_consent_positive_source_required")
        payload = json.dumps({"schema": _SCHEMA, "source": coordinates, "allowed": allowed},
                             ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        db.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (consent_key(source), payload),
        )
        if not current():
            raise ValueError("acquisition_consent_scope_changed")


def consent_current_in_transaction(db, source: SourceKey) -> bool:
    """Recheck the durable exact permission under publication's SQL lock.

    No Storage calls, commits, network or filesystem work. BEGIN IMMEDIATE
    serializes a concurrent external revocation with canonical publication.
    """
    if type(source) is not SourceKey:
        return False
    record = db.execute("SELECT value FROM metadata WHERE key=?", (consent_key(source),)).fetchone()
    return record is not None and _payload_allowed(record[0], source)


def _payload_allowed(raw, source):
    if type(raw) is not str or len(raw) > 65_536:
        return False
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        return False
    return (type(payload) is dict and payload.get("schema") == _SCHEMA
            and payload.get("source") == list(_coordinates(source))
            and payload.get("allowed") is True)


def read_source_acquisition_consent(service, account: Account) -> frozenset[SourceKey]:
    """Read fresh persisted permission for current positive exact references.

    Missing, malformed, legacy, inactive and unbound records grant nothing.
    All rows and permissions are read in one SQLite snapshot, never cached.
    """
    storage, scope, current = _context(service, account)
    if not current() or service.sync_source_enabled(account) is not True:
        return frozenset()
    admitted = set()
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        if not current() or not _bound(db, scope, enabled=True):
            return frozenset()
        rows = db.execute(
            "SELECT branch_id,edition_id,right_id,reference_kind,evidence_ref,observed_at FROM source_references "
            "WHERE identity_id=? AND provider_id=? AND credential_id=? AND account_id=? "
            "AND state='active' AND synchronizable=1 AND evidence_kind='provider_response' "
            "AND reference_kind IN ('purchase','loan') "
            "LIMIT ?", (*scope, _LIMIT + 1),
        ).fetchall()
        if len(rows) > _LIMIT:
            return frozenset()
        for branch, edition, right, kind, evidence_ref, observed_at in rows:
            try:
                source = SourceKey(ProviderRightsScope(*scope), edition, right, kind, branch)
            except ValueError:
                continue
            if not _attested(source, evidence_ref, observed_at):
                continue
            record = db.execute("SELECT value FROM metadata WHERE key=?", (consent_key(source),)).fetchone()
            if record is not None and _payload_allowed(record[0], source):
                admitted.add(source)
        return frozenset(admitted) if current() else frozenset()
