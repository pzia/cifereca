"""Bounded public-only detail receipt, never a private association or grant."""
from dataclasses import dataclass
from hashlib import sha256
import json

from . import notice_c_job_journal as journal
from .notice_c_purchase_association import _DETAIL_TTL_SECONDS
from .notice_c_purchase_publication import _association
from .notice_c_public_search import _public_observation
from .notice_cover_repository import _PREFIX as SOURCE_PREFIX, _checked_source_state
from .notice_cover_projection import notice_cover_document_ref
from .notice_source_observation import NoticeSourceKey, NoticeSourceObservation
from .notice_c_cover_index import (
    _db_key, _decode, _exact_public_search_evidence, canonical_cover_document_ref,
)
from .notice_c_purchase_field_delta import compare_exact_purchase_fields


@dataclass(frozen=True, slots=True, repr=False)
class PublicDetailReuse:
    # The retained source keeps its ORIGINAL time. The separate receipt
    # attests when a qualified detail was actually observed.
    observation: NoticeSourceObservation
    observed_at: int
    detail_revision: str
    canonical_epoch: int | None = None


def _canonical_member(db, source):
    edition, proof = _exact_public_search_evidence(source)
    document = canonical_cover_document_ref(edition)
    row = db.execute("SELECT value FROM metadata WHERE key=?", (_db_key(document),)).fetchone()
    if row is None:
        return None
    canonical = _decode(row[0], document)
    if len(canonical.sources) != 1:
        return None
    member = canonical.sources[0]
    if (member.source_ref != source.document.source_key or member.source_epoch != source.epoch
            or member.source_revision != source.observation.source_revision
            or member.relation_proof_id != proof or member.visibility != "public"
            or not member.public_web_references or member.public_bibliography is None):
        return None
    return canonical, member

_PREFIX = "notice.c.purchase-public-detail.v1:"
_MAX = 2048


def _key(provider, locator):
    return _PREFIX + sha256(json.dumps([provider, locator], ensure_ascii=True,
                                       separators=(",", ":")).encode("ascii")).hexdigest()


def read_public_detail_txn(db, provider, candidate, *, now):
    """Exact locator AND comparator, live qualified source, original time."""
    row = db.execute("SELECT value FROM metadata WHERE key=?",
                     (_key(provider, candidate.public_locator),)).fetchone()
    if row is None:
        return None
    try:
        if type(row[0]) is not str or len(row[0]) > 1024:
            return None
        value = json.loads(row[0], object_pairs_hook=journal._unique)
        required = {"source", "epoch", "revision", "observed_at"}
        extended = required | {"detail_revision", "canonical_epoch", "fields"}
        if (type(value) is not dict or set(value) not in (required, extended)
                or not journal._digest(value["source"])
                or not journal._integer(value["epoch"], minimum=1)
                or not journal._integer(value["observed_at"], minimum=1)
                or not 0 <= now - value["observed_at"] < _DETAIL_TTL_SECONDS):
            return None
        key = SOURCE_PREFIX + value["source"]
        raw = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        if raw is None or _public_observation(raw[0], key_string=key, now=now) is None:
            return None
        data = json.loads(raw[0], object_pairs_hook=journal._unique)
        document = notice_cover_document_ref(NoticeSourceKey(**data["key"]))
        _, source, _ = _checked_source_state(db, document, key)
        if (source is None or source.observation.key.provider_id != provider
                or source.epoch != value["epoch"]
                or source.observation.source_revision != value["revision"]
                or not _association(source.observation, candidate)):
            return None
        if set(value) == required:
            if source.observation.observed_at != value["observed_at"]:
                return None
            return PublicDetailReuse(source.observation, value["observed_at"], value["revision"])
        revision = value["detail_revision"]
        if (type(revision) is not str or not revision.startswith("notice-raw.v1:")
                or not journal._digest(revision.removeprefix("notice-raw.v1:"))
                or not journal._integer(value["canonical_epoch"], minimum=1)
                or source.observation.observed_at > value["observed_at"]
                or type(value["fields"]) is not list or len(value["fields"]) > 16
                or any(type(field) is not str for field in value["fields"])
                or value["fields"] != sorted(set(value["fields"]))):
            return None
        current = _canonical_member(db, source)
        if current is None or current[0].epoch != value["canonical_epoch"]:
            return None
        facts = {fact.field: fact for fact in current[1].public_detail_facts}
        if any(field not in facts or facts[field].source_revision != revision
               or facts[field].observed_at != value["observed_at"] for field in value["fields"]):
            return None
        return PublicDetailReuse(source.observation, value["observed_at"], revision, current[0].epoch)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return None


def _reclaim_expired_public_details_txn(db, *, now):
    """Free only expired public receipt slots, never a C/source or association.

    Called only under capacity pressure inside the existing write transaction.
    A malformed or future-dated receipt is not evidence of expiration.
    """
    reclaimed = 0
    rows = db.execute(
        "SELECT key,value FROM metadata WHERE key>=? AND key<? ORDER BY key LIMIT ?",
        (_PREFIX, _PREFIX + "\uffff", _MAX),
    ).fetchall()
    for key, raw in rows:
        if not journal._digest(key.removeprefix(_PREFIX)):
            continue
        try:
            if type(raw) is not str or len(raw) > 1024:
                continue
            value = json.loads(raw, object_pairs_hook=journal._unique)
            observed_at = value["observed_at"]
            if (type(value) is not dict
                    or not journal._integer(observed_at, minimum=1)
                    or now - observed_at < _DETAIL_TTL_SECONDS):
                continue
        except (ValueError, TypeError, KeyError, RecursionError):
            continue
        db.execute("DELETE FROM metadata WHERE key=? AND value=?", (key, raw))
        reclaimed += 1
        if reclaimed >= 64:
            break
    return reclaimed


def remember_public_detail_txn(db, provider, candidate, publication, observation):
    # No second documentary copy. For a distinct detail response keep a
    # receipt only when every added/conflicting field was durably admitted
    # into the SAME canonical member. A refusal, absent C, changed source or
    # nonretained detail field cannot yield a reusable proof.
    source = publication.source
    if (not db.in_transaction or observation.key.provider_id != provider
            or source.observation.key != observation.key
            or source.observation.observed_at > observation.observed_at
            or not _association(observation, candidate)):
        return
    value = {"source": source.document.source_key.removeprefix("notice-c:v1:"),
             "epoch": source.epoch, "revision": source.observation.source_revision,
             "observed_at": observation.observed_at}
    if source.observation != observation:
        current = _canonical_member(db, source)
        if current is None or publication.document != current[0].document:
            return
        comparison = compare_exact_purchase_fields(source.observation, observation, candidate)
        if comparison != publication.reconciliation:
            return
        # Detail currently contributes bibliography only, not a changed
        # thumbnail. Refuse caching an unretained positive cover.
        before = source.observation.fields_copy()["search_result"].get("thumbnail_url")
        after = observation.fields_copy()["search_result"].get("thumbnail_url")
        if after is not None and after != before:
            return
        facts = {fact.field: fact for fact in current[1].public_detail_facts}
        changed = []
        for change in comparison:
            fact = change.detail
            if fact is None:
                continue
            if change.state == "agreement":
                if change.retained.structured_evidence != fact.structured_evidence:
                    return
                continue
            stored = facts.get(change.field)
            if (stored is None or (stored.value, stored.provider_id, stored.source_ref,
                    stored.source_revision, stored.observed_at, stored.structured_evidence)
                    != (fact.value, fact.provider_id, fact.source_ref, fact.source_revision,
                        fact.observed_at, fact.structured_evidence)):
                return
            changed.append(change.field)
        value.update(detail_revision=observation.source_revision,
                     canonical_epoch=current[0].epoch, fields=sorted(changed))
    key = _key(provider, candidate.public_locator)
    exists = db.execute("SELECT 1 FROM metadata WHERE key=?", (key,)).fetchone()
    if not exists:
        count = db.execute("SELECT count(*) FROM metadata WHERE key>=? AND key<?",
                           (_PREFIX, _PREFIX + "\uffff")).fetchone()[0]
        if count >= _MAX:
            reclaimed = _reclaim_expired_public_details_txn(
                db, now=observation.observed_at,
            )
            if count - reclaimed >= _MAX:
                return
    db.execute("INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, json.dumps(value, separators=(",", ":"))))
