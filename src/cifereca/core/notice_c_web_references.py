"""#6397: versioned public web reference facts, never a browser/session grant.

The trusted parser/admission owner supplies the source observation. This
decoder only checks its bounded wire shape and the same URL fence used by
public Search. Authenticated references need a separate scoped contract.
"""
from __future__ import annotations

from dataclasses import dataclass
import unicodedata

FIELD = "public_web_references"
MAX_REFERENCES = 8


@dataclass(frozen=True, slots=True)
class CatalogueWebReference:
    provider_id: str
    source_ref: str
    record_id: str
    edition_id: str
    source_revision: str
    observed_at: int
    url: str
    evidence: str
    access_mode: str = "public"

    @property
    def label(self) -> str:
        return self.provider_id


def decode_public_web_references(raw, *, provider_id, source_ref, record_id,
                                 edition_id, source_revision, observed_at):
    """Bind a public link collection to its independently validated source.

    No proof inferred from URL alone, no fallback, no I/O. Absence of the
    optional extension means unknown, not a complete negative inventory.
    """
    from .search_orchestration import _public_catalog_url, _is_public_web_evidence

    # Source coordinates are an admission boundary, not user-defined
    # objects allowed to run equality, formatting or URL policy hooks.
    if (any(type(value) is not str or not 0 < len(value) <= 512
            for value in (provider_id, source_ref, record_id, edition_id,
                          source_revision))
            or any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
                   for value in (provider_id, source_ref, record_id, edition_id,
                                 source_revision) for char in value)
            or not source_ref.startswith("notice-c:v1:")
            or len(source_ref) != len("notice-c:v1:") + 64
            or any(char not in "0123456789abcdef" for char in source_ref[len("notice-c:v1:"):])
            or type(observed_at) is not int or not 1 <= observed_at < 2**63):
        raise ValueError("invalid_public_web_source_coordinates")

    if (type(raw) is not dict or set(raw) != {"schema", "links"}
            or type(raw["schema"]) is not int or raw["schema"] != 1
            or type(raw["links"]) is not list
            or not 1 <= len(raw["links"]) <= MAX_REFERENCES):
        raise ValueError("invalid_public_web_references")
    refs = []
    seen = set()
    for item in raw["links"]:
        if (type(item) is not dict or set(item) != {"url", "evidence"}
                or type(item["url"]) is not str
                or type(item["evidence"]) is not str
                or not _is_public_web_evidence(item["evidence"], provider_id)
                or _public_catalog_url(item["url"], evidence=item["evidence"],
                                       provider_id=provider_id, record_id=record_id) != item["url"]
                or item["url"] in seen):
            raise ValueError("invalid_public_web_references")
        seen.add(item["url"])
        refs.append(CatalogueWebReference(
            provider_id, source_ref, record_id, edition_id,
            source_revision, observed_at, item["url"], item["evidence"],
        ))
    return tuple(sorted(refs, key=lambda ref: ref.url))


def qualified_public_web_source(source, *, inventory_complete):
    """Exact Search v2 positive field, even when its cover is unknown.

    Only the trusted source writer may persist this observation. A generic
    partial parser or malformed candidate is not the declared thumbnail
    omission of this ABI.
    """
    obs = source.observation
    attr = source.attribute
    if (inventory_complete is not True
            or obs.key.visibility != "public"
            or obs.key.branch_id != "search.public.catalogue"
            or obs.parser_id != "core.search.public-cover"
            or obs.parser_version != "2"
            or attr.field_path != ("search_result", "thumbnail_url")):
        return False
    fields = obs.fields_copy().get("search_result")
    if (type(fields) is not dict
            or set(fields) != {"thumbnail_url", "public_bibliography", FIELD}):
        return False
    return (
        (obs.status == "complete" and (
            (attr.state == "available" and bool(attr.candidates))
            or (attr.state == "missing" and not attr.candidates
                and fields["thumbnail_url"] is None)))
        or (obs.status == "partial" and attr.state == "pending"
            and not attr.candidates and fields["thumbnail_url"] is None)
    )
