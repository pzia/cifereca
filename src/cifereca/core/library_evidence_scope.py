"""Private identity scoping and redacted group counters for #2565.

The selected identity is an explicit capability, never guessed from a path,
ISBN, title or SHA. Unattested files have provider='unattributed', even when
an artifact with identical bytes belongs to another provider in SQLite.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .library_inventory import Inventory
from .library_reconciliation import EvidenceSnapshot, ReconciliationReport


def scope_evidence(snapshot: EvidenceSnapshot, identity_ids: tuple[str, ...]) -> EvidenceSnapshot:
    """Remove unrelated private rows, but KEEP conflicting acquisition owners."""
    if (not isinstance(identity_ids, tuple) or not 1 <= len(identity_ids) <= 128
            or any(type(v) is not str or not v for v in identity_ids)):
        raise ValueError('invalid_reconciliation_identity_scope')
    selected = set(identity_ids)
    tables = snapshot.tables
    rights = [r for r in tables.get('entitlements', []) if r.get('identity_id') in selected]
    # A cross-identity acquisition only proves a conflict against a fully
    # identified selected right. NULL/empty legacy source coordinates must
    # not join arbitrary foreign borrowers into this identity's private view.
    right_ids = set()
    sources = set()
    for right in rights:
        entitlement_id = right.get('entitlement_id')
        if type(entitlement_id) is str and entitlement_id.strip():
            right_ids.add(entitlement_id)
        # A credential may have multiple *borrowers* (e.g. Paris primary
        # and paired cards) holding the same edition. The account is part of
        # the source of an entitlement, never just the login. A foreign
        # ledger row with a different borrower does not contradict this
        # right, and must not enter the selected identity's private audit.
        # Still keep direct entitlement-ID collisions independently above.
        key = (right.get('provider_id'), right.get('credential_id'),
               right.get('account_id'), right.get('item_id'))
        if all(type(value) is str and value.strip() for value in key):
            sources.add(key)
    result = {}
    for table, rows in tables.items():
        if table == 'artifacts':
            # Content-addressed, no account, title, path or provenance fields.
            result[table] = rows
        elif table == 'acquisition_ledger':
            result[table] = [r for r in rows if r.get('identity_id') in selected
                             or r.get('entitlement_id') in right_ids
                             or (r.get('provider_id'), r.get('credential_id'),
                                 r.get('account_id'), r.get('item_id')) in sources]
        elif table == 'adept_profile_bindings':
            result[table] = [r for r in rows if r.get('scope_type') == 'identity' and r.get('scope_id') in selected]
        elif table == 'paris_borrower_adept_bindings':
            result[table] = [r for r in rows if r.get('connection_identity_id') in selected or r.get('borrower_identity_id') in selected]
        else:
            result[table] = [r for r in rows if r.get('identity_id') in selected]
    return EvidenceSnapshot(result, set(snapshot.reasons))


@dataclass(repr=False)
class GroupedEvidence:
    # Only the private local report maps ephemeral group indices to identity IDs.
    identities: tuple[str, ...]
    counts: dict[tuple[int, str], dict[str, int]] = field(default_factory=dict)
    adept_local_references: int = 0

    def redacted(self):
        return {'schema': 1, 'read_only': True, 'acquisition_authorized': False,
                'groups': [{'identity_index': identity_index, 'provider': provider, 'counts': counts}
                           for (identity_index, provider), counts in sorted(self.counts.items())],
                'adept_status': 'local_reference_only' if self.adept_local_references else 'not_requested',
                'adept_local_references': self.adept_local_references,
                'adept_remote_inventory': 'not_requested'}


def group_evidence(inventory: Inventory, snapshot: EvidenceSnapshot,
                   report: ReconciliationReport, identity_ids: tuple[str, ...]) -> GroupedEvidence:
    """Per-provider counts only when provenance is explicitly attested."""
    if (not isinstance(identity_ids, tuple) or not 1 <= len(identity_ids) <= 128
            or any(type(v) is not str or not v for v in identity_ids)):
        raise ValueError('invalid_reconciliation_identity_scope')
    selected = tuple(dict.fromkeys(identity_ids))
    indices = {identity: index for index, identity in enumerate(selected)}
    counts = defaultdict(Counter)
    rights = snapshot.tables.get('entitlements', [])
    confirmed = defaultdict(set)
    for edge in report.edges:
        if edge.table == 'entitlements' and edge.certainty == 'attested' and 0 <= edge.row_index < len(rights):
            confirmed[edge.file_index].add(rights[edge.row_index].get('provider_id'))
    safe_providers = {'kobo', 'numilog', 'paris', 'local'}
    for fi, observation in enumerate(inventory.files):
        if observation.identity_id not in indices:
            continue
        statuses = report.files.get(fi, {'unverified_file'})
        providers = confirmed[fi] if 'confirmed_link' in statuses else set()
        provider = next(iter(providers)) if len(providers) == 1 else 'unattributed'
        provider = provider if provider in safe_providers else 'unattributed'
        counter = counts[(indices[observation.identity_id], provider)]
        counter['observed_files'] += 1
        counter['sha_verified'] += int(observation.sha256 is not None)
        for status in statuses:
            counter[status] += 1
    unverified = set(report.unverified_rights)
    for ri, right in enumerate(rights):
        identity = right.get('identity_id')
        if identity not in indices or right.get('state') != 'active':
            continue
        provider = right.get('provider_id')
        provider = provider if provider in safe_providers else 'other'
        counter = counts[(indices[identity], provider)]
        counter['observed_active_rights'] += 1
        if ri in unverified:
            counter['rights_without_verified_file'] += 1
    references = len(snapshot.tables.get('adept_profile_bindings', [])) + len(snapshot.tables.get('paris_borrower_adept_bindings', []))
    return GroupedEvidence(selected, {k: dict(sorted(v.items())) for k, v in counts.items()}, references)
