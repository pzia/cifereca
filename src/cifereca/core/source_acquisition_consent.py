"""#3171: Core-owned durable opt-in for one exact observed SourceKey.

Consent is neither a provider right nor an offer/receipt. No legacy global,
provider, title, SHA or transfer preference is promoted to this permission.
The existing executor must still check the current offer, session and bytes.
"""
from __future__ import annotations

from .source_context import source_context_epoch

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
    storage, accounts, epoch = service.storage, service.accounts, source_context_epoch(service)
    scope = (account.identity_id, account.provider_id, account.credential_id, account.unique_id)

    def current():
        try:
            matching = [a for a in service.accounts if type(a) is Account and (
                a.identity_id, a.provider_id, a.credential_id, a.unique_id,
            ) == scope]
            return (service.storage is storage and service.accounts is accounts
                    and source_context_epoch(service) == epoch
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


# Broker/UI controls: bounded pages, exact identity, never a bearer token.
# A source_reference_id is the Core's SHA-based opaque primary key. Expose
# only its 64-hex fingerprint; resolve it through SQLite's exact indexed PK
# at the mutation boundary. This scales to >500 Kobo rights without granting
# any future, ambiguous, foreign, or unattested right.
_CHOICES_LIMIT = 48
_MAX_CHOICE_PAGE = 2048


def _choices(service, account, *, page: int = 0):
    if type(page) is not int or not 0 <= page <= _MAX_CHOICE_PAGE:
        raise ValueError("invalid_canonical_consent_page")
    storage, scope, current = _context(service, account)
    if not current():
        return (), current
    # Metadata reads must not recurse into Storage._lock.
    enabled = service.sync_source_enabled(account) is True
    with storage._lock, storage._connect() as db:
        if not current() or not _bound(db, scope, enabled=False):
            return (), current
        rows = db.execute(
            "SELECT source_reference_id,branch_id,edition_id,right_id,"
            "reference_kind,state,synchronizable,evidence_kind,evidence_ref,"
            "observed_at FROM source_references "
            "WHERE identity_id=? AND provider_id=? AND credential_id=? "
            "AND account_id=? AND reference_kind IN ('purchase','loan') "
            "ORDER BY source_reference_id LIMIT ? OFFSET ?",
            (*scope, _CHOICES_LIMIT, page * _CHOICES_LIMIT),
        ).fetchall()
        bound_for_grant = _bound(db, scope, enabled=True)
        options = []
        for source_id, branch, edition, right, kind, state, synchronizable, evidence_kind, evidence_ref, observed_at in rows:
            if type(source_id) is not str or not source_id.startswith("source:"):
                continue
            try:
                source = SourceKey(ProviderRightsScope(*scope), edition, right, kind, branch)
            except (ValueError, TypeError):
                continue
            if source_id != _stable("source", *_coordinates(source)):
                continue
            record = db.execute(
                "SELECT value FROM metadata WHERE key=?",
                (consent_key(source),),
            ).fetchone()
            opted_in = record is not None and _payload_allowed(record[0], source)
            admissible = (
                bound_for_grant and enabled
                and state == "active" and synchronizable == 1
                and evidence_kind == "provider_response"
                and _attested(source, evidence_ref, observed_at)
            )
            # Render even inactive records as disabled rows: ordering/paging
            # must not hide a later live right behind a legacy negative.
            options.append((source_id[len("source:"):], kind, opted_in, admissible))
        return tuple(options) if current() else (), current


def canonical_acquisition_choices(
    service, account: Account, *, page: int = 0,
) -> tuple[dict[str, object], ...]:
    """Offline page of exact-right selectors, never upstream/provider I/O."""
    rows, current = _choices(service, account, page=page)
    if not current():
        return ()
    return tuple({
        "reference": reference, "kind": kind, "allowed": allowed,
        "eligible": eligible,
    } for reference, kind, allowed, eligible in rows)


def set_canonical_acquisition_choice(
    service, account: Account, reference: str, *, allowed: bool,
) -> None:
    """Resolve an opaque primary key in SQLite, then revalidate one grant."""
    if (type(reference) is not str or len(reference) != 64
            or any(char not in "0123456789abcdef" for char in reference)
            or type(allowed) is not bool):
        raise ValueError("invalid_canonical_consent_selection")
    storage, scope, current = _context(service, account)
    if not current():
        raise ValueError("canonical_consent_selection_unavailable")
    with storage._lock, storage._connect() as db:
        row = db.execute(
            "SELECT branch_id,edition_id,right_id,reference_kind "
            "FROM source_references WHERE source_reference_id=? "
            "AND identity_id=? AND provider_id=? AND credential_id=? "
            "AND account_id=?",
            ("source:" + reference, *scope),
        ).fetchone()
    if row is None or not current():
        raise ValueError("canonical_consent_selection_unavailable")
    try:
        source = SourceKey(
            ProviderRightsScope(*scope), row[1], row[2], row[3], row[0],
        )
    except (ValueError, TypeError):
        raise ValueError("canonical_consent_selection_unavailable") from None
    if _stable("source", *_coordinates(source)) != "source:" + reference:
        raise ValueError("canonical_consent_selection_unavailable")
    # The existing BEGIN IMMEDIATE Core mutation independently checks enabled
    # source, positive A, exact account and last in-memory context. Revocation
    # is valid even after the positive SourceReference becomes inactive.
    set_source_acquisition_consent(service, account, source, allowed=allowed)
