"""Read-only frozen key formats of obsolete acquisition metadata.

The source digest already includes provider, identity, credential and account.
Never select a format by supplier name. No historical row grants acquisition.
"""
from __future__ import annotations
import re

# Immutable storage formats, not an inventory of supported providers.
# Kind is the historical acquisition category, not an authorization.
_FORMATS = (
    ("purchase", "sync.source.numilog.materialization.consent.v1.",
     "on_start:v1", "sync.source.numilog.materialization.epoch.v1."),
    ("loan", "sync.source.bnf_auto.", "1", None),
    ("purchase", "sync.source.materialization.on_start.", "1", None),
)
_HEX = re.compile(r"[0-9a-f]{32}\Z")


def _source_id(scope):
    from .legacy_source_identifier import legacy_source_id
    return legacy_source_id(
        scope.identity_id, scope.provider_id, scope.credential_id,
        scope.account_id,
    )


def historical_consent_keys(scope, kind=None):
    """All historical key formats for this exact source, never provider policy."""
    sid = _source_id(scope)
    return tuple(
        (prefix + sid, old_on,
         epoch_prefix + sid if epoch_prefix is not None else prefix + sid + ".revision")
        for stored_kind, prefix, old_on, epoch_prefix in _FORMATS
        if kind is None or stored_kind == kind
    )


def legacy_source_consent_switch_present(storage, scope) -> bool:
    """Detect stored negative compatibility evidence, never an opt-in."""
    return any(
        storage.metadata_get(key) is not None
        for key, _old_on, _epoch in historical_consent_keys(scope)
    )


def historical_generation(scope, read):
    """Read an untouched legacy fence, refusing ambiguous/malformed rows."""
    values = [
        value for _switch, _old_on, key in historical_consent_keys(scope)
        if (value := read(key)) is not None
    ]
    if not values:
        return None
    if len(values) != 1 or type(values[0]) is not str or not _HEX.fullmatch(values[0]):
        return "invalid"
    return values[0]
