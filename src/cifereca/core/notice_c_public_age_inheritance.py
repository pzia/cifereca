"""Q9: live, directed descriptive evidence from an existing OriginLink C.

No inferred relation, private fields, provider IO or write. A retained group
alone cannot authorize inheritance. The caller recaptures this snapshot
in the same transaction as any descriptive publication.
"""
from .notice_c_aggregate_inputs import _capture_txn
from .notice_c_cover_index import _resolve_origin_linked_public_group_txn
from .notice_c_origin_recompute import _group_members
from .notice_c_origin_link_producer import reattest_exact_origin_group_txn
from .notice_c_public_age_descriptive_facts import public_age_descriptive_facts


def capture_origin_linked_age_sources_txn(db, *, group_document, target_edition_id):
    """Return facts and directed proofs for an exact member of a live group.

    No group membership or origin proof is accepted from the calling plugin.
    A missing, changed or ambiguous relation fails closed, including when
    a retained group exists but its source or private origin is gone.
    """
    if type(target_edition_id) is not str or not 0 < len(target_edition_id) <= 160:
        raise ValueError("invalid_public_age_snapshot_scope")
    inputs = _capture_txn(db, group_document)
    members = _group_members(inputs)
    sources = reattest_exact_origin_group_txn(db, inputs.sources)
    _, refs, proof = _resolve_origin_linked_public_group_txn(
        db, sources=sources, expected_document=group_document,
    )
    if refs != {member.source_ref for member in members} or any(
        member.relation_proof_id != proof for member in members
    ):
        raise ValueError("notice_origin_relation_changed")
    editions = {source.observation.key.edition_id for source in sources}
    if len(editions) != len(sources) or target_edition_id not in editions:
        raise ValueError("notice_origin_relation_changed")
    facts = tuple(fact for source in sources
                  for fact in public_age_descriptive_facts(source.observation))
    if len(facts) > 512:
        raise ValueError("public_age_snapshot_too_large")
    return facts, {edition: proof for edition in sorted(editions)
                   if edition != target_edition_id}
