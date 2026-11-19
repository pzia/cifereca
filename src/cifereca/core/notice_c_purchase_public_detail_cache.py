"""Bounded public-only detail receipt, never a private association or grant."""
from hashlib import sha256
import json

from . import notice_c_job_journal as journal
from .notice_c_purchase_association import _DETAIL_TTL_SECONDS
from .notice_c_purchase_publication import _association
from .notice_c_public_search import _public_observation
from .notice_cover_repository import _PREFIX as SOURCE_PREFIX, _checked_source_state
from .notice_cover_projection import notice_cover_document_ref
from .notice_source_observation import NoticeSourceKey

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
        if (type(value) is not dict or set(value) != {"source", "epoch", "revision", "observed_at"}
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
                or source.observation.observed_at != value["observed_at"]
                or not _association(source.observation, candidate)):
            return None
        return source.observation
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
    # Only an actual qualified detail observation identical to the retained
    # source can be reused. A richer/different retained listing is not proof
    # that its data were observed in this detail response.
    if publication.source.observation != observation:
        return
    if observation.key.provider_id != provider or not _association(observation, candidate):
        return
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
    value = {"source": publication.source.document.source_key.removeprefix("notice-c:v1:"),
             "epoch": publication.source.epoch, "revision": observation.source_revision,
             "observed_at": observation.observed_at}
    db.execute("INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, json.dumps(value, separators=(",", ":"))))
