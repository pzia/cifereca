"""Inert, private purchase→public enrichment intent in the one #4982 journal.

Only a trusted Core caller can opt in, after a positive native A commit.
The job contains a hashed source coordinate and association fingerprint,
never the private title/URL, provider response, account or policy grant.
Reattestation of the same candidate is necessary after restart; no
transport, parser, second queue or production sink is installed here.
"""
from __future__ import annotations

from hashlib import sha256
import json

from ..plugin_api.provider_purchase_enrichment import PurchaseEnrichmentCandidate
from ..plugin_api.provider_rights_observation import ProviderRightsSnapshot
from ..plugin_api.downloadable_resources import SourceReference
from .canonical_publication import _stable
from .source_auto_consent_bridge import _source_id
from .source_enablement import source_enabled
from . import notice_c_job_journal as journal

_POLICY = "notice.c.purchase-enrichment.enabled.v1"
_MAX_BATCH = 32


def _allowed_txn(db, scope):
    # Independent policy; neither Sync nor acquisition consent is sufficient.
    # Opt-in for this unconnected port. The product's default-on policy must
    # be decided by the later qualified runtime composition, not this writer.
    policy = db.execute("SELECT value FROM metadata WHERE key=?", (_POLICY,)).fetchone()
    if policy is None or not source_enabled(policy[0]):
        return False
    enabled = db.execute("SELECT value FROM metadata WHERE key=?",
                         ("sync.source.enabled." + _source_id(scope),)).fetchone()
    if not source_enabled(enabled[0] if enabled else None):
        return False
    return _binding_txn(db, scope)


def _binding_txn(db, scope):
    """Exact live account binding; not a network preference or grant."""
    bindings = db.execute(
        "SELECT c.connection_id FROM provider_connections c "
        "JOIN provider_accounts a ON a.connection_id=c.connection_id "
        "JOIN identities i ON i.identity_id=c.identity_id "
        "WHERE c.identity_id=? AND c.provider_id=? AND c.credential_id=? "
        "AND c.enabled=1 AND a.identity_id=c.identity_id "
        "AND a.provider_id=c.provider_id AND a.credential_id=c.credential_id "
        "AND a.account_id=? AND i.directory_name<>'' "
        "AND NOT EXISTS(SELECT 1 FROM identities other "
        "WHERE other.directory_name=i.directory_name AND other.identity_id<>i.identity_id) "
        "LIMIT 2",
        (scope.identity_id, scope.provider_id, scope.credential_id, scope.account_id),
    ).fetchall()
    return len(bindings) == 1


def _spec_txn(db, snapshot, candidate):
    """Verify exact current A, then fingerprint semantic association only."""
    if type(candidate) is not PurchaseEnrichmentCandidate:
        raise ValueError("notice_purchase_candidate_invalid")
    # Reconstruct frozen DTOs at the Core boundary; no mutable plugin aliases.
    source = candidate.source
    if type(source) is not SourceReference:
        raise ValueError("notice_purchase_candidate_invalid")
    source = SourceReference(source.scope, source.branch, source.edition_id,
                             source.resource_id, source.kind)
    candidate = PurchaseEnrichmentCandidate(
        source, candidate.public_locator, candidate.title, candidate.observed_at,
    )
    if source.scope != snapshot.scope or source.branch != snapshot.branch:
        raise ValueError("notice_purchase_scope_changed")
    row = snapshot.exact(source.edition_id, source.resource_id, "purchase")
    if (row is None or not row.provider_attested or row.state != "active"
            or row.observed_at != candidate.observed_at):
        raise ValueError("notice_purchase_unattested")
    scope = source.scope
    coords = (scope.identity_id, scope.provider_id, scope.credential_id,
              scope.account_id, source.branch, source.edition_id,
              source.resource_id, "purchase")
    ref = _stable("source", *coords)
    committed = db.execute(
        "SELECT identity_id,provider_id,credential_id,account_id,branch_id,"
        "edition_id,right_id,reference_kind,state,evidence_kind,evidence_ref,observed_at "
        "FROM source_references WHERE source_reference_id=?", (ref,),
    ).fetchone()
    if committed != (*coords, "active", "provider_response",
                     row.evidence_ref, row.observed_at):
        raise ValueError("notice_purchase_source_changed")
    # Freshness/receipt changes alone are not a new association. The future
    # execution port MUST recheck the current A and this candidate fingerprint.
    wire = json.dumps(["cifereca.purchase-enrichment-intent.v1", ref,
                       candidate.public_locator, candidate.title,
                       row.provider_revision_id],
                      ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return journal.JobSpec(
        "fetch_source_notice", journal._PURCHASE_TARGET + ref[len("source:"):],
        sha256(wire).hexdigest(), journal._PURCHASE_INTENT_DIGEST,
    )


def request_purchase_enrichment_intents(storage, snapshot, candidates, *, now,
                                        context_current):
    """Bounded atomic admission, after A. Pure in-memory context guard only.

    Refusal never changes committed A. A caller must split larger batches;
    it may not treat truncation as a complete handoff. No auto execution.
    """
    if (type(snapshot) is not ProviderRightsSnapshot
            or snapshot.status not in ("success", "partial")
            or snapshot.resume_cursor
            or type(candidates) is not tuple or not 1 <= len(candidates) <= _MAX_BATCH
            or not journal._integer(now, minimum=1)
            or not callable(context_current)):
        raise ValueError("notice_purchase_invalid_batch")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if context_current() is not True or not _allowed_txn(db, snapshot.scope):
            raise ValueError("notice_purchase_policy_or_context")
        specs = tuple(_spec_txn(db, snapshot, item) for item in candidates)
        if len({spec.target for spec in specs}) != len(specs):
            raise ValueError("notice_purchase_ambiguous_batch")
        refs = []
        for spec in specs:
            old = journal._load(db, journal._key(spec))
            refs.append(journal._request_job_txn(
                db, spec, now=now, expected_generation=old.ref.generation if old else 0,
            ))
        if context_current() is not True:
            raise ValueError("notice_purchase_policy_or_context")
        return tuple(refs)
