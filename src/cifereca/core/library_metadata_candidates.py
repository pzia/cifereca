"""Bounded bibliographic candidates; identifiers NEVER prove ownership (#2565)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import re
import time

from .library_inventory import Inventory
from .library_reconciliation import EvidenceSnapshot


def identifiers(metadata: object) -> tuple[tuple[str, str], ...]:
    """Extract bounded explicit scheme/value keys, never titles or filenames.

    ISBN check digits reject lookalikes; 10/13 are kept distinct. Other schemes
    require an explicit scheme and exact opaque value, not guessed edition IDs.
    """
    if not isinstance(metadata, dict):
        return ()
    rows = metadata.get('identifiers')
    if not isinstance(rows, list):
        local = metadata.get('epub_metadata')
        rows = local.get('identifiers') if isinstance(local, dict) else None
    if not isinstance(rows, list):
        return ()
    keys = set()
    for row in rows[:64]:
        if not isinstance(row, dict):
            continue
        value, scheme = row.get('value'), row.get('scheme')
        if type(value) is not str or not 0 < len(value) <= 256 or any(c in value for c in '\0\r\n'):
            continue
        value = value.strip()
        if value.lower().startswith('urn:isbn:'):
            value, scheme = value[9:], 'isbn'
        if type(scheme) is not str or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,40}', scheme):
            continue
        scheme = scheme.casefold()
        if scheme in {'isbn', 'isbn10', 'isbn13', 'isbn-10', 'isbn-13'}:
            value = value.replace('-', '').replace(' ', '').upper()
            if re.fullmatch(r'\d{13}', value) and sum(int(c)*(1 if i%2==0 else 3) for i,c in enumerate(value))%10 == 0:
                keys.add(('isbn13', value))
            elif re.fullmatch(r'\d{9}[\dX]', value) and sum((10-i)*(10 if c=='X' else int(c)) for i,c in enumerate(value))%11 == 0:
                keys.add(('isbn10', value))
        elif value:
            keys.add((scheme, value))
    return tuple(sorted(keys))


@dataclass(frozen=True, repr=False)
class MetadataCandidate:
    file_index: int
    catalog_row_index: int
    reason: str = 'exact_explicit_bibliographic_identifier'
    certainty: str = 'metadata_only'


@dataclass(repr=False)
class MetadataCandidateReport:
    candidates: list[MetadataCandidate] = field(default_factory=list)
    ambiguous_files: list[int] = field(default_factory=list)
    reasons: set[str] = field(default_factory=set)

    def redacted(self):
        return {'schema': 1, 'read_only': True, 'acquisition_authorized': False,
                'status': 'incomplete_scan' if self.reasons else 'complete',
                'reasons': sorted(self.reasons),
                'metadata_candidates': len(self.candidates),
                'ambiguous_files': len(self.ambiguous_files),
                'certainty': 'metadata_only'}


def metadata_candidates(inventory: Inventory, snapshot: EvidenceSnapshot, *,
                        cancelled=lambda: False, max_candidates: int = 10_000,
                        seconds: float = 10) -> MetadataCandidateReport:
    """Same-identity catalog metadata only. No relation is a repair suggestion.

    Scheme/value equality can locate an edition to inspect; it never attributes
    a provider, account, entitlement, acquisition, or permission to a file.
    """
    if (type(max_candidates) is not int or not 1 <= max_candidates <= 10_000
            or type(seconds) not in (int, float) or not 0 < seconds <= 30):
        raise ValueError('invalid_candidate_limits')
    result = MetadataCandidateReport(reasons=set(inventory.reasons) | set(snapshot.reasons))
    deadline = time.monotonic() + seconds
    index = defaultdict(set)
    rows = snapshot.tables.get('catalog_entries', [])
    if len(rows) > 100_000:
        result.reasons.add('metadata_candidate_budget_or_cancelled')
    references = 0
    for ci, row in enumerate(rows[:100_000]):
        if cancelled() or time.monotonic() >= deadline:
            result.reasons.add('metadata_candidate_budget_or_cancelled')
            return result
        keys = row.get('bibliographic_identifiers', ())
        if not isinstance(keys, (list, tuple)):
            result.reasons.add('metadata_unavailable')
            continue
        if len(keys) > 64:
            result.reasons.add('metadata_candidate_budget_or_cancelled')
        for key in keys[:64]:
            references += 1
            if references > 100_000:
                result.reasons.add('metadata_candidate_budget_or_cancelled')
                return result
            if isinstance(key, (list, tuple)) and len(key) == 2 and all(type(v) is str for v in key):
                index[(row.get('identity_id'), tuple(key))].add(ci)
    for fi, observation in enumerate(inventory.files):
        if cancelled() or time.monotonic() >= deadline:
            result.reasons.add('metadata_candidate_budget_or_cancelled')
            break
        if observation.validation != 'ok' or observation.sha256 is None:
            continue
        matches = set()
        for key in identifiers(observation.metadata):
            matches.update(index.get((observation.identity_id, key), ()))
            if len(matches) > max_candidates-len(result.candidates):
                result.reasons.add('metadata_candidate_budget_or_cancelled')
                return result
        for ci in sorted(matches):
            result.candidates.append(MetadataCandidate(fi, ci))
        if len(matches) > 1:
            result.ambiguous_files.append(fi)
    return result
