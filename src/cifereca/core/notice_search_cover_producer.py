"""#6366/#3724: real, public SearchResult cover -> admitted Notice C source.

Only the already-parsed row supplied by the Core search producer is read.
An image URL alone, a UI key, a title, an ISBN, a profile viewer or an N3
hit cannot establish a documentary association. No network or SQLite here.
"""
from __future__ import annotations

from hashlib import sha256
import json
from dataclasses import asdict

from ..models import SearchResult
from .notice_source_observation import (
    NoticeSourceKey, NoticeSourceObservation, admit_source_notice,
)
from .search_cover_provenance import project_exact_search_catalogue_cover


# Private Core-only object identity. An arbitrary supplier-provided attribute
# value (including True) can never admit public bibliographic disclosure.
_CORE_PUBLIC_C_WITNESS = object()


def _coordinate_digest(*parts: object) -> str:
    """Opaque, stable coordinates; never a title/ISBN/content deduplication."""
    raw = json.dumps(parts, ensure_ascii=True, separators=(",", ":"))
    return sha256(raw.encode("ascii")).hexdigest()


def admit_public_search_cover(
    row: SearchResult, *, identity_id: str, observed_at: int,
    public_cover_url: str | None,
) -> NoticeSourceObservation | None:
    """Admit one live producer-owned *public* image to the Notice C ABI.

    The caller must supply the URL from the existing Core public catalogue
    guard, not from the GTK widget. The additional search provenance gate
    rejects stale/offline copies, private credentials/accounts, owned and
    downloadable rows. An absent image is UNKNOWN: search-result omission
    does not attest a complete negative inventory.

    Coordinates preserve the producing provider, exact record, document
    base, collection, medium and optional supplier revision/edition marker.
    They do not merge editions or assign rights.
    """
    if type(row) is not SearchResult:
        return None
    if public_cover_url is None:
        # Without an image there is no legacy cover-only evidence. Require
        # BOTH Core-owned same-call witnesses before persisting a public
        # partial source: a provider-shaped row or cache replay is not proof.
        from .search_orchestration import _verified_public_cover_witness
        if (getattr(row, "_core_notice_c_public_bibliography", None)
                is not _CORE_PUBLIC_C_WITNESS
                or not _verified_public_cover_witness(row)):
            return None
        # A provider-declared PUBLIC bibliographic notice may legitimately
        # have no image. It is a C with an UNKNOWN cover inventory, not an
        # invented 'missing' negative and never an N3 image candidate.
        from .search_cover_provenance import exact_search_cover_scope
        scope = exact_search_cover_scope(row, identity_id)
        if (scope is None or scope.credential_id != "__public__"
                or row.source_collection != "public_catalog"
                or row.source_account_id is not None
                or row.owned is not False or row.downloadable is not False
                or row.download_url is not None
                or row.thumbnail_url is not None
                or type(row.metadata) is not dict
                or row.metadata.get("search_provenance") == "known_catalog_stale"):
            return None
    else:
        if type(public_cover_url) is not str or not public_cover_url:
            return None
        qualified = project_exact_search_catalogue_cover(
            row, identity_id=identity_id, share_public=True,
        )
        if (qualified is None
                or qualified.scope.kind != "public_catalogue"
                or qualified.catalogue.url != public_cover_url):
            return None
    key = NoticeSourceKey(
        provider_id=row.source_provider,
        visibility="public",
        identity_id=None, credential_id=None, account_id=None,
        branch_id="search.public.catalogue",
        record_id="search:" + _coordinate_digest(
            row.source_provider, row.source_collection, row.record_id,
        ),
        edition_id="edition:" + _coordinate_digest(
            row.source_provider, row.source_collection, row.record_id,
            row.docbase, row.medium, row.revision_id,
        ),
        manifestation_role=(
            "digital" if row.medium == "digital"
            else "print" if row.medium == "physical" else "unknown"
        ),
    )
    # This is the parser's single, present display-image field. No absence
    # or exhaustive origin/EPUB-cover inventory is asserted by a search hit.
    return admit_source_notice(
        key=key, parser_id="core.search.public-cover",
        parser_version="1", observed_at=observed_at,
        raw_fields={"search_result": {"thumbnail_url": public_cover_url}},
        status="complete" if public_cover_url is not None else "partial",
    )


def admit_public_search_catalogue(
    row: SearchResult, *, identity_id: str, observed_at: int,
    public_cover_url: str | None,
) -> NoticeSourceObservation | None:
    """Retain bounded *public bibliographic facts* inside the SAME C source.

    The existing cover-only writer remains available for legacy/unqualified
    rows. Only Core's freshly reconstructed anonymous same-call public result
    may carry the transient admission marker, set after its provider response
    and its public declaration were checked. A source/cache replay or a
    private-profile row cannot promote arbitrary title/metadata to C.
    No second catalogue record, GET, entitlement or heuristic edition join.
    """
    initial = admit_public_search_cover(
        row, identity_id=identity_id, observed_at=observed_at,
        public_cover_url=public_cover_url,
    )
    if (initial is None
            or getattr(row, "_core_notice_c_public_bibliography", None)
            is not _CORE_PUBLIC_C_WITNESS):
        return initial

    from .public_search_projection import (
        _public_bibliographic_text, declared_public_series_relation,
        declared_public_language_observation,
        declared_public_classification, declared_public_isbn_observation,
    )
    import re

    def coordinate(value: object, *, optional: bool = False) -> bool:
        if value is None and optional:
            return True
        return (
            type(value) is str
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", value) is not None
            and "://" not in value and ".." not in value
        )

    if (not coordinate(row.record_id)
            or not coordinate(row.docbase)
            or not coordinate(row.scenario)
            or row.medium not in ("digital", "physical", "unknown")
            or row.source_collection != "public_catalog"
            or not coordinate(row.revision_id, optional=True)
            or not _public_bibliographic_text(
                row.title, required=True, limit=512,
            ) or row.title.lstrip().casefold().startswith(
                ("/", "\\", "~/", "file:")
            )):
        return initial
    fields = {
        "schema": 1, "record_id": row.record_id,
        "docbase": row.docbase, "scenario": row.scenario,
        "source_collection": row.source_collection,
        "medium": row.medium, "revision_id": row.revision_id,
        "title": row.title,
    }
    for name in ("author", "format", "publisher", "published_at", "description"):
        value = getattr(row, name)
        if value is None:
            fields[name] = None
        elif (not _public_bibliographic_text(
                    value, limit=4000 if name == "description" else 512,
              ) or value.lstrip().casefold().startswith(
                  ("/", "\\", "~/", "file:")
              )):
            # The independently attested COVER is still useful, but reject
            # this untrusted descriptive value rather than leak it globally.
            return initial
        else:
            fields[name] = value

    relation = declared_public_series_relation(
        row.metadata, provider_id=row.source_provider, scenario=row.scenario,
    )
    if relation is not None:
        fields["series_relation"] = asdict(relation)
    language = declared_public_language_observation(
        row.metadata, provider_id=row.source_provider, scenario=row.scenario,
    )
    if language is not None:
        fields["language_observation"] = language.wire()
    isbns = declared_public_isbn_observation(
        row.metadata, provider_id=row.source_provider, scenario=row.scenario,
        record_id=row.record_id,
    )
    if isbns is not None:
        fields["isbn_observation"] = isbns.wire()
    classification = declared_public_classification(row.metadata)
    if classification is not None:
        fields["classification"] = classification.wire()
    from .notice_c_public_age import verified_same_call_public_age
    age = verified_same_call_public_age(row)
    if age is not None:
        fields["age_observation"] = age.wire()
    result = {"thumbnail_url": public_cover_url, "public_bibliography": fields}
    # An independently witnessed positive link is not a cover claim.
    # The omitted image stays pending. The existing source writer retains
    # any older complete observation rather than replacing it with partial.
    from .search_orchestration import _verified_public_web_reference
    link = _verified_public_web_reference(row)
    if link is not None:
        from .notice_c_web_references import FIELD
        result[FIELD] = {"schema": 1, "links": [link]}
    return admit_source_notice(
        key=initial.key, parser_id=initial.parser_id,
        parser_version="2", observed_at=observed_at,
        raw_fields={"search_result": result},
        status="complete" if public_cover_url is not None else "partial",
    )

__all__ = ["admit_public_search_cover", "admit_public_search_catalogue"]
