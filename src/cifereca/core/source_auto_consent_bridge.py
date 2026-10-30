"""#3171: scoped compatibility bridge from existing acquisition switches to A→B.

This is *only* a consent policy adapter. The canonical Core executor retains
all exact SourceKey/offer/ownership, global lock, ADEPT, SHA and atomic B guards.
Do not grant from inventory enabled, backup_items, selected UI profile or
presence of a local file. Remove this bridge when generic per-provider/type
consent is exposed through the versioned plugin/source policy contract.
"""
from __future__ import annotations

import hashlib
import re

from .source_convergence import SourceKey

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
    identity, provider, credential, account = (
        scope.identity_id, scope.provider_id, scope.credential_id,
        scope.account_id,
    )
    # Match the public Sync broker's stable identifier, including its legacy
    # primary-account fallback. Empty account IDs are not wildcard grants.
    unique = account.strip() or f"{provider}:{credential}:primary"
    raw = "\0".join((identity, provider, credential, unique))
    return "sync:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def configured_kind(provider_id, kind):
    """Whether an existing source/type-wide acquisition switch is available."""
    return (provider_id, kind) in _POLICIES


def toggle_state(db, source: SourceKey):
    """Read the current committed mode INSIDE the caller's SQLite snapshot.

    'on' grants only eligible exact references via the canonical admission;
    'off' explicitly revokes them; None means no compatible opt-in was set.
    A malformed value grants nothing. This method never writes metadata.
    """
    if type(source) is not SourceKey:
        return "off"
    spec = _POLICIES.get((source.scope.provider_id, source.kind))
    if spec is None:
        return None
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


def numilog_generation(storage, scope):
    """Opaque consent epoch for this exact account; None if unavailable.

    This epoch changes on Numilog revoke/re-opt-in. Pinning it prevents an
    ON→OFF→ON race from reviving a previously admitted delivery.
    """
    if getattr(scope, "provider_id", None) != "numilog":
        return None
    try:
        value = storage.metadata_get(
            "sync.source.numilog.materialization.epoch.v1." + _source_id(scope)
        )
        return value if type(value) is str and _HEX.fullmatch(value) else None
    except Exception:
        return None


def numilog_generation_in_transaction(db, source):
    """Read the same generation from the canonical publisher's transaction."""
    if type(source) is not SourceKey or source.scope.provider_id != "numilog":
        return None
    row = db.execute(
        "SELECT value FROM metadata WHERE key=?",
        ("sync.source.numilog.materialization.epoch.v1." +
         _source_id(source.scope),),
    ).fetchone()
    value = row[0] if row else None
    return value if type(value) is str and _HEX.fullmatch(value) else None


def _consent_generation_key(scope):
    """Generation key for every source-wide automatic acquisition switch."""
    sid = _source_id(scope)
    if scope.provider_id == "numilog":
        return "sync.source.numilog.materialization.epoch.v1." + sid
    if scope.provider_id == "kobo":
        return "sync.source.materialization.on_start." + sid + ".revision"
    if scope.provider_id == "bnf-pret":
        return "sync.source.bnf_auto." + sid + ".revision"
    return None


def consent_generation(storage, scope):
    """Read pre-grant epoch (None means an unversioned legacy switch).

    An unreadable/malformed revision is never equivalent to no revision:
    deny rather than permitting a stale ON->OFF->ON delivery.
    """
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
