"""Standard age consolidator: exact evidence first; abstain on conflict."""


def consolidate(facts):
    rows = facts["exact"] or facts["inherited"]
    if not rows:
        return None
    # Values are canonical serialized typed facts, including source label
    # and field. Never combine incompatible ranges or infer missing bounds.
    value = rows[0]["value"]
    for row in rows:
        if row["value"] != value:
            return None
    return {
        "canonical_type": facts["canonical_type"],
        "target_edition_id": facts["target_edition_id"],
        "schema_id": facts["schema_id"],
        "value": value,
        "inherited": not bool(facts["exact"]),
        "source_evidence_keys": [[
            row["source_id"], row["edition_id"], row["evidence_id"]
        ] for row in rows],
    }
