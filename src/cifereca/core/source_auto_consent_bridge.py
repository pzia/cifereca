"""#3171: scoped compatibility bridge from existing acquisition switches to A→B.

This is *only* a consent policy adapter. The canonical Core executor retains
all exact SourceKey/offer/ownership, global lock, ADEPT, SHA and atomic B guards.
Do not grant from inventory enabled, backup_items, selected UI profile or
presence of a local file. Remove this bridge when generic per-provider/type
consent is exposed through the versioned plugin/source policy contract.
"""
from __future__ import annotations

import logging

from .source_convergence import SourceKey
from .legacy_source_account_id import ambiguous_legacy_source_account_id
from .source_sync_consent import (
    inventory_epoch_key, read_policy, policy_in_transaction,
)

# Re-export historical names for existing legacy readers/tests. Metadata-key
# lookup carries no consent decision, provider GET or acquisition permission.
from .legacy_source_consent_metadata import (
    _HEX, _source_id, historical_consent_keys, historical_generation,
    legacy_source_consent_switch_present,
)


def toggle_state(db, source: SourceKey):
    """Read the current committed mode INSIDE the caller's SQLite snapshot.

    Only explicit source CAS 'on' grants whole-source acquisition.
    Any historical source-wide switch, purchase OR loan, is negative
    compatibility evidence pending explicit re-consent. Old 'on' (even
    with a valid epoch) must never admit a GET or override a revocation.
    None means no legacy veto exists against an exact-right permission.
    This method never writes metadata.
    """
    if type(source) is not SourceKey:
        return "off"
    try:
        generic = policy_in_transaction(db, source.scope)
        if generic.configured:
            from .source_enablement import source_enabled
            from .source_sync_consent import source_enablement_key
            enabled = db.execute(
                "SELECT value FROM metadata WHERE key=?",
                (source_enablement_key(source.scope),),
            ).fetchone()
            return ("on" if generic.mode == "on"
                    and source_enabled(enabled[0] if enabled else None) else "off")
    except Exception:
        # Failed CAS or enablement reads are not proof that there is no
        # policy. Falling back to an older ON after an unreadable CAS row
        # would resurrect a revoked or unverified automatic acquisition.
        return "off"
    # No alternative historical per-right grant can bypass an explicitly
    # disabled loan source. This runs inside the same SQLite transaction as
    # the final local publication guard.
    if source.kind == "loan":
        try:
            from .source_sync_consent import source_enablement_key
            from .source_enablement import source_enabled
            enabled_row = db.execute(
                "SELECT value FROM metadata WHERE key=?",
                (source_enablement_key(source.scope),),
            ).fetchone()
            if not source_enabled(enabled_row[0] if enabled_row else None):
                return "off"
        except Exception:
            return "off"
    # The old Sync source-id encoding maps an empty primary borrower to the
    # literal text "provider:credential:primary". A real upstream borrower
    # can have exactly that ID. A legacy source-wide ON row cannot identify
    # which of those TWO accounts opted in, even when only one remains in the
    # current account list. Deny the ambiguous *literal* borrower until it
    # receives the new exact-scope, CAS-versioned source policy above.
    scope = source.scope
    if ambiguous_legacy_source_account_id(
        scope.provider_id, scope.credential_id, scope.account_id
    ):
        return "off"
    # The digest binds the original owner/source; the frozen formats carry
    # only the historical kind. A matching row is always a veto, even if
    # its namespace does not correspond to the source's present provider.
    try:
        present = any(
            db.execute("SELECT 1 FROM metadata WHERE key=?", (key,)).fetchone()
            is not None
            for key, _old_on, _epoch in historical_consent_keys(scope, source.kind)
        )
    except Exception:
        return "off"
    return "off" if present else None





def consent_generation(storage, scope):
    """Read pre-grant epoch (None means an unversioned legacy switch).

    An unreadable/malformed revision is never equivalent to no revision:
    deny rather than permitting a stale ON->OFF->ON delivery.
    """
    try:
        generic = read_policy(storage, scope)
    except Exception:
        logging.getLogger('cifereca').warning('Source consent generation read failed code=policy_unavailable')
        return "unavailable"
    if generic.configured:
        return ("source:" + generic.revision) if generic.mode != "invalid" else "invalid"
    try:
        inventory = storage.metadata_get(inventory_epoch_key(scope))
        if inventory is not None:
            return ("inventory:" + inventory if type(inventory) is str
                    and _HEX.fullmatch(inventory) else "invalid")
        # Untouched historical installations retain their previous fence.
        return historical_generation(scope, storage.metadata_get)
    except Exception:
        return "unavailable"


def consent_generation_in_transaction(db, source):
    """Read the exact epoch under Core's final BEGIN IMMEDIATE publication."""
    if type(source) is not SourceKey:
        return "invalid"
    try:
        generic = policy_in_transaction(db, source.scope)
    except Exception:
        logging.getLogger('cifereca').warning('Source consent transaction generation read failed code=policy_unavailable')
        return "unavailable"
    if generic.configured:
        return ("source:" + generic.revision) if generic.mode != "invalid" else "invalid"
    try:
        row = db.execute(
            "SELECT value FROM metadata WHERE key=?",
            (inventory_epoch_key(source.scope),),
        ).fetchone()
        if row is not None:
            inventory = row[0]
            return ("inventory:" + inventory if type(inventory) is str
                    and _HEX.fullmatch(inventory) else "invalid")
        def read(key):
            row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            return row[0] if row else None

        return historical_generation(source.scope, read)
    except Exception:
        return "unavailable"


def legacy_source_auto_policy(storage, scope, kind: str) -> tuple[str, str | None]:
    """Read one explicitly configured legacy SOURCE consent (never a right).

    Descriptive compatibility only, with no production admission caller.
    Exactly one format row and a valid revision are required for display;
    even a reported historical ON cannot authorize a GET or publication.
    """
    from ..plugin_api.provider_rights_observation import ProviderRightsScope
    if type(scope) is not ProviderRightsScope or type(kind) is not str:
        return "off", None
    if ambiguous_legacy_source_account_id(
        scope.provider_id, scope.credential_id, scope.account_id,
    ):
        return "off", None
    try:
        from .source_sync_consent import source_enablement_key
        from .source_enablement import source_enabled
        if not source_enabled(storage.metadata_get(source_enablement_key(scope))):
            return "off", None
        present = [
            (value, active, revision_key)
            for switch, active, revision_key in historical_consent_keys(scope, kind)
            if (value := storage.metadata_get(switch)) is not None
        ]
        if len(present) != 1:
            return "off", None
        value, active, revision_key = present[0]
        revision = storage.metadata_get(revision_key)
    except Exception:
        return "off", None
    if type(revision) is not str or _HEX.fullmatch(revision) is None:
        return "off", None
    return ("on" if value == active else "off"), revision


def legacy_source_auto_current(storage, source: SourceKey, revision: str) -> bool:
    """Retired compatibility ABI; historical ON never authorizes a GET."""
    return False


def legacy_source_auto_in_transaction(db, source: SourceKey, revision: str) -> bool:
    """Retired transaction ABI; no old ON can publish a downloaded file."""
    return False

