from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal
from urllib.parse import urlsplit


CoverTier = Literal['embedded', 'origin', 'catalog']


@dataclass(frozen=True, slots=True)
class CoverCandidate:
    url: str
    tier: CoverTier
    source: str
    evidence_id: str = ''
    evidence_strength: int = 0


@dataclass(frozen=True, slots=True)
class CoverResolution:
    status: Literal['selected', 'ambiguous', 'missing']
    url: str = ''
    tier: str = ''
    source: str = ''
    evidence_id: str = ''
    candidates: tuple[CoverCandidate, ...] = ()

    def metadata(self) -> dict[str, object]:
        return {
            'status': self.status,
            'url': self.url or None,
            'tier': self.tier or None,
            'source': self.source or None,
            'evidence_id': self.evidence_id or None,
            'candidate_count': len(self.candidates),
        }


_TIER_PRIORITY = {'embedded': 300, 'origin': 200, 'catalog': 100}


def _safe_cover_url(value: object) -> str:
    url = str(value or '').strip()
    if not url:
        return ''
    parts = urlsplit(url)
    if parts.scheme in {'http', 'https'} and parts.netloc:
        return url
    if parts.scheme == 'file' and parts.path:
        return url
    return ''


def resolve_cover(candidates: Iterable[CoverCandidate]) -> CoverResolution:
    """Resolve a cover by explicit provenance only."""
    dedup: dict[str, CoverCandidate] = {}
    for candidate in candidates:
        if candidate.tier not in _TIER_PRIORITY:
            continue
        url = _safe_cover_url(candidate.url)
        if not url:
            continue
        normalized = CoverCandidate(
            url=url,
            tier=candidate.tier,
            source=str(candidate.source or candidate.tier),
            evidence_id=str(candidate.evidence_id or ''),
            evidence_strength=max(0, int(candidate.evidence_strength or 0)),
        )
        current = dedup.get(url)
        if current is None or (
            _TIER_PRIORITY[normalized.tier], normalized.evidence_strength
        ) > (_TIER_PRIORITY[current.tier], current.evidence_strength):
            dedup[url] = normalized

    rows = tuple(dedup.values())
    if not rows:
        return CoverResolution('missing')

    best_tier_priority = max(_TIER_PRIORITY[row.tier] for row in rows)
    tier_rows = tuple(row for row in rows if _TIER_PRIORITY[row.tier] == best_tier_priority)
    best_strength = max(row.evidence_strength for row in tier_rows)
    finalists = tuple(row for row in tier_rows if row.evidence_strength == best_strength)

    if len(finalists) != 1:
        return CoverResolution(
            'ambiguous',
            tier=finalists[0].tier if finalists else '',
            candidates=finalists,
        )
    selected = finalists[0]
    return CoverResolution(
        'selected',
        url=selected.url,
        tier=selected.tier,
        source=selected.source,
        evidence_id=selected.evidence_id,
        candidates=rows,
    )


def candidate(
    url: object, *, tier: CoverTier, source: str, evidence_id: str = '', evidence_strength: int = 0
) -> CoverCandidate | None:
    safe = _safe_cover_url(url)
    if not safe:
        return None
    return CoverCandidate(safe, tier, source, evidence_id, evidence_strength)


__all__ = ['CoverCandidate', 'CoverResolution', 'candidate', 'resolve_cover']
