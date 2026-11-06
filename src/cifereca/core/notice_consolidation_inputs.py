"""#4552: pure source evidence admission for per-type canonical consolidators.

Core MUST NOT infer a canonical age/genre/language, nor decide between
plugins. This bounded gate returns factual inputs and their relationship
provenance; a separately selected versioned consolidator owns all domain rules.

No I/O, account, permission, entitlement, profile, HTTP or storage access.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_FACTS = 512
MAX_RELATED = 128
MAX_VALUE_BYTES = 2048


@dataclass(frozen=True, slots=True)
class SourceFact:
    """One source's *descriptive* evidence, not a right or an offer."""

    source_id: str
    canonical_type: str
    edition_id: str
    evidence_id: str
    value: str
    source_revision: str
    relation: str  # "exact" or "strong_attested"
    inherited: bool
    relation_proof_id: str | None


@dataclass(frozen=True, slots=True)
class ConsolidationInputs:
    target_edition_id: str
    canonical_type: str
    consolidator_id: str
    consolidator_version: str
    exact: tuple[SourceFact, ...]
    inherited: tuple[SourceFact, ...]
    rejected_count: int


def _identifier(value: Any) -> bool:
    return (type(value) is str and 0 < len(value) <= 160
            and all(c.isascii() and (c.isalnum() or c in "._:-/") for c in value))


def admit_descriptive_facts(
    *, target_edition_id: str, canonical_type: str,
    consolidator_id: str, consolidator_version: str,
    source_facts: object, attested_strong_links: object,
) -> ConsolidationInputs:
    """Admit only exact-source and explicitly linked descriptive facts.

    Strong links are a Core-vetted directed map from foreign edition_id to
    opaque proof ID, NEVER title/author/ISBN similarity. This contract does
    not conclude that inherited facts are valid for the target; the chosen
    type-specific consolidator must do that, and preserve `inherited=True`.
    A duplicate evidence identity with conflicting payload rejects the
    entire input: order MUST NOT decide the winner.
    """
    if not all(_identifier(value) for value in (
        target_edition_id, canonical_type, consolidator_id,
        consolidator_version,
    )):
        raise ValueError("invalid_consolidation_scope")
    if (type(source_facts) not in (tuple, list)
            or len(source_facts) > MAX_FACTS
            or type(attested_strong_links) is not dict
            or len(attested_strong_links) > MAX_RELATED):
        raise ValueError("invalid_consolidation_batch")
    for other, proof in attested_strong_links.items():
        if (not _identifier(other) or not _identifier(proof)
                or other == target_edition_id):
            raise ValueError("invalid_strong_link")
    exact: list[SourceFact] = []
    inherited: list[SourceFact] = []
    signatures: dict[tuple[str, str, str], tuple[str, str]] = {}
    rejected = 0
    for row in source_facts:
        if type(row) is not dict:
            rejected += 1
            continue
        # A future plugin type is just a validated value; no hardcoded
        # whitelist of age/genre/etc. Keep any rights-like type out of scope.
        if row.get("canonical_type") != canonical_type:
            rejected += 1
            continue
        source_id = row.get("source_id")
        edition_id = row.get("edition_id")
        evidence_id = row.get("evidence_id")
        source_revision = row.get("source_revision")
        value = row.get("value")
        if (not all(_identifier(x) for x in (
                source_id, edition_id, evidence_id, source_revision))
                or type(value) is not str):
            rejected += 1
            continue
        try:
            value_size = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            rejected += 1
            continue
        if not 0 < value_size <= MAX_VALUE_BYTES:
            rejected += 1
            continue
        if edition_id == target_edition_id:
            relation, proof, inherited_flag = "exact", None, False
        elif edition_id in attested_strong_links:
            relation, proof, inherited_flag = (
                "strong_attested", attested_strong_links[edition_id], True,
            )
        else:
            rejected += 1
            continue
        unique_key = (source_id, edition_id, evidence_id)
        fingerprint = (source_revision, value)
        if unique_key in signatures:
            if signatures[unique_key] != fingerprint:
                raise ValueError("conflicting_source_evidence")
            # A repeated input is not a second independent proof.
            continue
        signatures[unique_key] = fingerprint
        fact = SourceFact(
            source_id=source_id, canonical_type=canonical_type,
            edition_id=edition_id, evidence_id=evidence_id,
            value=value, source_revision=source_revision,
            relation=relation, inherited=inherited_flag,
            relation_proof_id=proof,
        )
        (inherited if inherited_flag else exact).append(fact)
    key = lambda f: (f.source_id, f.edition_id, f.evidence_id)
    return ConsolidationInputs(
        target_edition_id=target_edition_id,
        canonical_type=canonical_type,
        consolidator_id=consolidator_id,
        consolidator_version=consolidator_version,
        exact=tuple(sorted(exact, key=key)),
        inherited=tuple(sorted(inherited, key=key)),
        rejected_count=rejected,
    )
