"""Notice C cover attribute: source-owned facts, never a second book catalogue.

A verified NoticeSourceObservation may contribute *catalogue* image candidates
for its exact source/edition. Parsing an arbitrary raw URL is not permission to
fetch it, and neither a shared image nor an ISBN joins documentary identities.

The output is an immutable, revisioned Notice C attribute which the N2 adapter
may project only on demand. Origin/embedded tiers require separate, stronger
Core attestations; they cannot be manufactured from a source-notice field.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Literal
from urllib.parse import urlsplit

from .book_cover_projection import CoverDocumentScope
from .covers import CoverCandidate, candidate
from .notice_source_observation import (
    NoticeSourceKey, NoticeSourceObservation, _MAX_BYTES,
    admit_source_notice,
)

CoverAttributeState = Literal["available", "missing", "pending"]
_MAX_COVER_VALUES = 32
_MAX_FIELD_DEPTH = 8


@dataclass(frozen=True, slots=True)
class NoticeCoverAttribute:
    """Versioned attribute of ONE admitted source notice, not a media cache.

    'missing' is possible only when the producer explicitly declares that
    its *cover field inventory* and the source observation are complete.
    """
    document: CoverDocumentScope
    source_key: NoticeSourceKey
    source_revision: str
    field_path: tuple[str, ...]
    state: CoverAttributeState
    candidates: tuple[CoverCandidate, ...]

    @property
    def snapshot_complete(self) -> bool:
        return self.state != "pending"


def notice_cover_document_ref(key: NoticeSourceKey) -> CoverDocumentScope:
    """Construct a stable, exact Notice C DocumentRef, not an ISBN-based join.

    Only public and credential-scoped source catalogues are representable here.
    An embedded notice's Artifact SHA alone cannot attest the Placement needed
    to build a valid 'embedded' N2 source.
    """
    if type(key) is not NoticeSourceKey or key.visibility not in (
        "public", "private",
    ):
        raise ValueError("cover_notice_requires_exact_catalogue_source")
    # Reuse Core's full source-key validation rather than treating dataclass
    # construction as authentication of provider/account coordinates.
    from .notice_source_observation import _valid_key
    if not _valid_key(key):
        raise ValueError("invalid_cover_notice_source")
    coordinates = (
        "cifereca.notice-c.cover-document.v1",
        key.provider_id, key.visibility, key.identity_id,
        key.credential_id, key.account_id, key.branch_id,
        key.record_id, key.edition_id, key.manifestation_role,
    )
    digest = sha256(json.dumps(
        coordinates, ensure_ascii=True, separators=(",", ":"),
    ).encode("ascii")).hexdigest()
    return CoverDocumentScope(
        kind="public_catalogue" if key.visibility == "public" else "catalogue",
        identity_id=key.identity_id,
        source_key="notice-c:v1:" + digest,
        provider_id=key.provider_id,
        credential_id="__public__" if key.visibility == "public" else key.credential_id,
        account_id=key.account_id,
    )


def _admitted_fields(observation: NoticeSourceObservation) -> dict:
    """Replay the immutable admission; reject tampered/copied-in raw DTOs."""
    if (type(observation) is not NoticeSourceObservation
            or type(observation.raw_json) is not str):
        raise ValueError("invalid_cover_notice_observation")
    try:
        if len(observation.raw_json.encode("utf-8")) > _MAX_BYTES:
            raise ValueError("oversized source")
        fields = observation.fields_copy()
        verified = admit_source_notice(
            key=observation.key,
            parser_id=observation.parser_id,
            parser_version=observation.parser_version,
            observed_at=observation.observed_at,
            raw_fields=fields,
            status=observation.status,
        )
        if (verified.source_revision != observation.source_revision
                or verified.raw_json != observation.raw_json):
            raise ValueError("changed source")
    except (ValueError, TypeError, UnicodeError, RecursionError,
            OverflowError, AttributeError) as exc:
        raise ValueError("invalid_cover_notice_observation") from exc
    return fields


def project_notice_cover_attribute(
    observation: NoticeSourceObservation, *,
    field_path: tuple[str, ...],
    cover_inventory_complete: bool,
) -> NoticeCoverAttribute:
    """Qualify a *declared* parser field from one admitted Notice C source.

    The producer names its exact already-parsed field; Core does not guess
    from URL, title, ISBN or a neighbouring manifestation. A partial parsing
    or incomplete cover inventory never yields a negative 'missing' fact.
    A malformed image candidate does not silently turn into 'missing'.
    """
    if (type(field_path) is not tuple
            or not 1 <= len(field_path) <= _MAX_FIELD_DEPTH
            or any(type(name) is not str or not name
                   or not name.isprintable()
                   or len(name.encode("utf-8")) > 128
                   for name in field_path)):
        raise ValueError("invalid_notice_cover_field_path")
    if type(cover_inventory_complete) is not bool:
        raise TypeError("cover_inventory_complete must be explicit")
    fields = _admitted_fields(observation)
    document = notice_cover_document_ref(observation.key)
    value = fields
    for name in field_path:
        if type(value) is not dict or name not in value:
            value = None
            break
        value = value[name]

    complete = observation.status == "complete" and cover_inventory_complete
    found: list[CoverCandidate] = []
    malformed = False
    if value is None or value == []:
        pass
    else:
        values = [value] if type(value) is str else value
        if (type(values) is not list
                or not 1 <= len(values) <= _MAX_COVER_VALUES):
            malformed = True
        else:
            for url in values:
                if type(url) is not str:
                    malformed = True
                    break
                proof = candidate(
                    url, tier="catalog", source="notice-c",
                    evidence_id=document.source_key,
                )
                # Notice C's shared description must never embed local file
                # paths, plaintext credentials or a private HTTP transport.
                if (proof is None or urlsplit(proof.url).scheme != "https"):
                    malformed = True
                    break
                found.append(proof)

    # All-or-nothing malformed fields avoid selecting an apparently unique
    # candidate while suppressing a conflicting (unparsable) second value.
    if malformed:
        found.clear()
    state: CoverAttributeState = (
        "pending" if malformed or not complete else
        "available" if found else "missing"
    )
    return NoticeCoverAttribute(
        document=document,
        source_key=observation.key,
        source_revision=observation.source_revision,
        field_path=field_path,
        state=state,
        candidates=tuple(found),
    )


__all__ = [
    "NoticeCoverAttribute", "notice_cover_document_ref",
    "project_notice_cover_attribute",
]
