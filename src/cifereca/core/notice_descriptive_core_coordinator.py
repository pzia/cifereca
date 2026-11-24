"""#4552: synchronous Core coordination of isolated descriptive consolidation.

Trusted Core integration port, not a scheduler. The caller supplies a vetted
installed registry and an authoritative SQLite-only snapshot reader; never a
provider/plugin callback. No HTTP, GTK, rights or network inside the transaction.
"""
from __future__ import annotations

from hashlib import sha256
from time import monotonic

from . import notice_c_job_journal as journal
from .notice_c_cover_index import canonical_cover_document_ref
from .notice_consolidation_lineage import descriptive_execution_key
from .notice_consolidation_inputs import admit_descriptive_facts
from .notice_consolidator_selection import select_consolidator
from .notice_descriptive_selection_store import (
    read_descriptive_selection_txn, require_descriptive_selection,
    descriptive_selection_input_digest,
)
from .notice_consolidator_runner import InstalledTextConsolidator
from .notice_descriptive_runner_adapter import propose_registered_descriptive_materialization
from .notice_descriptive_materialization_store import publish_descriptive_materialization


def run_descriptive_core_job(
    storage, *, capture_sources_txn, declarations, text_schemas, installed,
    canonical_type, scope, selected_plugin_id, selected_version,
    target_edition_id, now, lease_seconds, expected_generation=0,
    expected_execution_key=None,
):
    """Capture -> request/claim -> isolated runner -> fenced SQL publication.

    capture_sources_txn(db) is a trusted, read-only Core function returning
    (source_facts, attested_strong_links). It MUST derive these exclusively
    from the same authoritative DB transaction and must not run plugins.
    The producer must prevalidate its installed registry and source authority.
    """
    selection = select_consolidator(
        declarations, canonical_type=canonical_type, scope=scope,
        selected_plugin_id=selected_plugin_id, selected_version=selected_version,
    )
    if selection is None:
        raise ValueError("consolidator_not_selected")
    if type(installed) is not InstalledTextConsolidator:
        raise ValueError("installed_consolidator_mismatch")
    identity = (
        installed.canonical_type, installed.scope, installed.plugin_id,
        installed.version, installed.schema_id,
    )
    expected_identity = (
        selection.canonical_type, selection.scope, selection.plugin_id,
        selection.version, selection.schema_id,
    )
    fields = (*identity, installed.source, installed.source_sha256)
    if any(type(value) is not str for value in fields):
        raise ValueError("installed_consolidator_mismatch")
    if identity != expected_identity:
        raise ValueError("installed_consolidator_mismatch")
    try:
        source_digest = sha256(installed.source.encode("utf-8")).hexdigest()
    except UnicodeError as exc:
        raise ValueError("installed_consolidator_mismatch") from exc
    if source_digest != installed.source_sha256:
        raise ValueError("installed_consolidator_mismatch")
    # Source snapshot and work identity are derived under the same read lock.
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        pin = read_descriptive_selection_txn(db, canonical_type=canonical_type, scope=scope)
        require_descriptive_selection(pin, selection, installed.source_sha256)
        facts, links = capture_sources_txn(db)
        if type(facts) not in (tuple, list) or type(links) is not dict:
            raise ValueError("invalid_descriptive_source_snapshot")
        facts = tuple(facts)
        links = dict(links)
        admitted = admit_descriptive_facts(
            target_edition_id=target_edition_id, canonical_type=canonical_type,
            consolidator_id=selection.plugin_id,
            consolidator_version=selection.version,
            source_facts=facts, attested_strong_links=links,
        )
        execution_key = descriptive_execution_key(
            selection=selection, admitted=admitted,
            installed_source_sha256=installed.source_sha256,
        )
    if not admitted.exact and not admitted.inherited:
        return None
    if pin is None:
        # Legacy trusted staging callers retain their existing journal ABI.
        spec = journal.public_job_spec(
            "recompute_aggregate", canonical_cover_document_ref(target_edition_id),
            input_digest=descriptive_selection_input_digest(execution_key, pin),
            implementation_digest=installed.source_sha256,
        )
    else:
        spec = journal.descriptive_job_spec(
            target_edition_id=target_edition_id, canonical_type=canonical_type,
            scope=scope, input_digest=descriptive_selection_input_digest(execution_key, pin),
            implementation_digest=installed.source_sha256,
        )
    # Recheck authority before allocating a durable job. A concurrent
    # revocation must not allow an obsolete producer to supersede newer work.
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if read_descriptive_selection_txn(
            db, canonical_type=canonical_type, scope=scope,
        ) != pin:
            raise ValueError("descriptive_selection_changed")
        ref = journal._request_job_txn(
            db, spec, now=now, expected_generation=expected_generation,
        )
    lease = journal.claim_job(
        storage, ref, now=now, lease_seconds=lease_seconds,
    )
    if lease is None:
        return None
    # Preserve the caller's clock domain while accounting for worker elapsed time.
    started = monotonic()

    def completion_now():
        return now + int(max(0, monotonic() - started))

    # This is deliberately outside every DB transaction.
    try:
        proposal = propose_registered_descriptive_materialization(
            declarations=declarations, text_schemas=text_schemas,
            installed=installed, canonical_type=canonical_type, scope=scope,
            selected_plugin_id=selected_plugin_id,
            selected_version=selected_version,
            target_edition_id=target_edition_id, source_facts=facts,
            attested_strong_links=links,
        )
        if proposal is None or proposal.expected_execution_key != execution_key:
            raise ValueError("descriptive_no_publishable_result")

        def recapture(db):
            latest_pin = read_descriptive_selection_txn(
                db, canonical_type=canonical_type, scope=scope,
            )
            if latest_pin != pin:
                raise ValueError("descriptive_selection_changed")
            require_descriptive_selection(latest_pin, selection, installed.source_sha256)
            latest_facts, latest_links = capture_sources_txn(db)
            latest = admit_descriptive_facts(
                target_edition_id=target_edition_id,
                canonical_type=canonical_type,
                consolidator_id=selection.plugin_id,
                consolidator_version=selection.version,
                source_facts=latest_facts, attested_strong_links=latest_links,
            )
            return selection, latest, installed.source_sha256

        return publish_descriptive_materialization(
            storage, proposal.materialization,
            expected_execution_key=expected_execution_key,
            capture_current_txn=recapture,
            journal_lease=lease, journal_now=completion_now(),
            journal_clock=completion_now,
        )
    except Exception:
        # A refused or failed worker never marks a job current. Preserve
        # the original exception; failure receipt is best-effort and fenced.
        try:
            journal.fail_job(storage, lease, now=completion_now(), transient=False)
        except (ValueError, OSError):
            pass
        raise
