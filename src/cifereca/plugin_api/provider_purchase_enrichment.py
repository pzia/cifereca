"""Optional same-read purchase enrichment observation, NOT fetch authority.

The resource inventory (A) and candidate association for metadata (C) remain
distinct. Core owns admission, preferences, storage, durable work and transport
dispatch. No locator is a grant, a delivery offer or a purchased-format proof.
These private association envelopes must never be serialized into public C.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from .downloadable_resources import ResourceSnapshot, SourceReference
from .provider_rights_observation import _timestamp

PURCHASE_ENRICHMENT_OBSERVATION_CONTRACT = "cifereca.provider.purchase-enrichment-observation/1"
_MAX_CANDIDATES = 10_000
_OMISSION_REASONS = frozenset(("direct_locator_unattested", "comparison_title_unattested", "candidate_unqualified"))
MAX_PURCHASE_RETRY_SECONDS = 30 * 24 * 3600


class PurchaseEnrichmentDeferred(Exception):
    """Closed transient outcome from a qualified public metadata producer.

    A bounded delay, not a GET grant. No raw header, URL, account or remote
    exception is part of this contract. Core owns the retry budget.
    """

    def __init__(self, reason, retry_after_seconds):
        if (type(reason) is not str or reason not in (
                "rate_limited", "unavailable", "budget_busy")
                or type(retry_after_seconds) is not int
                or not 1 <= retry_after_seconds <= MAX_PURCHASE_RETRY_SECONDS):
            raise ValueError("invalid_purchase_enrichment_deferral")
        super().__init__("purchase_enrichment_deferred")
        self.reason = reason
        self.retry_after_seconds = retry_after_seconds


def _public_locator(value):
    # Syntax only. The supplier's parser and qualified transport must prove
    # that the exact link is a public product, not an authenticated endpoint.
    if (type(value) is not str or not 1 <= len(value) <= 2048
            or any(ord(c) < 33 or ord(c) > 126 or c == "\\" for c in value)):
        return False
    try:
        parts = urlsplit(value)
        return (parts.scheme == "https" and bool(parts.hostname)
                and parts.username is None and parts.password is None
                and parts.port in (None, 443) and "?" not in value
                and "#" not in value and parts.path not in ("", "/")
                and not any(p in (".", "..") for p in parts.path.split("/")))
    except ValueError:
        return False


@dataclass(frozen=True, slots=True, repr=False)
class PurchaseEnrichmentCandidate:
    """One private-to-public association claimed by the same inventory read.

    The source is the exact positive purchase resource, not a title or ISBN
    join. The title is a comparison value only; it must never be sent to
    public search as an automatic fallback. Only a qualified supplier adapter
    can use the direct locator under a separately admitted Core work lease.
    """

    source: SourceReference
    public_locator: str
    title: str
    observed_at: str

    def __post_init__(self):
        if (type(self.source) is not SourceReference
                or self.source.kind != "purchase"
                or not _public_locator(self.public_locator)
                or type(self.title) is not str or not 1 <= len(self.title) <= 512
                or not self.title.strip() or not self.title.isprintable()
                or not _timestamp(self.observed_at)):
            raise ValueError("invalid_purchase_enrichment_candidate")

    @property
    def authorizes_fetch(self) -> bool:
        return False


@dataclass(frozen=True, slots=True, repr=False)
class PurchaseEnrichmentOmission:
    """Positive A was observed, but no qualified public candidate was emitted.

    A closed reason, not a negative catalogue result, fetch grant or error.
    No locator, title, tracking link or provider diagnostic is carried.
    """
    source: SourceReference
    observed_at: str
    reason: str

    def __post_init__(self):
        if (type(self.source) is not SourceReference or self.source.kind != "purchase"
                or not _timestamp(self.observed_at) or type(self.reason) is not str
                or self.reason not in _OMISSION_REASONS):
            raise ValueError("invalid_purchase_enrichment_omission")

    @property
    def authorizes_fetch(self) -> bool:
        return False


@dataclass(frozen=True, slots=True, repr=False)
class PurchaseEnrichmentObservation:
    """Independent candidates attached to exactly one native inventory report.

    A partial read can carry positively observed purchases; absence of a
    candidate is not absence of a purchase or negative catalogue evidence.
    Exact matches are bounded and scope/branch/observation time are checked.
    No private right is published or mutated by this envelope.
    """

    inventory: ResourceSnapshot
    candidates: tuple[PurchaseEnrichmentCandidate, ...] = ()
    schema_version: str = PURCHASE_ENRICHMENT_OBSERVATION_CONTRACT
    omissions: tuple[PurchaseEnrichmentOmission, ...] = ()

    def __post_init__(self):
        if (type(self.inventory) is not ResourceSnapshot
                or self.schema_version != PURCHASE_ENRICHMENT_OBSERVATION_CONTRACT
                or type(self.candidates) is not tuple
                or type(self.omissions) is not tuple
                or len(self.candidates) + len(self.omissions) > _MAX_CANDIDATES
                or any(type(item) is not PurchaseEnrichmentCandidate for item in self.candidates)
                or any(type(item) is not PurchaseEnrichmentOmission for item in self.omissions)
                or ((self.candidates or self.omissions) and self.inventory.status not in ("success", "partial"))):
            raise ValueError("invalid_purchase_enrichment_observation")
        accepted = {}
        for resource in self.inventory.resources:
            if (resource.source.kind == "purchase"
                    and resource.right is not None
                    and resource.right.state == "active"):
                accepted[resource.source] = resource.observed_at
        seen = set()
        for candidate in self.candidates:
            if (candidate.source in seen
                    or accepted.get(candidate.source) != candidate.observed_at):
                raise ValueError("unattested_purchase_enrichment_candidate")
            seen.add(candidate.source)
        for omission in self.omissions:
            if (omission.source in seen
                    or accepted.get(omission.source) != omission.observed_at):
                raise ValueError("unattested_purchase_enrichment_omission")
            seen.add(omission.source)

    @property
    def authorizes_fetch(self) -> bool:
        return False


__all__ = [
    "PURCHASE_ENRICHMENT_OBSERVATION_CONTRACT",
    "PurchaseEnrichmentCandidate", "PurchaseEnrichmentObservation", "PurchaseEnrichmentOmission",
    "PurchaseEnrichmentDeferred", "MAX_PURCHASE_RETRY_SECONDS",
]
