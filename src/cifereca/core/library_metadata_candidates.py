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
    # Aggregate progress from the bounded current attempt; no ISBN, row,
    # identity, title, fingerprint, SHA or per-record timings exposed.
    catalog_rows_examined: int = 0
    references_examined: int = 0
    files_examined: int = 0
    index_ms: int = 0
    match_ms: int = 0
    index_completed: bool = False
    match_phase_executed: bool = False

    def redacted(self):
        return {'schema': 1, 'read_only': True, 'acquisition_authorized': False,
                'status': 'incomplete_scan' if self.reasons else 'complete',
                'reasons': sorted(self.reasons),
                'metadata_candidates': len(self.candidates),
                'ambiguous_files': len(self.ambiguous_files),
                'catalog_rows_examined': self.catalog_rows_examined,
                'references_examined': self.references_examined,
                'files_examined': self.files_examined,
                'index_ms': self.index_ms, 'match_ms': self.match_ms,
                'index_completed': self.index_completed,
                'match_phase_executed': self.match_phase_executed,
                'certainty': 'metadata_only'}


def metadata_candidates(inventory: Inventory, snapshot: EvidenceSnapshot, *,
                        cancelled=lambda: False, max_candidates: int = 10_000,
                        seconds: float = 10,
                        index_seconds: float | None = None) -> MetadataCandidateReport:
    """Same-identity catalog metadata only. No relation is a repair suggestion.

    Limits have distinct *closed* codes (#2565): timeout, cancellation,
    catalogue rows, identifiers, output candidates or per-row key ceiling.
    No code implies ownership, missing acquisition or exhaustiveness.
    """
    if (type(max_candidates) is not int or not 1 <= max_candidates <= 10_000
            or type(seconds) not in (int, float) or not 0 < seconds <= 60
            or (index_seconds is not None and (
                type(index_seconds) not in (int, float)
                or not 0 < index_seconds < seconds))):
        raise ValueError('invalid_candidate_limits')
    result = MetadataCandidateReport(reasons=set(inventory.reasons) | set(snapshot.reasons))
    started_at = time.monotonic()
    deadline = started_at + seconds
    # Legacy one-shot API retains its full 10-second index budget. Scoped
    # Maintenance explicitly reserves a separate matching window (30+30s).
    index_deadline = (deadline if index_seconds is None
                      else started_at + index_seconds)
    index = defaultdict(set)
    rows = snapshot.tables.get('catalog_entries', [])
    if len(rows) > 100_000:
        result.reasons.add('metadata_candidate_row_budget')

    def stopped():
        if cancelled():
            result.reasons.add('metadata_candidate_cancelled')
            return True
        if time.monotonic() >= deadline:
            result.reasons.add('metadata_candidate_time_budget')
            return True
        return False

    started = time.perf_counter()
    index_timed_out = False
    try:
        for ci, row in enumerate(rows[:100_000]):
            if stopped():
                return result
            if index_seconds is not None and time.monotonic() >= index_deadline:
                result.reasons.add('metadata_candidate_time_budget')
                index_timed_out = True
                break
            result.catalog_rows_examined += 1
            keys = row.get('bibliographic_identifiers', ())
            if not isinstance(keys, (list, tuple)):
                result.reasons.add('metadata_unavailable')
                continue
            if len(keys) > 64:
                result.reasons.add('metadata_candidate_keys_budget')
            for key in keys[:64]:
                result.references_examined += 1
                if result.references_examined > 100_000:
                    result.reasons.add('metadata_candidate_reference_budget')
                    return result
                if isinstance(key, (list, tuple)) and len(key) == 2 and all(type(v) is str for v in key):
                    index[(row.get('identity_id'), tuple(key))].add(ci)
        if not index_timed_out and len(rows) <= 100_000:
            result.index_completed = True
    finally:
        result.index_ms = max(0, min(600_000, int((time.perf_counter() - started) * 1000)))
    if index_timed_out and index_seconds is None:
        return result
    # A budgeted partial index is NOT negative evidence. Nonetheless perform
    # a bounded metadata-only match over the indexed subset when time remains.
    # This is deliberately not a rights/ownership attestation.
    if stopped():
        return result
    result.match_phase_executed = True
    started = time.perf_counter()
    # The inverse ambiguity matters too: one catalogue notice (or ISBN) can
    # match several independent EPUBs. Neither file may be presumed to be the
    # unique copy/edition. Keep only integer indices, bounded by the existing
    # max_candidates budget; this is descriptive evidence, never ownership.
    first_file_by_notice = {}
    ambiguous = set()
    try:
        for fi, observation in enumerate(inventory.files):
            if stopped():
                break
            result.files_examined += 1
            if observation.validation != 'ok' or observation.sha256 is None:
                continue
            matches = set()
            for key in identifiers(observation.metadata):
                # A file can have several explicit identifiers. Enforce the
                # output cap on the UNION, not on each individual lookup:
                # otherwise disjoint ISBN/edition matches can overrun the
                # bounded report or falsely imply a complete comparison.
                matches.update(index.get((observation.identity_id, key), ()))
                if len(matches) > max_candidates - len(result.candidates):
                    result.reasons.add('metadata_candidate_count_budget')
                    return result
            for ci in sorted(matches):
                previous = first_file_by_notice.setdefault(ci, fi)
                if previous != fi:
                    ambiguous.update((previous, fi))
                result.candidates.append(MetadataCandidate(fi, ci))
            if len(matches) > 1:
                ambiguous.add(fi)
    finally:
        # Populate even if time/candidate limits interrupted the match loop:
        # report ambiguity only among the positively observed candidates.
        result.ambiguous_files = sorted(ambiguous)
        result.match_ms = max(0, min(600_000, int((time.perf_counter() - started) * 1000)))
    return result
