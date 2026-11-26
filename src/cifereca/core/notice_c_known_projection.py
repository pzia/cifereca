"""#6366/#2385: read-only C-first results for the existing async known Search.

A PUBLIC Notice C source has a durable DocumentRef and descriptive text,
but NO provider session, identity owner, loan or acquisition. Never merge C
with another notice by title/ISBN/image/legacy record ID. Keep an independently
possessed local book, and suppress only the exact duplicated anonymous public
mirror source when its full edition coordinates agree.

This pure projection performs no SQLite/GTK/network/file I/O; its caller owns
the asynchronously scheduled query and the identity/generation lease.
"""
from __future__ import annotations

from typing import Any
from dataclasses import asdict, fields

from .notice_c_public_search import PublicNoticeCPage, PublicNoticeCHit
from .book_cover_projection import CoverDocumentScope
from .notice_cover_repository import is_notice_cover_document
from .notice_c_cover_index import _MAX_SOURCES

_MAX = 50
_PRIMARY_FACT_TOKEN = object()
_PRIMARY_FACT_FIELD = "_core_notice_c_primary_facts"
_PRIMARY_HIT_SEAL_FIELD = "_core_notice_c_primary_hit_seal"
_QUERY_COVERAGE_FIELD = "_core_notice_c_query_coverage"
_QUERY_COVERAGE_TOKEN = object()
# This owner-only snapshot records the original hit, independently of the
# mutable view mapping and its field-by-field presentation provenance.
_PRIMARY_HIT_SEAL_NAMES = (
    "provider_id", "record_id", "docbase", "medium", "source_revision",
    "title", "author", "publisher", "published_at", "media_format",
    "description", "stale", "cover_state", "edition_id", "epoch",
    "observed_at", "last_seen_at",
)
# A frozen CoverDocumentScope is also mutable via object.__setattr__.
# Its native fields belong to the original Core receipt, not to the view.
_PRIMARY_DOCUMENT_SEAL_NAMES = (
    "kind", "identity_id", "source_key", "provider_id", "credential_id",
    "account_id", "loan_id", "artifact_id", "placement_id",
)


def _source_evidence_snapshot(hit):
    """Bounded native receipt for one source's genre, language, series and age.

    Frozen bibliography evidence dataclasses can be mutated in place. No
    provider callback, source join, rights inference or serialization is used.
    """
    from .public_search_projection import (
        PublicClassification, PublicSeriesRelation, PublicLanguageObservation, PublicIsbnObservation,
    )
    from .notice_c_public_age import PublicAgeObservation, PublicAgeRange

    known = (
        PublicClassification, PublicSeriesRelation, PublicLanguageObservation, PublicIsbnObservation,
        PublicAgeObservation, PublicAgeRange,
    )
    remaining = [512]

    def freeze(value, depth=0):
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 8:
            raise ValueError("invalid_public_source_evidence_budget")
        kind = type(value)
        if kind in (str, int, bool, type(None)):
            if kind is str and len(value) > 8192:
                raise ValueError("invalid_public_source_evidence_text")
            return kind.__name__, value
        if kind is tuple:
            if len(value) > 64:
                raise ValueError("invalid_public_source_evidence_tuple")
            return "tuple", tuple(freeze(member, depth + 1) for member in value)
        if kind in known:
            return kind.__name__, tuple(
                freeze(getattr(value, field.name), depth + 1)
                for field in fields(kind)
            )
        raise ValueError("invalid_public_source_evidence_type")

    if type(hit) is not PublicNoticeCHit:
        return None
    try:
        return tuple(freeze(getattr(hit, name)) for name in (
            "classification", "language_observation", "series_relation",
            "age_observation", "isbn_observation",
        ))
    except (AttributeError, ValueError, TypeError, RecursionError):
        return None


def _same_source_evidence_snapshot(left, right, depth=0):
    """Compare only exact native snapshot scalars, never foreign equality."""
    if depth > 12 or type(left) is not type(right):
        return False
    if type(left) is tuple:
        return (len(left) == len(right)
                and all(_same_source_evidence_snapshot(a, b, depth + 1)
                        for a, b in zip(left, right)))
    if type(left) in (str, int, bool, type(None)):
        return left == right
    return False


def _native_c_source_ref(document):
    # Guard a frozen DocumentRef's mutable scalar members before dataclass
    # equality or repository source routing; a correct class alone is not
    # sufficient when a view has substituted foreign __eq__/__hash__.
    if type(document) is not CoverDocumentScope:
        return False
    for name in ("kind", "source_key", "provider_id", "credential_id",
                 "loan_id", "artifact_id", "placement_id"):
        value = getattr(document, name)
        if type(value) is not str or len(value) > 512 or not value.isprintable():
            return False
    if (document.identity_id is not None
            and (type(document.identity_id) is not str
                 or len(document.identity_id) > 512
                 or not document.identity_id.isprintable())):
        return False
    if (document.account_id is not None
            and (type(document.account_id) is not str
                 or len(document.account_id) > 512
                 or not document.account_id.isprintable())):
        return False
    try:
        # Reapply the dataclass's original scope constraints after mutation:
        # public C remains anonymous; private catalogue stays owner-scoped.
        document.__post_init__()
    except (ValueError, TypeError):
        return False
    return is_notice_cover_document(document)


def known_public_primary_facts(row):
    """Read the exact Core-created C-source packet, not loose metadata proof.

    A copied/modified metadata mapping cannot acquire a provenance by
    supplying a provider name, a proof-shaped dict or a DocumentRef alone.
    The view still owns its existing mapped-row/context/epoch lease.
    """
    # A view mapping is mutable, unlike the Core-only sealed packet.
    # Validate native types BEFORE equality: an injected source/identity,
    # DocumentRef or display field must not execute foreign __eq__ in UI.
    if (type(row) is not dict or len(row) > 256
            or any(type(name) is not str for name in row)
            or type(row.get("source")) is not str
            or row["source"] != "notice_c_public"):
        return None
    packet = row.get(_PRIMARY_FACT_FIELD)
    seal = row.get(_PRIMARY_HIT_SEAL_FIELD)
    meta = row.get("metadata")
    identity = row.get("identity_id")
    if (type(packet) is not tuple or len(packet) != 3
            or packet[0] is not _PRIMARY_FACT_TOKEN
            or type(identity) is not str or type(packet[1]) is not str
            or packet[1] != identity
            or type(packet[2]) is not PublicNoticeCHit
            or type(meta) is not dict or len(meta) > 256
            or any(type(name) is not str for name in meta)):
        return None
    hit = packet[2]
    # Frozen dataclass fields can still be replaced using object.__setattr__.
    # The original scalar receipt is independent from the mutable view row;
    # inspect native types before comparing to avoid hostile __eq__.
    if (type(seal) is not tuple or len(seal) != 5
            or seal[0] is not _PRIMARY_FACT_TOKEN
            or type(seal[1]) is not int or seal[1] != id(hit)
            or type(seal[2]) is not tuple
            or len(seal[2]) != len(_PRIMARY_HIT_SEAL_NAMES)
            or any(type(original) not in (str, int, bool, type(None))
                   or (type(original) is str and len(original) > 8192)
                   for original in seal[2])
            or type(seal[3]) is not tuple
            or len(seal[3]) != len(_PRIMARY_DOCUMENT_SEAL_NAMES)
            or any(type(original) not in (str, type(None))
                   or (type(original) is str and len(original) > 512)
                   for original in seal[3])
            or type(seal[4]) is not tuple
            or type(hit.document) is not CoverDocumentScope):
        return None
    for name, original in zip(_PRIMARY_HIT_SEAL_NAMES, seal[2]):
        current = getattr(hit, name)
        if type(current) is not type(original) or current != original:
            return None
    for name, original in zip(_PRIMARY_DOCUMENT_SEAL_NAMES, seal[3]):
        current = getattr(hit.document, name)
        if type(current) is not type(original) or current != original:
            return None
    if not _same_source_evidence_snapshot(_source_evidence_snapshot(hit), seal[4]):
        return None
    document = row.get("_cover_document_ref")
    if (not _native_c_source_ref(hit.document)
            or not _native_c_source_ref(document)
            or hit.document != document
            or type(hit.epoch) is not int
            or type(meta.get("notice_c_epoch")) is not int
            or hit.epoch != meta["notice_c_epoch"]):
        return None
    for name, expected in (
        ("provider_id", hit.provider_id),
        ("item_id", hit.record_id),
        ("title", hit.title),
    ):
        supplied = row.get(name)
        if type(expected) is not str or type(supplied) is not str or supplied != expected:
            return None
    # The source hit is a frozen dataclass, but object.__setattr__ can still
    # replace its scalar fields after the C-first row was projected. The
    # grouped Search source filter must not attribute the other author's,
    # medium's or descriptive facts to this primary edition simply because
    # provider/record/title still match the original receipt.
    for supplied, expected in (
        (row.get("author"), hit.author),
        (row.get("published_at"), hit.published_at),
        (row.get("media_format"), hit.media_format),
        (row.get("medium"), hit.medium),
        # Publisher and description are independently qualified by their
        # exact displayed value in the presentation layer. Altering one
        # displayed field must not revoke unrelated intact provenances.
        (meta.get("notice_c_cover_state"), hit.cover_state),
    ):
        if (type(expected) not in (str, type(None))
                or type(supplied) is not type(expected)
                or supplied != expected):
            return None
    if (type(hit.stale) is not bool
            or meta.get("notice_c_stale") is not hit.stale):
        return None
    return hit



def known_public_alternative_facts(row):
    """Reuse the already sealed source-group packet, never serialized alternatives."""
    from .notice_c_loaded_filter import _sources

    primary = known_public_primary_facts(row)
    if primary is None:
        return ()
    hits = _sources(row)
    if not hits or hits[0] is not primary:
        return ()
    return hits[1:]


def _public_mirror_key(row: dict[str, Any], *, identity_id: str) -> tuple | None:
    """Only an exact, unpossessed, descriptive anonymous mirror is redundant."""
    # These are provider-mirrored presentation dictionaries, not an
    # immutable Core witness. Inspect native keys/values before any equality
    # or truthiness: foreign __eq__/__bool__ must not affect a C-first view.
    if type(row) is not dict or any(type(name) is not str for name in row):
        return None
    source, owner, state = (
        row.get("source"), row.get("identity_id"), row.get("state"),
    )
    if (type(source) is not str or source != "known_catalog"
            or type(owner) is not str or owner != identity_id
            or row.get("local") is not False
            or type(state) is not str
            or state not in ("remote_catalog", "available_to_buy")):
        return None
    for name in ("placement_id", "artifact_id", "local_path"):
        value = row.get(name)
        if value is not None and (type(value) is not str or bool(value)):
            return None
    meta = row.get("metadata")
    if (type(meta) is not dict
            or any(type(name) is not str for name in meta)
            or type(meta.get("catalogue_scope_version")) is not int
            or meta["catalogue_scope_version"] not in (2, 3)
            or type(meta.get("source_credential")) is not str
            or meta["source_credential"] != "__public__"
            or type(meta.get("source_collection")) is not str
            or meta["source_collection"] != "public_catalog"):
        return None
    account = meta.get("source_account_id")
    if account is not None and (type(account) is not str or account != ""):
        return None
    revision = meta.get("source_revision")
    if meta["catalogue_scope_version"] == 2:
        if revision is not None and (type(revision) is not str or revision != ""):
            return None
        revision = None
    elif type(revision) is not str or not revision:
        return None
    coords = (row.get("provider_id"), row.get("item_id"),
              meta.get("docbase"), meta.get("medium"))
    if any(type(value) is not str or not value for value in coords):
        return None
    return (*coords, revision)

def _series_metadata(hit: PublicNoticeCHit) -> dict[str, Any]:
    metadata = hit.classification.wire() if hit.classification is not None else {}
    proofs = {}
    if hit.series_relation is not None:
        proof = asdict(hit.series_relation)
        metadata["series"] = [proof.pop("label")]
        proofs["series"] = proof
    if hit.language_observation is not None:
        proof = hit.language_observation.wire()
        metadata["languages"] = proof.pop("languages")
        proofs["languages"] = proof
    if hit.age_observation is not None:
        proofs["youth_age"] = hit.age_observation.wire()
        ranges = hit.age_observation.filter_ranges()
        if ranges:
            metadata["youth_age_ranges"] = ranges
    if proofs:
        metadata["bibliography_evidence"] = proofs
    return metadata


def _source_alternative(hit: PublicNoticeCHit) -> dict[str, Any]:
    """The same source-specific descriptive alternative in both known views."""
    return {
        "provider_id": hit.provider_id,
        "record_id": hit.record_id,
        "source_revision": hit.source_revision,
        "title": hit.title,
        "author": hit.author,
        "publisher": hit.publisher,
        "description": hit.description,
        **({"published_at": hit.published_at} if hit.published_at is not None else {}),
        **_series_metadata(hit),
    }


def merge_public_notice_c_known_rows(
    known: list[dict[str, Any]], page: PublicNoticeCPage,
    *, identity_id: str,
) -> list[dict[str, Any]]:
    """Present known C before the mirror; source C never conveys rights.

    The C query cursor limits itself to 50 known documents or source facts, so this first
    stage has no claim to full query coverage. An empty page is unknown, not
    evidence that a provider has zero documents.
    """
    # PublicNoticeCPage is frozen but its collection members may have been
    # replaced after a Core read. Validate native shape before len(), hashes,
    # document comparisons or rendering any untrusted source fact.
    if (type(known) is not list or type(page) is not PublicNoticeCPage
            or type(identity_id) is not str or not identity_id
            or type(page.hits) is not tuple or len(page.hits) > _MAX
            or any(type(hit) is not PublicNoticeCHit
                   or not _native_c_source_ref(hit.document)
                   for hit in page.hits)
            or type(page.matching_sources) is not tuple
            or len(page.matching_sources) > _MAX * _MAX_SOURCES
            or any(type(hit) is not PublicNoticeCHit
                   or not _native_c_source_ref(hit.document)
                   for hit in page.matching_sources)
            or any(type(value) is not int or not 0 <= value < 2**63
                   for value in (page.known_matches, page.scanned_sources,
                                 page.unindexed_sources))
            or (page.known_documents is not None
                and (type(page.known_documents) is not int
                     or not 0 <= page.known_documents < 2**63))
            or type(page.has_more) is not bool
            or type(page.status) is not str
            or page.status not in ("partial", "budget_exceeded")):
        raise ValueError("invalid_known_public_c_projection")

    hits = page.hits
    if page.known_documents is not None:
        # A grouped page can carry up to 32 exact sources per visible C.
        # Do not reintroduce an unrelated group or silently truncate its
        # admitted matching alternatives when suppressing public mirrors.
        hits = page.matching_sources
        if (len(hits) > _MAX * _MAX_SOURCES
                or {hit.document for hit in hits} != {hit.document for hit in page.hits}):
            raise ValueError("invalid_grouped_public_c_projection")
    rows: list[dict[str, Any]] = []
    admitted: set[tuple] = set()
    by_document: dict[str, dict[str, Any]] = {}
    source_groups: dict[str, list[PublicNoticeCHit]] = {}
    for hit in hits:
        evidence_snapshot = _source_evidence_snapshot(hit)
        if (evidence_snapshot is None
                or type(hit) is not PublicNoticeCHit
                or hit.document.kind != "public_catalogue"
                or hit.document.identity_id is not None
                or not is_notice_cover_document(hit.document)):
            continue
        admitted.add((hit.provider_id, hit.record_id, hit.docbase,
                      hit.medium, hit.source_revision))
        source_groups.setdefault(hit.document.source_key, []).append(hit)
        prior = by_document.get(hit.document.source_key)
        if prior is not None:
            # Distinct providers can contribute different public text to ONE
            # canonical cover document. Keep their independently attested
            # descriptions as alternatives; never silently treat the first
            # source's words as a reconciled canonical bibliography.
            alternatives = prior["metadata"].setdefault(
                "notice_c_source_alternatives", [],
            )
            alternatives.append(_source_alternative(hit))
            continue
        rows.append({
            "key": "c-public:" + hit.document.source_key,
            "source": "notice_c_public",
            "identity_id": identity_id,
            "provider_id": hit.provider_id,
            "item_id": hit.record_id,
            "title": hit.title,
            "author": hit.author,
            "published_at": hit.published_at,
            "media_format": hit.media_format,
            "medium": hit.medium,
            "state": "remote_catalog",
            "local": False,
            "local_path": None,
            "thumbnail_url": None,  # N2 reads C; never raw URL fallback.
            "metadata": {
                "notice_c_public": True,
                "notice_c_stale": hit.stale,
                "notice_c_cover_state": hit.cover_state,
                "notice_c_epoch": hit.epoch,
                "notice_c_query_status": page.status,
                "publisher": hit.publisher,
                "description": hit.description,
                **_series_metadata(hit),
            },
            "_cover_document_ref": hit.document,
        })
        # Keep the immutable primary contribution alongside its display
        # mapping. Never reconstruct per-field provenance from that mapping.
        rows[-1][_PRIMARY_FACT_FIELD] = (_PRIMARY_FACT_TOKEN, identity_id, hit)
        rows[-1][_PRIMARY_HIT_SEAL_FIELD] = (
            _PRIMARY_FACT_TOKEN, id(hit),
            tuple(getattr(hit, name) for name in _PRIMARY_HIT_SEAL_NAMES),
            tuple(getattr(hit.document, name) for name in _PRIMARY_DOCUMENT_SEAL_NAMES),
            evidence_snapshot,
        )
        by_document[hit.document.source_key] = rows[-1]
    from .notice_c_loaded_filter import attach_filter_sources
    for key, group in source_groups.items():
        if len(group) > 1:
            attach_filter_sources(by_document[key], tuple(group))
    if page.known_documents is not None and rows:
        # One descriptive receipt for the initial C query cohort, not
        # a provider total or a count for a later loaded-filter edit.
        coverage = (_QUERY_COVERAGE_TOKEN, identity_id, page.known_documents,
                    page.status, page.has_more)
        for row in rows:
            row[_QUERY_COVERAGE_FIELD] = coverage
    for row in known:
        # Do not replace local placements, verified rights, private sources,
        # different revisions or medium with public descriptive metadata.
        if _public_mirror_key(row, identity_id=identity_id) not in admitted:
            rows.append(row)
    return rows


def known_public_c_loaded_coverage(rows, *, identity_id):
    """Describe one already loaded C cohort, not current filter coverage.

    Keep document counts distinct from source occurrences and suppress mixed
    or ungrouped cohorts. No SQLite, supplier request, action or new proof.
    """
    if type(identity_id) is not str or not identity_id:
        return None
    cohort = None
    documents = set()
    for row in rows:
        if type(row) is not dict or row.get("source") != "notice_c_public":
            continue
        packet = row.get(_QUERY_COVERAGE_FIELD)
        if (type(packet) is not tuple or len(packet) != 5
                or packet[0] is not _QUERY_COVERAGE_TOKEN
                or type(packet[1]) is not str or packet[1] != identity_id
                or type(packet[2]) is not int or not 0 < packet[2] < 2**63
                or type(packet[3]) is not str or packet[3] not in ("partial", "budget_exceeded")
                or type(packet[4]) is not bool
                or (cohort is not None and cohort is not packet)):
            return None
        hit = known_public_primary_facts(row)
        if hit is None or row["identity_id"] != identity_id:
            return None
        cohort = packet
        documents.add(hit.document.source_key)
        if len(documents) > _MAX:
            return None
    if cohort is None or len(documents) > cohort[2]:
        return None
    return len(documents), cohort[2], cohort[3], cohort[4]


def matching_live_public_canonical_ref(row: object, *, identity_id: str):
    """Match one same-call live result to its Core-attested canonical C ref.

    A mutable provider-facing SearchResult may carry arbitrary attributes.
    Re-derive both source and edition coordinates under the private Core
    witness; a title/URL alone cannot retire a C-first row from the view.
    Pure projection: no DB, GTK, entitlement, image IO or provider calls.
    """
    from ..models import SearchResult
    from .search_orchestration import _verified_public_cover_witness
    from .notice_search_cover_producer import admit_public_search_cover
    from .notice_cover_projection import notice_cover_document_ref
    from .notice_c_cover_index import (
        canonical_cover_document_ref, is_canonical_cover_document,
    )

    if (type(row) is not SearchResult
            or type(identity_id) is not str or not identity_id
            or not _verified_public_cover_witness(row)):
        return None
    source = getattr(row, "_core_notice_cover_document_ref", None)
    canonical = getattr(row, "_core_notice_canonical_cover_document_ref", None)
    if not is_canonical_cover_document(canonical):
        return None
    try:
        admitted = admit_public_search_cover(
            row, identity_id=identity_id, observed_at=1,
            public_cover_url=row.thumbnail_url,
        )
        if (admitted is not None
                and notice_cover_document_ref(admitted.key) == source):
            if canonical_cover_document_ref(
                admitted.key.edition_id,
            ) == canonical:
                return canonical
            # A true cross-provider target is never derivable from Search
            # title/ISBN/thumbnail/provider fields. Only the Core worker
            # can stamp this private token AFTER verifying two completed,
            # same-owner provider OriginLinks and persisting both sources.
            # No SQL or entitlement lookup is performed on GTK here.
            from .notice_c_origin_link_producer import (
                _CORE_ORIGIN_LINK_C_WITNESS,
            )
            proof = getattr(row, "_core_notice_cross_provider_proof", None)
            epoch = getattr(
                row, "_core_notice_canonical_cover_document_epoch", None,
            )
            if (type(proof) is tuple and len(proof) == 4
                    and proof[0] is _CORE_ORIGIN_LINK_C_WITNESS
                    and proof[1] == source.source_key
                    and proof[2] == canonical.source_key
                    and type(epoch) is int and epoch > 0
                    and proof[3] == epoch):
                return canonical
    except (TypeError, ValueError, UnicodeError):
        pass
    return None


__all__ = ["merge_public_notice_c_known_rows", "matching_live_public_canonical_ref"]
