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
