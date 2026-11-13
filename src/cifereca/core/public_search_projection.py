"""Inert admission of explicit public catalogue rows (#5048/#5518).

No supplier grammar, provider call, storage, or permission inference. Caller
retains the live search identity/session/generation lease before publication.
"""
from __future__ import annotations

import re

from ..models import SearchResult
from ..plugin_api.provider_search_facets import ProviderSearchResponse, PublicSearchNotice
from .notice_source_observation import _safe_scalar

_KEY_FIELDS = (
    "id", "source_provider", "source_credential", "docbase", "record_id",
    "scenario", "source_collection", "medium",
)
_TEXT_FIELDS = (
    "title", "author", "type", "format", "publisher", "published_at",
    "description", "content_type",
)
_BIBLIOGRAPHY = frozenset({
    "language", "languages", "original_language", "isbn", "isbns", "series",
    "collection", "publisher", "subjects", "subject", "genres", "genre",
    "authors", "contributors", "edition", "tags", "keywords", "themes",
})


def _text(value: object, *, required: bool = False, limit: int = 8192) -> bool:
    return (value is None and not required) or (
        type(value) is str and len(value) <= limit
        and (not required or bool(value.strip())) and _safe_scalar(value)
        and not any(ord(c) < 32 and c not in "\n\r\t" for c in value)
    )


_PRIVATE_BIBLIOGRAPHIC_PATTERN = re.compile(
    r"(?i)(?:https?://|(?<!\\w)(?:bearer|basic)\\s+\\S+|"
    r"(?<!\\w)(?:token|auth|cookie|signature|access_token|refresh_token)\\s*=|"
    r"[^\\s@]+@[^\\s@]+\\.[^\\s@]+)"
)


def _public_bibliographic_text(
    value: object, *, required: bool = False, limit: int = 8192,
) -> bool:
    """A public notice cannot launder private URLs or auth/account strings."""
    return (
        _text(value, required=required, limit=limit)
        and (value is None or (
            type(value) is str
            and _PRIVATE_BIBLIOGRAPHIC_PATTERN.search(value) is None
        ))
    )


def _key(row: object) -> tuple | None:
    if type(row) is not SearchResult:
        return None
    values = tuple(getattr(row, name) for name in _KEY_FIELDS)
    if any(not _text(value, required=index != 6, limit=512)
           for index, value in enumerate(values)):
        return None
    if row.medium not in ("digital", "physical", "unknown"):
        return None
    return values


def project_public_search_result(
    response: ProviderSearchResponse, row: SearchResult, *,
    provider_id: str, credential_id: str,
) -> SearchResult | None:
    """Return the unique public projection of an exact response row or refuse.

    provider_id/credential_id are the expected *row source*, independently
    checked by the caller against the producing session (anonymous rows may
    have a distinct source). Never substitute the active profile's credential.
    Match the same response's exact row object, not a cached key from an old
    request. Revalidate the caller's identity/epoch lease after this function.

    v1 deliberately does not carry URLs, classifications or arbitrary extras.
    A producer's declaration attests public parsing, not legal rights, source
    ownership, complete coverage, or the truth of bibliographic information.
    """
    if (type(response) is not ProviderSearchResponse
            or type(response.results) is not list or len(response.results) > 10000
            or not any(item is row for item in response.results)
            or type(response.public_notices) is not tuple
            or len(response.public_notices) > 10000
            or not _text(provider_id, required=True, limit=512)
            or not _text(credential_id, required=True, limit=512)):
        return None
    key = _key(row)
    if (key is None or row.source_provider != provider_id
            or row.source_credential != credential_id):
        return None
    matches = []
    for notice in response.public_notices:
        if (type(notice) is not PublicSearchNotice
                or type(notice.schema_version) is not int
                or notice.schema_version != 1):
            return None
        candidate_key = _key(notice.result)
        if candidate_key is None:
            return None
        if candidate_key == key:
            matches.append(notice.result)
    if len(matches) != 1:
        return None
    public = matches[0]
    # Contradictory declarations are not silently laundered into public rows.
    if (type(public.owned) not in (bool, type(None)) or public.owned is True
            or type(public.downloadable) not in (bool, type(None))
            or public.downloadable is True
            or public.download_url is not None or public.source_account_id is not None
            or any(not _public_bibliographic_text(
                       getattr(public, name), required=name == "title",
                   ) for name in _TEXT_FIELDS)
            or type(public.metadata) is not dict or len(public.metadata) > 256):
        return None
    metadata = {"catalogue_access": "shared_read_only"}
    for name in _BIBLIOGRAPHY:
        value = public.metadata.get(name)
        if value is None:
            continue
        if type(value) is str and _public_bibliographic_text(value):
            metadata[name] = value
        elif (type(value) in (list, tuple) and len(value) <= 32
              and all(type(part) is str and _public_bibliographic_text(part)
                      for part in value)):
            metadata[name] = list(value)
        else:
            return None
    # Construct afresh so future SearchResult fields cannot accidentally gain
    # public visibility through dataclasses.replace/asdict or raw metadata.
    return SearchResult(
        **dict(zip(_KEY_FIELDS, key)),
        **{name: getattr(public, name) for name in _TEXT_FIELDS},
        thumbnail_url=None, friendly_url=None,
        owned=False, downloadable=False, download_url=None, metadata=metadata,
    )
