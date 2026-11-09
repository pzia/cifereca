"""#3171: scoped compatibility bridge from existing acquisition switches to A→B.

This is *only* a consent policy adapter. The canonical Core executor retains
all exact SourceKey/offer/ownership, global lock, ADEPT, SHA and atomic B guards.
Do not grant from inventory enabled, backup_items, selected UI profile or
presence of a local file. Remove this bridge when generic per-provider/type
consent is exposed through the versioned plugin/source policy contract.
"""
from __future__ import annotations

import re
import logging

from .source_convergence import SourceKey
from .legacy_source_account_id import ambiguous_legacy_source_account_id
from .source_sync_consent import read_policy, policy_in_transaction

# Scoped *automatic acquisition* policies, NOT source-inventory switches.
# The kind distinguishes purchase vs loan. No loan can inherit a purchase
# grant from another provider/account or the GUI's selected identity.
_POLICIES = {
    ("numilog", "purchase"): (
        "sync.source.numilog.materialization.consent.v1.", "on_start:v1",
        "sync.source.numilog.materialization.epoch.v1.",
    ),
    ("bnf-pret", "loan"): (
        "sync.source.bnf_auto.", "1", None,
    ),
    ("kobo", "purchase"): (
        "sync.source.materialization.on_start.", "1", None,
    ),
}
_HEX = re.compile(r"[0-9a-f]{32}\Z")


def _source_id(scope):
    from .legacy_source_identifier import legacy_source_id
    return legacy_source_id(
        scope.identity_id, scope.provider_id, scope.credential_id,
        scope.account_id,
    )


def toggle_state(db, source: SourceKey):
    """Read the current committed mode INSIDE the caller's SQLite snapshot.

    'on' grants only eligible exact references via the canonical admission;
    'off' explicitly revokes them; None means no compatible opt-in was set.
    A malformed value grants nothing. This method never writes metadata.
    """
    if type(source) is not SourceKey:
        return "off"
    generic = policy_in_transaction(db, source.scope)
    if generic.configured:
        from .source_enablement import source_enabled
        enabled = db.execute(
            "SELECT value FROM metadata WHERE key=?",
            ("sync.source.enabled." + _source_id(source.scope),),
        ).fetchone()
        return ("on" if generic.mode == "on"
                and source_enabled(enabled[0] if enabled else None) else "off")
    spec = _POLICIES.get((source.scope.provider_id, source.kind))
    if spec is None:
        return None
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
    prefix, active, epoch_prefix = spec
    sid = _source_id(source.scope)
    row = db.execute(
        "SELECT value FROM metadata WHERE key=?", (prefix + sid,),
    ).fetchone()
    if row is None:
        # An installation without this optional source-wide switch keeps
        # its legacy explicit per-right grants, not an implicit opt-out.
        return None
    if row[0] != active:
        return "off"
    if epoch_prefix is not None:
        revision = db.execute(
            "SELECT value FROM metadata WHERE key=?", (epoch_prefix + sid,),
        ).fetchone()
        if (revision is None or type(revision[0]) is not str
                or _HEX.fullmatch(revision[0]) is None):
            return "off"
    return "on"



def _consent_generation_key(scope):
    """Read the exact legacy epoch key from the same compatibility policy.

    The policy itself remains legacy until every provider has migrated to
    versioned CAS consent. Unknown or ambiguous provider mappings fail closed.
    """
    specs = [
        spec for (provider, _kind), spec in _POLICIES.items()
        if provider == scope.provider_id
    ]
    if len(specs) != 1:
        return None
    prefix, _active, epoch_prefix = specs[0]
    sid = _source_id(scope)
    return (epoch_prefix + sid if epoch_prefix is not None
            else prefix + sid + ".revision")


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
    key = _consent_generation_key(scope)
    if key is None:
        return None
    try:
        raw = storage.metadata_get(key)
    except Exception:
        return "unavailable"
    if raw is None:
        return None
    return raw if type(raw) is str and _HEX.fullmatch(raw) else "invalid"


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
    key = _consent_generation_key(source.scope)
    if key is None:
        return None
    try:
        row = db.execute(
            "SELECT value FROM metadata WHERE key=?", (key,),
        ).fetchone()
    except Exception:
        return "unavailable"
    raw = row[0] if row else None
    if raw is None:
        return None
    return raw if type(raw) is str and _HEX.fullmatch(raw) else "invalid"
