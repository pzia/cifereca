"""#4552: deterministic revision key for descriptive consolidation *inputs*.

Not a cache, not a right, not a permission and not a plugin executor.
A future Core-only cache may use this key after separately vetting plugins
and the active schema; any lineage change invalidates the old result.
"""
from __future__ import annotations

import hashlib
import json

from .notice_consolidation_inputs import ConsolidationInputs, SourceFact
from .notice_consolidator_selection import ExplicitSelection

MAX_CACHE_FACTS = 512
_KEY_DOMAIN = "cifereca.notice-c.descriptive-lineage.v1"


def descriptive_lineage_key(
    *, selection: ExplicitSelection, admitted: ConsolidationInputs,
) -> str:
    """SHA256 over a canonical, length-safe structured snapshot.

    Keys deliberately include both exact and merely *eligible* inherited
    evidence, relation proof ID and revisions. Invalidation may be
    conservative, but a changed proof cannot silently retain an old cache
    entry. The key never authorizes a loan or metadata mutation.
    """
    if type(selection) is not ExplicitSelection or type(admitted) is not ConsolidationInputs:
        raise ValueError("invalid_consolidation_context")
    if (selection.canonical_type != admitted.canonical_type
            or selection.plugin_id != admitted.consolidator_id
            or selection.version != admitted.consolidator_version):
        raise ValueError("consolidator_context_mismatch")
    if (len(admitted.exact) + len(admitted.inherited) > MAX_CACHE_FACTS
            or type(admitted.rejected_count) is not int
            or admitted.rejected_count < 0):
        raise ValueError("invalid_lineage_size")
    seen: set[tuple[str, str, str]] = set()
    fact_rows = []
    for inherited, rows in ((False, admitted.exact), (True, admitted.inherited)):
        if type(rows) is not tuple:
            raise ValueError("invalid_lineage_facts")
        for fact in rows:
            if (type(fact) is not SourceFact
                    or fact.inherited is not inherited
                    or fact.canonical_type != selection.canonical_type
                    or (not inherited and
                        (fact.edition_id != admitted.target_edition_id
                         or fact.relation != "exact"
                         or fact.relation_proof_id is not None))
                    or (inherited and
                        (fact.edition_id == admitted.target_edition_id
                         or fact.relation != "strong_attested"
                         or not fact.relation_proof_id))):
                raise ValueError("invalid_lineage_fact")
            key = (fact.source_id, fact.edition_id, fact.evidence_id)
            if key in seen:
                raise ValueError("duplicate_lineage_fact")
            seen.add(key)
            fact_rows.append((
                fact.source_id, fact.edition_id, fact.evidence_id,
                fact.source_revision, fact.value, fact.relation,
                fact.relation_proof_id, inherited,
            ))
    # Structured JSON eliminates delimiter ambiguity (an edition or value
    # can legitimately include the separator a naïve string join would use).
    payload = (
        _KEY_DOMAIN,
        selection.canonical_type, selection.scope, selection.plugin_id,
        selection.version, selection.schema_id,
        admitted.target_edition_id,
        tuple(sorted(fact_rows, key=lambda row: (row[0], row[1], row[2]))),
    )
    try:
        raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("invalid_lineage_encoding") from exc
    return "notice-c-desc-v1:" + hashlib.sha256(raw).hexdigest()
