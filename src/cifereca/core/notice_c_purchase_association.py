"""Exact private purchase -> public descriptive C pointer, not a relation proof.

The metadata entry is keyed by an opaque, already committed A coordinate. It
contains no account/title/URL/raw provider response and no documentary copy.
Only the qualified lease owner may record the result in the SAME transaction
as C source publication and journal completion. Readers never dispatch GET.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from ..plugin_api.downloadable_resources import SourceReference
from . import notice_c_job_journal as journal
from .book_cover_projection import CoverDocumentScope
from .canonical_publication import _stable
from .notice_c_cover_index import (
    _db_key, _decode, _exact_public_search_evidence, canonical_cover_document_ref,
)
from .notice_c_purchase_intent import _allowed_txn, _binding_txn, _spec_txn
from .notice_c_purchase_publication import PurchasePublicPublication, _association
from .notice_cover_repository import _checked_source_state, _opaque_key

_PREFIX = "notice.c.purchase-association.v1:"
_CANONICAL_PREFIX = "notice-c:canonical-cover:v1:"
_SOURCE_PREFIX = "notice-c:v1:"


@dataclass(frozen=True, slots=True, repr=False)
class PurchaseNoticeAssociation:
    document: CoverDocumentScope
    observed_at: int

    @property
    def authorizes_acquisition(self):
        return False


def _coords(source):
    if type(source) is not SourceReference or source.kind != "purchase":
        raise ValueError("notice_purchase_invalid_reference")
    scope = source.scope
    return (scope.identity_id, scope.provider_id, scope.credential_id,
            scope.account_id, source.branch, source.edition_id,
            source.resource_id, "purchase")


def _digest(values):
    return sha256(json.dumps(values, ensure_ascii=True,
                             separators=(",", ":")).encode("ascii")).hexdigest()


def _current_a(db, source):
    coords = _coords(source)
    ref = _stable("source", *coords)
    row = db.execute(
        "SELECT identity_id,provider_id,credential_id,account_id,branch_id,"
        "edition_id,right_id,reference_kind,state,evidence_kind,evidence_ref,observed_at "
        "FROM source_references WHERE source_reference_id=?", (ref,),
    ).fetchone()
    if row is None or row[:10] != (*coords, "active", "provider_response"):
        return ref, None
    if not all(type(value) is str and value for value in row[10:]):
        return ref, None
    return ref, _digest(["notice-purchase-current-a.v1", ref, *row[10:]])


def record_purchase_association_txn(db, lease, snapshot, candidate, publication, *, observed_at, now):
    """Private pointer receipt; caller also completes this lease before commit.

    Strict current A and source observation CAS. This does not create,
    change or reattest any OriginLink, A, placement, or acquired edition.
    """
    if (not db.in_transaction or type(publication) is not PurchasePublicPublication
            or not journal._integer(observed_at, minimum=1)
            or not journal._integer(now, minimum=1)
            or observed_at > now or not _allowed_txn(db, snapshot.scope)):
        raise ValueError("notice_purchase_association_invalid")
    job = journal._held(db, lease, now)
    spec = _spec_txn(db, snapshot, candidate)
    if job.spec != spec:
        raise ValueError("notice_purchase_association_changed")
    ref, proof = _current_a(db, candidate.source)
    source = publication.source
    key = _opaque_key(source.document)
    if key is None or proof is None:
        raise ValueError("notice_purchase_association_changed")
    _, live, _ = _checked_source_state(db, source.document, key)
    if live != source or not _association(source.observation, candidate):
        raise ValueError("notice_purchase_association_changed")
    edition, _ = _exact_public_search_evidence(source)
    if publication.document != canonical_cover_document_ref(edition):
        raise ValueError("notice_purchase_association_changed")
    wire = {
        "schema": 1, "a": proof, "input": spec.input_digest,
        "job": lease.ref.key, "generation": lease.ref.generation,
        "canonical": publication.document.source_key,
        "source": source.document.source_key,
        "source_epoch": source.epoch,
        "source_revision": source.observation.source_revision,
        "observed_at": observed_at,
    }
    db.execute("INSERT INTO metadata(key,value) VALUES(?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (_PREFIX + ref[len("source:"):],
                json.dumps(wire, ensure_ascii=True, separators=(",", ":"))))


def _read_txn(db, source):
    ref, proof = _current_a(db, source)
    if proof is None:
        return None
    scope = source.scope
    # Read visibility is not network permission. The user may turn off
    # public enrichment requests while retaining already attested facts.
    if not _binding_txn(db, scope):
        return None
    row = db.execute("SELECT value FROM metadata WHERE key=?",
                     (_PREFIX + ref[len("source:"):],)).fetchone()
    if row is None or type(row[0]) is not str or len(row[0]) > 2048:
        return None
    value = json.loads(row[0], object_pairs_hook=journal._unique)
    if (type(value) is not dict or set(value) != {
            "schema", "a", "input", "job", "generation", "canonical", "source",
            "source_epoch", "source_revision", "observed_at"}
            or type(value["schema"]) is not int or value["schema"] != 1
            or value["a"] != proof or not journal._digest(value["input"])
            or not journal._digest(value["job"])
            or not journal._integer(value["generation"], minimum=1)
            or not journal._integer(value["source_epoch"], minimum=1)
            or not journal._integer(value["observed_at"], minimum=1)):
        return None
    job = journal._load(db, value["job"])
    if (job is None or job.state != "current"
            or job.ref.generation != value["generation"]
            or job.spec.implementation_digest != journal._PURCHASE_INTENT_DIGEST
            or job.spec.target != journal._PURCHASE_TARGET + ref[len("source:"):]
            or job.spec.input_digest != value["input"]):
        return None
    canonical = value["canonical"]
    member = value["source"]
    if (type(canonical) is not str or not canonical.startswith(_CANONICAL_PREFIX)
            or not journal._digest(canonical[len(_CANONICAL_PREFIX):])
            or type(member) is not str or not member.startswith(_SOURCE_PREFIX)
            or not journal._digest(member[len(_SOURCE_PREFIX):])):
        return None
    document = CoverDocumentScope(kind="public_catalogue", identity_id=None,
                                  provider_id="cifereca.c", credential_id="__public__",
                                  source_key=canonical)
    row = db.execute("SELECT value FROM metadata WHERE key=?", (_db_key(document),)).fetchone()
    if row is None:
        return None
    notice = _decode(row[0], document)
    matches = [
        item for item in notice.sources if item.source_ref == member
        and item.visibility == "public" and item.source_epoch == value["source_epoch"]
        and item.source_revision == value["source_revision"]
        and item.public_bibliography is not None
    ]
    if len(notice.sources) != 1 or len(matches) != 1:
        return None
    return PurchaseNoticeAssociation(document, value["observed_at"])


def read_purchase_notice_association(storage, source, *, identity_id):
    """Exact profile-scoped local read; no search, GET or inferred linkage.

    The caller must bind identity_id to its current authorized viewer.
    Changed A receipt, journal generation, canonical source epoch, or
    removed binding -> unknown until reattested. Job recycling likewise
    requires reattestation; no pointer is promoted from stale evidence.
    """
    if type(source) is not SourceReference or source.scope.identity_id != identity_id:
        return None
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        try:
            return _read_txn(db, source)
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
            return None
