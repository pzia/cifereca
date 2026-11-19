"""Conservative purchase enrichment publication on the canonical C writer.

Only trusted Core composition may call this port after a qualified public
detail response. It never copies private facts into C, never treats the
purchase/product association as an acquired-edition equivalence, and never
replaces a richer earlier source with a cover-unknown detail observation.
The caller owns the purchase authority, lease and encompassing transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from . import notice_c_job_journal as journal
from .notice_c_purchase_intent import _allowed_txn, _spec_txn
from .notice_c_public_search import _public_observation
from .notice_c_public_search_replay import _anonymous_session
from .notice_search_cover_producer import admit_public_search_catalogue
from .search_orchestration import (
    _public_rows_from_response, _verified_public_web_reference,
)
from .notice_cover_projection import project_notice_cover_attribute
from .notice_cover_repository import _opaque_key, _checked_source_state, _persist_notice_cover_txn
from .notice_c_cover_index import _exact_public_search_evidence, canonical_cover_document_ref
from .notice_c_initial_publication import request_initial_publication_txn
from .notice_c_work_requests import request_source_worker_dependents_txn
from ..plugin_api import ProviderSearchResponse


@dataclass(frozen=True, slots=True, repr=False)
class PurchasePublicPublication:
    document: object
    source: object
    created: bool


def _title_key(value):
    if type(value) is not str or not value.strip():
        return None
    return unicodedata.normalize("NFKC", " ".join(value.split())).casefold()


def prepare_purchase_public_observation(response, session, candidate, *, observed_at):
    """Use the actual anonymous same-call ABI; no synthetic session or row.

    Time must be the producer's actual observation, not the current clock on
    cache reuse. The trusted caller separately verifies the transport receipt.
    The private title is only a local comparator; it is never projected.
    """
    provider = getattr(session, "provider_id", None)
    if (not _anonymous_session(session, provider)
            or type(response) is not ProviderSearchResponse or response.partial is not False
            or type(response.results) is not list or len(response.results) != 1
            or type(response.public_notices) is not tuple or len(response.public_notices) != 1
            or not journal._integer(observed_at, minimum=1)):
        raise ValueError("notice_purchase_public_response_required")
    rows = _public_rows_from_response(response, session)
    if len(rows) != 1:
        raise ValueError("notice_purchase_public_response_required")
    row = rows[0]
    link = _verified_public_web_reference(row)
    if (link is None or link["url"] != candidate.public_locator
            or _title_key(row.title) is None
            or _title_key(row.title) != _title_key(candidate.title)):
        raise ValueError("notice_purchase_public_association_changed")
    observation = admit_public_search_catalogue(
        row, identity_id="__public__", observed_at=observed_at,
        public_cover_url=row.thumbnail_url,
    )
    if observation is None or observation.parser_version != "2":
        raise ValueError("notice_purchase_public_response_required")
    return observation


def _association(source, candidate):
    fields = source.observation.fields_copy()["search_result"]
    bio = fields["public_bibliography"]
    links = fields.get("public_web_references", {}).get("links", ())
    return (_title_key(bio["title"]) == _title_key(candidate.title)
            and any(link.get("url") == candidate.public_locator for link in links))


def publish_purchase_public_txn(db, spec, snapshot, candidate, observation, *, now):
    """Atomic new public source or reuse exact retained compatible source.

    No write to an existing source, even its watermark, from a detail-only
    observation: this would misdate prior listing/cover facts. In particular
    a cache hit is NOT a fresh catalogue observation. A retained source
    with conflicting coordinates/title/link, or an evicted source, is
    refused rather than overwritten or reconstructed from partial evidence.
    A later per-field provenance reducer can add new detail facts safely.
    """
    if (not db.in_transaction or not journal._integer(now, minimum=1)
            or not journal._integer(observation.observed_at, minimum=1)
            or observation.observed_at > now
            or spec.implementation_digest != journal._PURCHASE_INTENT_DIGEST
            or not _allowed_txn(db, snapshot.scope)
            or _spec_txn(db, snapshot, candidate) != spec
            or observation.key.visibility != "public"
            or observation.parser_id != "core.search.public-cover"
            or observation.parser_version != "2"):
        raise ValueError("notice_purchase_publication_changed")
    field = ("search_result", "thumbnail_url")
    attr = project_notice_cover_attribute(
        observation, field_path=field, cover_inventory_complete=True,
    )
    key = _opaque_key(attr.document)
    if key is None:
        raise ValueError("notice_purchase_publication_changed")
    raw, old, fence = _checked_source_state(db, attr.document, key)
    if old is None:
        if fence is not None:
            raise ValueError("notice_purchase_evicted_source")
        source = _persist_notice_cover_txn(db, observation, attr, key, field, True, 0)
        if source is None or source.observation != observation:
            raise ValueError("notice_purchase_publication_changed")
        created = True
    else:
        source = old
        created = False
    # The repository's strict read decoder verifies complete public schema
    # and exact coordinates before any repair request is recorded.
    raw, live, _fence = _checked_source_state(db, attr.document, key)
    if (live != source or _public_observation(raw[0], key_string=key, now=now) is None
            or not _association(source, candidate)):
        raise ValueError("notice_purchase_public_association_changed")
    edition, _proof = _exact_public_search_evidence(source)
    if created:
        changed = request_source_worker_dependents_txn(db, source, now=now)
        if changed.overflow or changed.refused:
            raise ValueError("notice_purchase_dependents_refused")
    request_initial_publication_txn(db, source, now=now)
    return PurchasePublicPublication(canonical_cover_document_ref(edition), source, created)
