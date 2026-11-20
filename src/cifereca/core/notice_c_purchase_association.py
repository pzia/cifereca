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
# Product detail freshness, independent from the transient HTTP cache (60s).
# Conservative initial default, configurable only by a future policy port.
_DETAIL_TTL_SECONDS = 7 * 24 * 3600


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


def _canonical_document(canonical):
    return CoverDocumentScope(kind="public_catalogue", identity_id=None,
                              provider_id="cifereca.c", credential_id="__public__",
                              source_key=canonical)


def _receipt(db, ref):
    row = db.execute("SELECT value FROM metadata WHERE key=?",
                     (_PREFIX + ref[len("source:"):],)).fetchone()
    if row is None:
        return None
    if type(row[0]) is not str or len(row[0]) > 2048:
        raise ValueError("notice_purchase_association_corrupt")
    value = json.loads(row[0], object_pairs_hook=journal._unique)
    required = {"schema", "a", "input", "job", "generation", "canonical", "source",
                "source_epoch", "source_revision", "observed_at"}
    if (type(value) is not dict or set(value) not in (required, required | {"refresh_generation"})
            or type(value["schema"]) is not int or value["schema"] != 1
            or ("refresh_generation" in value and (
                not journal._integer(value["refresh_generation"], minimum=1)
                or not journal._integer(value["generation"], minimum=1)
                or value["refresh_generation"] <= value["generation"]))
            or not all(journal._digest(value[name]) for name in ("a", "input", "job"))
            or not all(journal._integer(value[name], minimum=1)
                       for name in ("generation", "source_epoch", "observed_at"))
            or not all(type(value[name]) is str and value[name].startswith(prefix)
                       and journal._digest(value[name][len(prefix):])
                       for name, prefix in (("canonical", _CANONICAL_PREFIX),
                                            ("source", _SOURCE_PREFIX),
                                            ("source_revision", "notice-raw.v1:")))):
        raise ValueError("notice_purchase_association_corrupt")
    return value


def _matches_job(value, job, ref):
    return (job is not None and job.state == "current"
            and job.ref.key == value["job"] and job.ref.generation == value["generation"]
            and job.spec.implementation_digest == journal._PURCHASE_INTENT_DIGEST
            and job.spec.target == journal._PURCHASE_TARGET + ref[len("source:"):]
            and job.spec.input_digest == value["input"])


def _matches_refresh(value, job, ref):
    # Only the exact successor issued by the TTL renewal txn. A changed
    # candidate (even A→B→A), generic job request or recycled key cannot
    # inherit this marker.
    return (job is not None and job.state in ("pending", "error")
            and job.ref.generation == value.get("refresh_generation")
            and job.ref.key == value["job"]
            and job.spec.implementation_digest == journal._PURCHASE_INTENT_DIGEST
            and job.spec.target == journal._PURCHASE_TARGET + ref[len("source:"):]
            and job.spec.input_digest == value["input"])


def _store_receipt(db, ref, value):
    cursor = db.execute("UPDATE metadata SET value=? WHERE key=?",
                        (json.dumps(value, ensure_ascii=True, separators=(",", ":")),
                         _PREFIX + ref[len("source:"):]))
    if cursor.rowcount != 1:
        raise ValueError("notice_purchase_association_changed")


def _exact_receipt_source_txn(db, value, candidate):
    """Revalidate the retained public proof without loading a new product."""
    from .notice_cover_repository import _PREFIX as source_prefix
    from .notice_source_observation import NoticeSourceKey
    from .notice_cover_projection import notice_cover_document_ref
    raw = db.execute("SELECT value FROM metadata WHERE key=?",
                     (source_prefix + value["source"][len(_SOURCE_PREFIX):],)).fetchone()
    if raw is None:
        return False
    parsed = json.loads(raw[0], object_pairs_hook=journal._unique)
    document = notice_cover_document_ref(NoticeSourceKey(**parsed["key"]))
    key = _opaque_key(document)
    if document.source_key != value["source"] or key is None:
        raise ValueError("notice_purchase_association_changed")
    _, live, _ = _checked_source_state(db, document, key)
    if (live is None or live.epoch != value["source_epoch"]
            or live.observation.source_revision != value["source_revision"]
            or not _association(live.observation, candidate)):
        return False
    edition, _ = _exact_public_search_evidence(live)
    if canonical_cover_document_ref(edition).source_key != value["canonical"]:
        raise ValueError("notice_purchase_association_changed")
    return True


def reattest_current_purchase_association(storage, job_ref, snapshot, candidate, *, now,
                                         report_change=False):
    """Rebind identical same-read association to fresh A, without a GET.

    Revalidate the exact prior public source under the same transaction.
    A legacy current job without a receipt or a changed source requires a
    new generation; no old lease is reopened. On TTL expiry only, an exact
    compatible successor may coexist with the prior descriptive receipt:
    no metadata is withdrawn merely because refresh is pending or failed.
    A subsequent positive A can rebind that exact successor as well, without
    changing its due time, attempts, generation or public observation time.
    A corrupt receipt is a refusal, not authority for another GET.
    The caller pins the same live context as the original request.
    """
    def result(ref, changed=False):
        return (ref, changed) if report_change else ref

    if not journal._integer(now, minimum=1):
        raise ValueError("notice_purchase_association_invalid")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not _allowed_txn(db, snapshot.scope):
            raise ValueError("notice_purchase_association_changed")
        job = journal._get(db, job_ref)
        if job.state not in ("current", "pending", "error") or _spec_txn(db, snapshot, candidate) != job.spec:
            raise ValueError("notice_purchase_association_changed")
        ref, proof = _current_a(db, candidate.source)
        if proof is None:
            raise ValueError("notice_purchase_association_changed")
        value = _receipt(db, ref)
        if value is not None and (_matches_job(value, job, ref) or _matches_refresh(value, job, ref)):
            if value["observed_at"] > now:
                raise ValueError("notice_purchase_association_changed")
            if _exact_receipt_source_txn(db, value, candidate):
                if _matches_refresh(value, job, ref):
                    changed = value["a"] != proof
                    if changed:
                        value["a"] = proof
                        _store_receipt(db, ref, value)
                    return result(job.ref, changed)
                if now - value["observed_at"] >= _DETAIL_TTL_SECONDS:
                    successor = journal._renew_purchase_current_txn(db, job, now=now)
                    value["refresh_generation"] = successor.generation
                    value["a"] = proof
                    _store_receipt(db, ref, value)
                    return result(successor, True)
                changed = value["a"] != proof
                if changed:
                    value["a"] = proof
                    _store_receipt(db, ref, value)
                return result(job.ref, changed)
        if job.state != "current":
            return result(job.ref)
        return result(journal._renew_purchase_current_txn(db, job, now=now), True)


def _read_txn(db, source):
    ref, proof = _current_a(db, source)
    if proof is None:
        return None
    scope = source.scope
    # Read visibility is not network permission. The user may turn off
    # public enrichment requests while retaining already attested facts.
    if not _binding_txn(db, scope):
        return None
    value = _receipt(db, ref)
    if value is None or value["a"] != proof:
        return None
    job = journal._load(db, value["job"])
    if not (_matches_job(value, job, ref) or _matches_refresh(value, job, ref)):
        return None
    document = _canonical_document(value["canonical"])
    member = value["source"]
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
