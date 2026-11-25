"""#4552 durable exact public-age request in the existing Notice C journal.

Only the trusted Core producer may call this port. A locator is not an
execution grant; dispatch must independently qualify the active archive
and reattest this snapshot before claiming/publishing. No new queue, GET
or public API. An explicit group locator admits only live directed relations.
"""
from hashlib import sha256
import json

from . import notice_c_job_journal as journal
from .book_cover_projection import CoverDocumentScope
from .notice_c_cover_index import canonical_cover_document_ref, is_canonical_cover_document, _db_key, _decode
from .notice_c_public_age_sql_snapshot import capture_public_age_sources_txn
from .notice_consolidation_inputs import admit_descriptive_facts
from .notice_consolidation_lineage import descriptive_execution_key
from .notice_descriptive_selection_store import (
    read_descriptive_selection_txn, descriptive_selection_input_digest,
)


def capture_exact_c_age_txn(db, *, target_edition_id):
    """Derive exact sources from the canonical self-C, never caller DTOs."""
    if not db.in_transaction:
        raise ValueError("notice_work_transaction_required")
    canonical = canonical_cover_document_ref(target_edition_id)
    raw = db.execute("SELECT value FROM metadata WHERE key=?", (_db_key(canonical),)).fetchone()
    if raw is None:
        return (), {}
    members = _decode(raw[0], canonical).sources
    documents = []
    for member in members:
        proof = sha256(json.dumps(
            ["cifereca.notice-c.exact-public-search-source.v1",
             member.source_ref, target_edition_id],
            ensure_ascii=True, separators=(",", ":"),
        ).encode("ascii")).hexdigest()
        if (member.visibility != "public" or member.public_bibliography is None
                or member.public_bibliography.edition_id != target_edition_id
                or member.relation_proof_id != "core.exact.search.v1:" + proof):
            raise ValueError("notice_descriptive_public_c_scope_changed")
        documents.append(CoverDocumentScope(
            kind="public_catalogue", identity_id=None,
            source_key=member.source_ref, provider_id=member.provider_id,
            credential_id="__public__",
        ))
    return capture_public_age_sources_txn(
        db, documents=tuple(documents), target_edition_id=target_edition_id,
    )


def group_document_from_locator(locator):
    if len(locator) == 2:
        return None
    document = CoverDocumentScope(
        kind="public_catalogue", identity_id=None, source_key=locator[2],
        provider_id="cifereca.c", credential_id="__public__",
    )
    if not is_canonical_cover_document(document):
        raise ValueError("notice_work_invalid_descriptive_spec")
    return document


def capture_public_age_job_sources_txn(db, *, target_edition_id, group_document=None):
    if group_document is None:
        return capture_exact_c_age_txn(db, target_edition_id=target_edition_id)
    from .notice_c_public_age_inheritance import capture_origin_linked_age_sources_txn
    if not is_canonical_cover_document(group_document):
        raise ValueError("notice_work_invalid_descriptive_spec")
    return capture_origin_linked_age_sources_txn(
        db, group_document=group_document, target_edition_id=target_edition_id,
    )


def _snapshot(db, *, target_edition_id, scope, group_document=None):
    pin = read_descriptive_selection_txn(db, canonical_type="age", scope=scope)
    if pin is None or pin.selection is None or pin.schema_sha256 is None:
        return None
    facts, links = capture_public_age_job_sources_txn(
        db, target_edition_id=target_edition_id, group_document=group_document,
    )
    selection = pin.selection
    admitted = admit_descriptive_facts(
        target_edition_id=target_edition_id, canonical_type="age",
        consolidator_id=selection.plugin_id, consolidator_version=selection.version,
        source_facts=facts, attested_strong_links=links,
    )
    if not admitted.exact and not admitted.inherited:
        return None
    execution_key = descriptive_execution_key(
        selection=selection, admitted=admitted,
        installed_source_sha256=pin.installed_source_sha256,
    )
    spec = journal.descriptive_job_spec(
        target_edition_id=target_edition_id, canonical_type="age", scope=scope,
        input_digest=descriptive_selection_input_digest(execution_key, pin),
        implementation_digest=pin.installed_source_sha256, public_age=True,
        group_document=group_document,
    )
    return spec, pin, admitted


def request_public_age_consolidation(storage, *, target_edition_id, scope, now,
                                    expected_generation, implementation_digests=None,
                                    group_document=None):
    """Durable CAS request from a qualified C and explicit pin; never claim."""
    if not journal._integer(now) or not journal._integer(expected_generation):
        raise ValueError("notice_work_invalid_clock_or_generation")
    if implementation_digests is not None and (
            type(implementation_digests) is not tuple
            or not 1 <= len(implementation_digests) <= 128
            or any(not journal._digest(value) for value in implementation_digests)
            or len(set(implementation_digests)) != len(implementation_digests)):
        raise ValueError("notice_work_invalid_public_age_filter")
    canonical_cover_document_ref(target_edition_id)
    if group_document is not None and not is_canonical_cover_document(group_document):
        raise ValueError("notice_work_invalid_descriptive_spec")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        snapshot = _snapshot(db, target_edition_id=target_edition_id, scope=scope,
                             group_document=group_document)
        if snapshot is None or (implementation_digests is not None
                and snapshot[0].implementation_digest not in implementation_digests):
            return None
        return journal._request_job_txn(db, snapshot[0], now=now,
                                        expected_generation=expected_generation)


def reattest_public_age_job_txn(db, spec):
    """Rebuild the exact current input, rejecting legacy and superseded jobs."""
    if not db.in_transaction:
        raise ValueError("notice_work_transaction_required")
    journal._check_spec(spec)
    if spec.kind != "consolidate_descriptive" or spec.public_age_locator is None:
        raise ValueError("notice_descriptive_public_age_locator_required")
    target, scope = spec.public_age_locator[:2]
    current = _snapshot(db, target_edition_id=target, scope=scope,
                        group_document=group_document_from_locator(spec.public_age_locator))
    if current is None or current[0] != spec:
        raise ValueError("notice_descriptive_public_age_input_changed")
    return current[1], current[2]
