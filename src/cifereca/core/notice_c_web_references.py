"""#6397: versioned public web reference facts, never a browser/session grant.

The trusted parser/admission owner supplies the source observation. This
decoder only checks its bounded wire shape and the same URL fence used by
public Search. Authenticated references need a separate scoped contract.
"""
from __future__ import annotations

from dataclasses import dataclass

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
    from .search_orchestration import _public_catalog_url

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
                or item["evidence"] != f"{provider_id}.public_search_product_card"
                or _public_catalog_url(item["url"], evidence=item["evidence"],
                                       provider_id=provider_id) != item["url"]
                or item["url"] in seen):
            raise ValueError("invalid_public_web_references")
        seen.add(item["url"])
        refs.append(CatalogueWebReference(
            provider_id, source_ref, record_id, edition_id,
            source_revision, observed_at, item["url"], item["evidence"],
        ))
    return tuple(sorted(refs, key=lambda ref: ref.url))
