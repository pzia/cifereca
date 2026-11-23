"""#4552: project admitted public Search catalogue age evidence to typed facts.

Pure Core bridge. The actual source observation is produced by the verified
Search public-catalogue path; no age inference, edition joining, HTTP, rights,
or plugin execution is performed here. Persisting/replaying observations is
a separate authority boundary.
"""
from __future__ import annotations

from hashlib import sha256
import json

from .notice_c_public_age import decode_public_age_observation
from .notice_consolidation_inputs import admit_descriptive_facts
from .notice_source_observation import NoticeSourceObservation, admit_source_notice
from .notice_search_cover_producer import _coordinate_digest


def public_age_descriptive_facts(observation: NoticeSourceObservation) -> tuple[dict, ...]:
    """Extract one source's typed native age observation, without ranking it.

    The source edition is exact; callers may later supply independently
    attested strong links to the standard admission gate. No guessed links.
    """
    if type(observation) is not NoticeSourceObservation:
        return ()
    key = observation.key
    # Reattest the raw source revision before extracting any facts.
    try:
        restored = admit_source_notice(
            key=key, parser_id=observation.parser_id,
            parser_version=observation.parser_version,
            observed_at=observation.observed_at,
            raw_fields=observation.fields_copy(), status=observation.status,
        )
    except (ValueError, TypeError, AttributeError, UnicodeError):
        return ()
    if (restored.source_revision != observation.source_revision
            or restored.raw_json != observation.raw_json):
        return ()
    if (key.visibility != "public" or key.identity_id is not None
            or key.credential_id is not None or key.account_id is not None
            or key.branch_id != "search.public.catalogue"
            or observation.parser_id != "core.search.public-cover"
            or observation.parser_version != "2"):
        return ()
    try:
        payload = observation.fields_copy()
        bibliography = payload["search_result"]["public_bibliography"]
        if type(bibliography) is not dict:
            return ()
        age = decode_public_age_observation(
            bibliography.get("age_observation"),
            provider_id=key.provider_id,
            record_id=bibliography["record_id"],
            docbase=bibliography["docbase"],
            medium=bibliography["medium"],
            source_revision=bibliography["revision_id"],
        )
        if age is None:
            return ()
        # The native age receipt must describe the SAME producer-authenticated
        # source key. A valid receipt from a different edition is not evidence.
        collection = bibliography.get("source_collection")
        if collection != "public_catalog":
            return ()
        if key.record_id != "search:" + _coordinate_digest(
            key.provider_id, collection, age.record_id,
        ):
            return ()
        if key.edition_id != "edition:" + _coordinate_digest(
            key.provider_id, collection, age.record_id, age.docbase,
            age.medium, age.source_revision,
        ):
            return ()
        # Hash bounded, vetted source coordinates; do not expose a URL or title
        # as an identity and never merge editions by bibliographic similarity.
        source_digest = sha256(json.dumps(
            (key.provider_id, key.branch_id, key.record_id),
            ensure_ascii=True, separators=(",", ":"),
        ).encode("ascii")).hexdigest()
        revision = observation.source_revision
        if type(revision) is not str or not revision:
            return ()
        result = []
        for index, item in enumerate(age.ranges):
            value = json.dumps({
                "age_min": item.age_min, "age_max": item.age_max,
                "age_kind": item.age_kind, "source_label": item.source_label,
                "source_field": age.source_field,
            }, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            result.append({
                "source_id": "source:" + source_digest,
                "canonical_type": "age",
                "edition_id": key.edition_id,
                "evidence_id": "age:" + str(index),
                "value": value,
                "source_revision": revision,
            })
        return tuple(result)
    except (KeyError, TypeError, ValueError, UnicodeError, AttributeError):
        return ()


def admit_public_age_descriptive_facts(
    observation: NoticeSourceObservation, *, target_edition_id: str,
    consolidator_id: str, consolidator_version: str,
    attested_strong_links: dict[str, str],
):
    """Apply the existing Core exact/strong-proof admission to real age facts."""
    return admit_descriptive_facts(
        target_edition_id=target_edition_id, canonical_type="age",
        consolidator_id=consolidator_id,
        consolidator_version=consolidator_version,
        source_facts=public_age_descriptive_facts(observation),
        attested_strong_links=attested_strong_links,
    )
