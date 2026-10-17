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


def merge_attested_owned_public_detail(owned, detail):
    """Safely enrich a *fresh, listing-matched* owned row from one public Book.

    This is a metadata projection, not a purchase/format validation. The
    authenticated library supplies ownership; the listing's identical product
    URL supplies only a candidate commercial edition. Neither that URL nor
    JSON-LD ISBN proves an EPUB/PDF variant or entitlement to any file.
    """
    import unicodedata

    error = "Numilog : public_detail_evidence_conflict"
    url = public_book_url(getattr(owned, "friendly_url", None))
    metadata = getattr(owned, "metadata", None)
    if (type(url) is not str or url != getattr(owned, "friendly_url", None)
            or getattr(owned, "source_provider", None) != "numilog"
            or getattr(owned, "source_collection", None) != "library"
            or getattr(owned, "owned", None) is not True
            or getattr(owned, "downloadable", None) is not True
            or getattr(owned, "download_url", None) is not None
            or type(metadata) is not dict):
        raise ProviderCapabilityError(error)
    listing = metadata.get("public_enrichment")
    if (type(listing) is not dict
            or listing.get("status") != "attested_listing_match"
            or listing.get("scope") != "public_listing_only"
            or listing.get("product_url") != url
            or type(listing.get("field_evidence")) is not dict):
        raise ProviderCapabilityError(error)
    try:
        _query(listing.get("source_url"))
    except Exception:
        raise ProviderCapabilityError(error) from None
    if (type(detail) is not dict or detail.get("product_url") != url
            or detail.get("owned") is not False
            or detail.get("downloadable") is not False
            or detail.get("download_url", False) is not None
            or type(detail.get("fields")) is not dict
            or type(detail.get("field_evidence")) is not dict):
        raise ProviderCapabilityError(error)
    fields = detail["fields"]
    evidence = detail["field_evidence"]
    allowed = {"title", "authors", "publisher", "published_at",
               "description", "language", "isbn_edition_unspecified"}
    if not set(fields).issubset(allowed) or set(fields) != set(evidence):
        raise ProviderCapabilityError(error)
    for key, proof in evidence.items():
        if (type(proof) is not dict
                or proof.get("kind") != "observed_public_jsonld"
                or proof.get("source_url") != url
                or proof.get("edition_proven") is not False):
            raise ProviderCapabilityError(error)
    def comparable(value):
        if type(value) is not str:
            return ""
        return unicodedata.normalize("NFKC", " ".join(value.split())).casefold()

    # URL equality is necessary but a contradictory product title makes the
    # fiche unusable. Never associate homonyms on title alone.
    if not comparable(fields.get("title")):
        raise ProviderCapabilityError("Numilog : public_detail_title_unverified")
    if comparable(fields["title"]) != comparable(owned.title):
        raise ProviderCapabilityError("Numilog : public_detail_title_conflict")

    projection, copied_evidence, conflicts = {}, {}, []
    for source_field, destination in (
        ("authors", "author"), ("publisher", "publisher"),
        ("published_at", "published_at"), ("description", "description"),
    ):
        value = fields.get(source_field)
        if source_field == "authors" and isinstance(value, list):
            if (not value or any(type(x) is not str or not x for x in value)):
                raise ProviderCapabilityError(error)
            value = " ; ".join(value)
        if value is None:
            continue
        if type(value) is not str or not value.strip():
            raise ProviderCapabilityError(error)
        current = getattr(owned, destination, None)
        if current:
            if comparable(current) != comparable(value):
                conflicts.append(source_field)
        else:
            projection[destination] = value
            copied_evidence[destination] = {
                "kind": "numilog.public_product_jsonld",
                "source_url": url, "field": source_field,
            }
    # Data absent from SearchResult's contract remains only in the
    # provenance subdocument, with explicit unresolved-edition semantics.
    next_metadata = dict(metadata)
    detail_summary = {
        "status": "attested_public_product",
        "scope": "public_detail_only",
        "product_url": url,
        "format_edition_proven": False,
        "rights_proven_by_public_detail": False,
        "field_evidence": copied_evidence,
        "listing_conflicts": tuple(conflicts),
    }
    if "language" in fields:
        if type(fields["language"]) is not str:
            raise ProviderCapabilityError(error)
        detail_summary["language"] = fields["language"]
    if "isbn_edition_unspecified" in fields:
        if type(fields["isbn_edition_unspecified"]) is not str:
            raise ProviderCapabilityError(error)
        detail_summary["isbn_edition_unspecified"] = fields["isbn_edition_unspecified"]
    next_metadata["public_detail_enrichment"] = detail_summary
    return replace(owned, metadata=next_metadata, **projection)
