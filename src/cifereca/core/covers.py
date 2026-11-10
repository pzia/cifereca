from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from typing import Iterable, Literal
from urllib.parse import parse_qsl, unquote, urlsplit


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
    # URLs supplied by a catalogue or plugin must not inject credentials into
    # the Core/UI read model. A malformed IPv6 authority must be an omitted
    # candidate, not an exception that blocks other independent covers.
    if type(value) is not str or len(value) > 4096:
        return ''
    url = value.strip()
    if not url or any(ord(char) < 32 or ord(char) == 127 or char == '\\'
                      for char in url):
        return ''
    try:
        parts = urlsplit(url)
        if parts.scheme in {'http', 'https'}:
            if (not parts.hostname or parts.username is not None
                    or parts.password is not None):
                return ''
            # A catalogue image is an untrusted network destination. The
            # shared UI loader must not be instructed to access local services
            # or private IP literals by a provider cover candidate.
            hostname = parts.hostname.rstrip(".").casefold()
            if (not hostname or hostname == "localhost"
                    or hostname.endswith(".localhost")
                    or hostname.endswith(".local")):
                return ''
            try:
                address = ip_address(hostname)
            except ValueError:
                pass  # Ordinary public DNS name; no DNS resolution here.
            else:
                if not address.is_global:
                    return ''
            # Accessing port validates malformed network authorities.
            _ = parts.port
            # A cover may legitimately carry public rendering parameters,
            # e.g. Feedbooks ?size=medium&t=..., but never bearer/signature
            # parameters which would publish a credential in the UI model.
            if parts.fragment:
                return ''
            secret_keys = {
                'token', 'accesstoken', 'refreshtoken', 'auth', 'authorization',
                'signature', 'sig', 'credential', 'secret', 'password',
                'session', 'sessionid', 'sessionkey', 'ticket', 'apikey',
                'accesskeyid', 'awsaccesskeyid', 'googleaccessid', 'jwt',
                'code', 'key',
                # CloudFront policy + signer identify a private signed URL
                # even when a separately signed Signature was omitted here.
                'keypairid', 'cloudfrontpolicy', 'policy',
            }
            # Legacy servers treat semicolon as another query separator.
            # A URL such as ?size=medium;token=SECRET must not bypass this
            # boundary by hiding an account token in the value of "size".
            if ';' in parts.query:
                return ''
            for key, _value in parse_qsl(parts.query, keep_blank_values=True):
                # URL parsers decode once. Bound one additional decoding pass
                # so %2574oken cannot be smuggled through as a public key.
                normalized = ''.join(
                    char for char in unquote(key).casefold() if char.isalnum()
                )
                if (normalized in secret_keys
                        or normalized.startswith(('xamz', 'xgoog'))):
                    return ''
            return url
        if parts.scheme == 'file' and parts.netloc in {'', 'localhost'}:
            if parts.path.startswith('/') and not parts.query and not parts.fragment:
                return url
    except ValueError:
        return ''
    return ''


def _validated_cover_candidate(raw: object) -> CoverCandidate | None:
    """Admit bounded scalar provenance; never coerce plugin-owned objects.

    A malformed candidate must not abort the resolution of independent,
    already-attested covers or leak an arbitrary __str__/repr payload.
    """
    if type(raw) is not CoverCandidate:
        return None
    if type(raw.tier) is not str or raw.tier not in _TIER_PRIORITY:
        return None
    url = _safe_cover_url(raw.url)
    if not url or (url.startswith('file:') and raw.tier != 'embedded'):
        return None
    if raw.source is None:
        source = raw.tier
    elif type(raw.source) is str:
        source = raw.source or raw.tier
    else:
        return None
    if raw.evidence_id is None:
        evidence_id = ''
    elif type(raw.evidence_id) is str:
        evidence_id = raw.evidence_id
    else:
        return None
    if (len(source) > 128 or not source.isprintable()
            or len(evidence_id) > 512 or not evidence_id.isprintable()):
        return None
    strength = raw.evidence_strength
    if strength is None:
        strength = 0
    if type(strength) is not int or not 0 <= strength <= 1000:
        return None
    return CoverCandidate(url, raw.tier, source, evidence_id, strength)


def resolve_cover(candidates: Iterable[CoverCandidate]) -> CoverResolution:
    """Resolve covers independently, rejecting malformed or private inputs."""
    dedup: dict[str, CoverCandidate] = {}
    for raw in candidates:
        normalized = _validated_cover_candidate(raw)
        if normalized is None:
            continue
        url = normalized.url
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
    return _validated_cover_candidate(CoverCandidate(
        url, tier, source, evidence_id, evidence_strength,
    ))


__all__ = ['CoverCandidate', 'CoverResolution', 'candidate', 'resolve_cover']
