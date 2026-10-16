"""Bounded, attested enrichment of an owned Numilog book from ONE public listing.

No account, HTTP or acquisition functionality in this module. An owned card's
explicit public title URL is the sole edition association; title, cover,
private readBook lid and paper ISBN are never identifiers.
"""
from __future__ import annotations

from dataclasses import replace
from urllib.parse import urlsplit

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog_html import public_book_url
from cifereca_numilog_public import _query


def merge_attested_public_listing(owned, public_rows):
    """Return (new_owned_row, status), with no invented entitlement or edition.

    The public result must come from Numilog's structurally verified parser
    and have a product ID, canonical URL and safe source search URL all in
    agreement. Duplicates/contradictions fail closed rather than picking one.
    Only listing-level attributes are copied; ISBN, DRM, format and historic
    purchase price cannot be inferred.
    """
    owned_url = public_book_url(getattr(owned, "friendly_url", None))
    if owned_url is None:
        return owned, "no_attested_public_url"
    source_product = urlsplit(owned_url).path.split("/")
    if (len(source_product) != 3 or not source_product[1].isdigit()
            or not source_product[2].endswith(".ebook")):
        return owned, "product_edition_not_attested"
    product_id = source_product[1]
    matched = []
    for public in public_rows:
        if (getattr(public, "friendly_url", None) != owned_url
                or getattr(public, "record_id", None) != "numilog:public:" + product_id):
            continue
        meta = getattr(public, "metadata", None)
        if (type(meta) is not dict
                or meta.get("source_item_id") != product_id
                or getattr(public, "source_provider", None) not in ("numilog", "numilog-catalog")
                or getattr(public, "source_credential", None) != "__public__"
                or getattr(public, "source_collection", None) != "public_catalog"
                or getattr(public, "owned", None) is not False
                or getattr(public, "downloadable", None) is not False
                or getattr(public, "download_url", None) is not None):
            raise ProviderCapabilityError("Numilog : public_edition_evidence_conflict")
        source_url = meta.get("source_url")
        try:
            _query(source_url)  # Public search parameters only, never account/signatures.
        except Exception:
            raise ProviderCapabilityError("Numilog : public_edition_evidence_conflict") from None
        matched.append(public)

    if len(matched) > 1:
        raise ProviderCapabilityError("Numilog : public_edition_ambiguous")
    if not matched:
        return owned, "not_attested_on_first_page"

    public = matched[0]
    meta = public.metadata
    evidence = meta.get("field_evidence")
    if type(evidence) is not dict:
        raise ProviderCapabilityError("Numilog : public_edition_evidence_conflict")
    source = meta["source_url"]
    # Never copy unverified fields or caller-supplied upstream URLs into the
    # private row. Each copied public field must have a matching HTML selector
    # attestation pointing to exactly the validated public search response.
    public_to_owned = {
        "author": ("authors", "author"),
        "publisher": ("publisher", "publisher"),
        "published_at": ("published_at", "published_at"),
        "description": ("summary", "description"),
        "thumbnail_url": ("cover_url", "thumbnail_url"),
    }
    fields = {}
    copied_evidence = {}
    for public_field, (proof_key, owned_field) in public_to_owned.items():
        value = getattr(public, public_field, None)
        proof = evidence.get(proof_key)
        if not value or getattr(owned, owned_field, None):
            continue
        if (type(proof) is not dict or proof.get("source_url") != source
                or proof.get("kind") != "observed_public_html"):
            raise ProviderCapabilityError("Numilog : public_edition_evidence_conflict")
        fields[owned_field] = value
        copied_evidence[owned_field] = {
            "kind": "numilog.public_search_listing",
            "source_url": source,
            "selector": proof.get("source"),
        }
    metadata = dict(owned.metadata or {})
    metadata["public_enrichment"] = {
        "status": "attested_listing_match",
        "scope": "public_listing_only",
        "product_url": owned_url,
        "source_url": source,
        "field_evidence": copied_evidence,
    }
    if meta.get("price") is not None:
        price_proof = evidence.get("price")
        if (type(price_proof) is not dict or price_proof.get("source_url") != source
                or price_proof.get("kind") != "observed_public_html"):
            raise ProviderCapabilityError("Numilog : public_edition_evidence_conflict")
        metadata["public_offer_price"] = meta["price"]
    if type(meta.get("collection")) is str and meta.get("collection"):
        proof = evidence.get("collection")
        if (type(proof) is not dict or proof.get("source_url") != source
                or proof.get("kind") != "observed_public_html"):
            raise ProviderCapabilityError("Numilog : public_edition_evidence_conflict")
        metadata["public_collection"] = meta["collection"]
    # The catalogue listing may aggregate PDF and EPUB offers. Never copy
    # public.format / ISBN / DRM into the owned edition from a listing match.
    return replace(owned, metadata=metadata, **fields), "attested_listing_match"
