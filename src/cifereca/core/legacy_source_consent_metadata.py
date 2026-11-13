"""Read-only metadata-key grammar of historical source acquisition switches.

Compatibility lookup only: these keys never authorize acquisition, confer a
right, or replace versioned CAS consent. Byte-stable SQLite namespace.
"""
from __future__ import annotations
import re

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


def legacy_source_consent_switch_present(storage, scope) -> bool:
    """Whether an exact historical *acquisition* switch exists, never its grant.

    A source with no legacy acquisition preference must retain ordinary
    inventory-only toggles: do not manufacture a generic policy for it.
    The literal-primary ambiguity can only be cleared by explicit fresh CAS.
    """
    try:
        specs = [
            spec for (provider, _kind), spec in _POLICIES.items()
            if provider == scope.provider_id
        ]
        if len(specs) != 1:
            return False
        prefix, _active, _revision = specs[0]
        return storage.metadata_get(prefix + _source_id(scope)) is not None
    except Exception:
        # A broken legacy preference cannot be used as migration evidence.
        return False



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

