"""Bounded, attested enrichment of an owned Numilog book from ONE public listing.

No account, HTTP or acquisition functionality in this module. An owned card's
explicit public title URL is the sole edition association; title, cover,
private readBook lid and paper ISBN are never identifiers.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
import unicodedata
import re
from urllib.parse import urlsplit

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog_html import public_book_url
from cifereca_numilog_public import _query, _safe_text


def _qualified_public_listing_value(public, field, value):
    """Recheck parsed HTML values at the owned-library trust boundary.

    The public SearchResult can be cached, passed by a caller or mutated after
    HTML parsing. An exact selector proof does not validate its present value.
    """
    valid = type(value) is str and bool(value.strip())
    if valid and field == "author":
        names = getattr(public, "metadata", {}).get("authors")
        valid = (
            _safe_text(value, 32 * 1000 + 96)
            and type(names) is list and 1 <= len(names) <= 32
            and all(type(name) is str and bool(name.strip())
                    and _safe_text(name, 1000) for name in names)
            and value == " ; ".join(names)
        )
    elif valid and field == "publisher":
        valid = _safe_text(value, 2000)
    elif valid and field == "published_at":
        valid = _safe_text(value, 10) and re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value,
        ) is not None
        if valid:
            try:
                valid = date.fromisoformat(value).isoformat() == value
            except ValueError:
                valid = False
    elif valid and field == "description":
        valid = _safe_text(value, 16000)
    elif valid and field == "thumbnail_url":
        valid = re.fullmatch(
            r"https://couverture\.numilog\.com/[0-9]{1,32}_GRANDE\.jpg",
            value,
        ) is not None
    elif valid and field == "collection":
        valid = _safe_text(value, 2000)
    else:
        valid = False
    if not valid:
        raise ProviderCapabilityError("Numilog : public_edition_value_unverified")


def merge_attested_public_listing(owned, public_rows):
    """Return (new_owned_row, status), with no invented entitlement or edition.

    The public result must come from Numilog's structurally verified parser
    and have a product ID, canonical URL and safe source search URL all in
    agreement. Duplicates/contradictions fail closed rather than picking one.
    Only listing-level attributes are copied; ISBN, DRM, format and historic
    purchase price cannot be inferred.
    """
    # This helper can be invoked independently of the authenticated session.
    # Reassert the private-book boundary before even checking a public URL:
    # a public catalogue row or another provider must never acquire a
    # purchased-edition provenance marker through this merge.
    owned_metadata = getattr(owned, "metadata", None)
    if (getattr(owned, "source_provider", None) != "numilog"
            or getattr(owned, "source_collection", None) != "library"
            or getattr(owned, "source_credential", None) in (None, "", "__public__")
            or getattr(owned, "owned", None) is not True
            or getattr(owned, "downloadable", None) is not True
            or getattr(owned, "download_url", None) is not None
            or type(owned_metadata) is not dict
            or owned_metadata.get("public_url_evidence") != "numilog.library_title_link"):
        raise ProviderCapabilityError("Numilog : owned_edition_evidence_unverified")
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
                or getattr(public, "scenario", None) != "NUMILOG_PUBLIC"
                or getattr(public, "docbase", None) != "NUMILOG"
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

    # The account's direct product link and the public product ID agree, but
    # URL equality alone does not prove that a changed public listing still
    # describes the purchased title. A contradictory title must not enrich
    # authors, cover, publisher, synopsis or any public commercial offer.
    # Never compare the caller's cached title: 'owned' was freshly fetched.
    def normalized_title(value):
        if (type(value) is not str or not 0 < len(value.strip()) <= 512
                or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in value)):
            return None
        return unicodedata.normalize(
            "NFKC", " ".join(value.split()),
        ).casefold()

    if (normalized_title(getattr(owned, "title", None)) is None
            or normalized_title(getattr(public, "title", None)) is None
            or normalized_title(owned.title) != normalized_title(public.title)):
        raise ProviderCapabilityError("Numilog : public_edition_title_conflict")
    if evidence.get("title") != {
        "source_url": source,
        "source": "a.TitleBook",
        "kind": "observed_public_html",
    }:
        raise ProviderCapabilityError("Numilog : public_edition_evidence_conflict")

    # Never copy unverified fields or caller-supplied upstream URLs into the
    # private row. Each copied public field must have a matching HTML selector
    # attestation pointing to exactly the validated public search response.
    public_to_owned = {
        "author": ("authors", "author", "a.Author"),
        "publisher": ("publisher", "publisher", ".product_meta .LabelInfos[Éditeur]"),
        "published_at": ("published_at", "published_at", ".product_meta .LabelInfos[Parution]"),
        "description": ("summary", "description", "p.list-group-item-text"),
        "thumbnail_url": ("cover_url", "thumbnail_url", "img.lazy-image[data-src]"),
    }
    fields = {}
    copied_evidence = {}
    for public_field, (proof_key, owned_field, required_selector) in public_to_owned.items():
        value = getattr(public, public_field, None)
        proof = evidence.get(proof_key)
        if not value or getattr(owned, owned_field, None):
            continue
        if proof != {
            "source_url": source,
            "source": required_selector,
            "kind": "observed_public_html",
        }:
            raise ProviderCapabilityError("Numilog : public_edition_evidence_conflict")
        _qualified_public_listing_value(public, owned_field, value)
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
        price = meta["price"]
        if price_proof != {
            "source_url": source, "source": ".ColPrice",
            "kind": "observed_public_html",
        } or type(price) is not dict:
            raise ProviderCapabilityError("Numilog : public_edition_evidence_conflict")
        parts = price.get("raw_parts")
        if (type(parts) is not list or len(parts) != 3
                or any(type(v) is not str for v in parts)
                or re.fullmatch(r"[0-9]{1,8}", parts[0]) is None
                or re.fullmatch(r"[0-9]{2}", parts[1]) is None
                or parts[2] != "€"
                or price.get("amount") != f"{int(parts[0])}.{parts[1]}"
                or type(price.get("minor_units")) is not int
                or price["minor_units"] != int(parts[0]) * 100 + int(parts[1])
                or price.get("currency") != "EUR"
                or price.get("display_reconstructed") != f"{parts[0]},{parts[1]} €"
                or price.get("applies_to") != "displayed_card_offer"
                or price.get("tax_included", False) is not None
                or price.get("source_selectors") != {
                    "major": ".ColPrice .LblPrice",
                    "fraction": ".ColPrice .LblDecimal",
                    "currency": ".ColPrice .LblSeparatorPrice",
                }):
            raise ProviderCapabilityError("Numilog : public_edition_evidence_conflict")
        # No live purchase price: this remains commercial listing evidence.
        metadata["public_offer_price"] = {
            "amount": price["amount"], "minor_units": price["minor_units"],
            "currency": "EUR", "applies_to": "displayed_card_offer",
        }
    # Non-textual collection metadata is not evidence of "no collection":
    # a mutated cache result must fail closed, not silently bypass validation.
    if meta.get("collection") is not None:
        _qualified_public_listing_value(public, "collection", meta["collection"])
        proof = evidence.get("collection")
        if proof != {
            "source_url": source,
            "source": ".product_meta .LabelInfos[Collection]",
            "kind": "observed_public_html",
        }:
            raise ProviderCapabilityError("Numilog : public_edition_evidence_conflict")
        metadata["public_collection"] = meta["collection"]
    # The catalogue listing may aggregate PDF and EPUB offers. Never copy
    # public.format / ISBN / DRM into the owned edition from a listing match.
    return replace(owned, metadata=metadata, **fields), "attested_listing_match"


def merge_attested_owned_public_detail(owned, detail):
    """Safely enrich a fresh owned book from an attested public product.

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
            or getattr(owned, "source_credential", None) in (None, "", "__public__")
            or getattr(owned, "owned", None) is not True
            or getattr(owned, "downloadable", None) is not True
            or getattr(owned, "download_url", None) is not None
            or type(metadata) is not dict
            or metadata.get("public_url_evidence") not in {
                "numilog.library_title_link", "numilog.library_readzis_redirect",
            }):
        raise ProviderCapabilityError(error)
    listing = metadata.get("public_enrichment")
    if type(listing) is not dict or listing.get("product_url") != url:
        raise ProviderCapabilityError(error)
    listing_attested = (
        listing.get("status") == "attested_listing_match"
        and listing.get("scope") == "public_listing_only"
        and type(listing.get("field_evidence")) is dict
    )
    direct_title_link_attested = (
        set(listing) == {"status", "scope", "product_url", "field_evidence"}
        and listing.get("status") == "attested_owned_title_link"
        and listing.get("scope") == "owned_library_title_link_only"
        and listing.get("field_evidence") == {}
        and metadata.get("format_unverified") is True
    )
    readzis_attested = (
        set(listing) == {"status", "scope", "product_url", "field_evidence"}
        and listing.get("status") == "attested_owned_readzis_redirect"
        and listing.get("scope") == "owned_library_readzis_redirect_only"
        and listing.get("field_evidence") == {}
        and metadata.get("format_unverified") is True
        and metadata.get("public_url_evidence") == "numilog.library_readzis_redirect"
    )
    if (listing_attested or direct_title_link_attested) and (
            metadata.get("public_url_evidence") != "numilog.library_title_link"):
        raise ProviderCapabilityError(error)
    if not (listing_attested or direct_title_link_attested or readzis_attested):
        raise ProviderCapabilityError(error)
    if listing_attested:
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
    from cifereca_numilog_public_identifiers import (
        PublicIdentifierError, validated_public_identifiers,
    )
    from cifereca_numilog_public_detail import (
        _valid_text, PublicDetailError, validated_public_jsonld_fields,
    )
    try:
        # The same strict, copy-on-validation contract protects both the
        # anonymous catalogue and an explicitly rechecked purchased title.
        fields, evidence = validated_public_jsonld_fields(detail, url)
        typed_ids, typed_proof = validated_public_identifiers(detail, url)
    except (PublicDetailError, PublicIdentifierError):
        raise ProviderCapabilityError(error) from None

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
    listing_excerpt = None
    for source_field, destination in (
        ("authors", "author"), ("publisher", "publisher"),
        ("published_at", "published_at"), ("description", "description"),
    ):
        value = fields.get(source_field)
        if source_field == "authors" and isinstance(value, list):
            value = " ; ".join(value)
        if value is None:
            continue
        current = getattr(owned, destination, None)
        if current and comparable(current) != comparable(value):
            # The search listing exposes an excerpt, not a definitive full
            # description. Upgrade ONLY if the full Book description really
            # extends that exact, provenance-attested excerpt. Otherwise keep
            # the purchased row's existing text and surface both versions.
            listing_proof = listing["field_evidence"].get("description")
            may_upgrade = (
                listing_attested and source_field == "description"
                and type(current) is str and _valid_text(current, 16000)
                and type(listing_proof) is dict
                and listing_proof.get("kind") == "numilog.public_search_listing"
                and listing_proof.get("source_url") == listing["source_url"]
                and listing_proof.get("selector") == "p.list-group-item-text"
                and comparable(value).startswith(comparable(current))
                and len(comparable(value)) > len(comparable(current))
            )
            if may_upgrade:
                projection[destination] = value
                listing_excerpt = current
            else:
                conflicts.append(source_field)
                continue
        elif not current:
            projection[destination] = value
        else:
            continue
        copied_evidence[destination] = {
            "kind": "numilog.public_product_jsonld",
            "source_url": url, "field": source_field,
        }
    # Preserve EVERY validated public JSON-LD value and its per-field
    # provenance even when the listing projection wins or fields are not
    # supported on SearchResult. This is commercial metadata only: neither
    # a verified purchased format nor a proof of entitlement.
    next_metadata = dict(metadata)
    detail_summary = {
        "status": "attested_public_product",
        "scope": "public_detail_only",
        "product_url": url,
        "format_edition_proven": False,
        "rights_proven_by_public_detail": False,
        "field_evidence": copied_evidence,
        "listing_conflicts": tuple(conflicts),
        "public_jsonld": {
            "fields": dict(fields),
            "field_evidence": {key: dict(proof) for key, proof in evidence.items()},
            "edition_proven": False,
            "rights_proven": False,
        },
        # Public offers describe formats on the commercial product page, not
        # which variant this account bought. Keep typed ids isolated.
        "public_identifiers": typed_ids,
        "identifier_evidence": typed_proof,
    }
    if direct_title_link_attested or readzis_attested:
        detail_summary["association_evidence"] = metadata["public_url_evidence"]
        detail_summary["association_scope"] = "authenticated_owned_book_product"
    if listing_excerpt is not None:
        detail_summary["replaced_listing_excerpt"] = listing_excerpt
    if "language" in fields:
        detail_summary["language"] = fields["language"]
    if "isbn_edition_unspecified" in fields:
        detail_summary["isbn_edition_unspecified"] = fields["isbn_edition_unspecified"]
    from cifereca_numilog_public_metadata import validated_public_metadata, PublicMetadataError
    from cifereca_numilog_public_commercial import (
        validated_public_commercial, PublicCommercialError,
    )
    try:
        html_metadata = validated_public_metadata(detail, url)
        commercial = validated_public_commercial(detail, url)
    except (PublicMetadataError, PublicCommercialError):
        raise ProviderCapabilityError(error) from None
    if commercial is not None:
        # Observed public shelf price/availability/image are NOT evidence of
        # purchased EPUB/PDF format, title, artifact, right or historical price.
        detail_summary["public_commercial"] = commercial
    if html_metadata is not None:
        detail_summary["public_html_metadata"] = html_metadata
        next_metadata["public_product_metadata"] = dict(html_metadata["fields"])
    next_metadata["public_detail_enrichment"] = detail_summary
    return replace(owned, metadata=next_metadata, **projection)


def merge_attested_owned_direct_public_detail(owned, detail):
    """Project a public Book linked *directly* in a fresh private title card.

    A validated owned-library title link is already an explicit product-level
    association: it need not first appear in a public title search's first
    page. This never proves the purchased EPUB/PDF variant, paper/digital
    ISBN equality, entitlement or delivery. Only the caller which freshly
    reloaded the authenticated inventory may invoke this path.
    """
    error = "Numilog : public_detail_evidence_conflict"
    meta = getattr(owned, "metadata", None)
    url = getattr(owned, "friendly_url", None)
    if (type(meta) is not dict
            or meta.get("public_url_evidence") not in {
                "numilog.library_title_link", "numilog.library_readzis_redirect",
            }
            or meta.get("format_unverified") is not True
            or meta.get("public_enrichment") is not None
            or type(url) is not str
            or public_book_url(url) != url
            or getattr(owned, "source_provider", None) != "numilog"
            or getattr(owned, "source_collection", None) != "library"
            or getattr(owned, "source_credential", None) in (None, "", "__public__")
            or getattr(owned, "owned", None) is not True
            or getattr(owned, "downloadable", None) is not True
            or getattr(owned, "download_url", None) is not None):
        raise ProviderCapabilityError(error)

    # The private library attests only the product URL, not a search listing
    # or an owned edition format. These statements remain separate in Core.
    readzis = meta["public_url_evidence"] == "numilog.library_readzis_redirect"
    direct = {
        "status": ("attested_owned_readzis_redirect" if readzis
                   else "attested_owned_title_link"),
        "scope": ("owned_library_readzis_redirect_only" if readzis
                  else "owned_library_title_link_only"),
        "product_url": url,
        "field_evidence": {},
    }
    fresh = replace(owned, metadata={**meta, "public_enrichment": direct})
    return merge_attested_owned_public_detail(fresh, detail)
