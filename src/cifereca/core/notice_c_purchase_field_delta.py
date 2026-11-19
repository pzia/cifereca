"""First #1072 field-reconciliation seam, NOT a canonical write or policy.

Compare two individually admitted public records with the SAME product
source coordinate and direct-link/title proof. Keep both original evidence
vectors. No synthetic source revision, new source, replacement of cover,
relabelled timestamp, selected conflict winner, storage or network.
"""
from __future__ import annotations

from dataclasses import dataclass
import json

from .notice_c_public_search import _public_observation
from .notice_cover_projection import notice_cover_document_ref
from .notice_cover_repository import _encode_source_row, _opaque_key
from .notice_source_observation import NoticeSourceObservation


@dataclass(frozen=True, slots=True, repr=False)
class PublicFieldEvidence:
    value: str | tuple[str, ...]
    provider_id: str
    source_ref: str
    source_revision: str
    observed_at: int
    structured_evidence: str | None = None


@dataclass(frozen=True, slots=True, repr=False)
class PublicFieldComparison:
    field: str
    state: str  # retained_only | addition | agreement | conflict
    retained: PublicFieldEvidence | None
    detail: PublicFieldEvidence | None


def _qualified_fields(observation):
    # Use the SAME encoder and strict read decoder as the real source
    # repository. Epoch 1 is only a temporary validation coordinate, never
    # persisted or offered as evidence. Raw digest and exact source are
    # rechecked by the read decoder's cover projection.
    document = notice_cover_document_ref(observation.key)
    raw = _encode_source_row(observation, ("search_result", "thumbnail_url"), True, 1)
    hit = _public_observation(raw, key_string=_opaque_key(document), now=observation.observed_at)
    if hit is None or hit.document != document:
        raise ValueError("notice_purchase_field_comparison_unqualified")
    facts = {}

    def add(field, value, structured=None):
        if type(value) is str and not value.strip():
            return
        if type(value) is tuple and not any(part.strip() for part in value):
            return
        if value is None:
            return
        facts[field] = PublicFieldEvidence(
            value, hit.provider_id, document.source_key, observation.source_revision,
            observation.observed_at, None if structured is None else json.dumps(
                structured, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )

    for field in ("title", "author", "publisher", "description", "published_at"):
        add(field, getattr(hit, field))
    if hit.series_relation is not None:
        from dataclasses import asdict
        add("series", hit.series_relation.label, asdict(hit.series_relation))
    if hit.language_observation is not None:
        add("languages", hit.language_observation.languages, hit.language_observation.wire())
    if hit.classification is not None:
        for field, value in hit.classification.entries:
            add(field, value)
    return facts


def compare_exact_purchase_fields(retained, detail, candidate):
    """Inert explicit comparison for the existing purchase writer.

    An agreement may carry two original timestamps; neither is assigned to
    a different field. A conflict preserves both, and a missing detail
    field never deletes retained evidence. No relation inferred from title.
    """
    from .notice_c_purchase_publication import _association
    if (type(retained) is not NoticeSourceObservation
            or type(detail) is not NoticeSourceObservation
            or retained.key != detail.key or retained.key.visibility != "public"):
        raise ValueError("notice_purchase_field_comparison_scope")
    prior, fresh = _qualified_fields(retained), _qualified_fields(detail)
    if not _association(retained, candidate) or not _association(detail, candidate):
        raise ValueError("notice_purchase_field_comparison_scope")
    result = []
    for field in sorted(prior.keys() | fresh.keys()):
        old, new = prior.get(field), fresh.get(field)
        state = ("addition" if old is None else "retained_only" if new is None
                 else "agreement" if old.value == new.value else "conflict")
        result.append(PublicFieldComparison(field, state, old, new))
    return tuple(result)
