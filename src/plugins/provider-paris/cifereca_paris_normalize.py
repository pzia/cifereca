from __future__ import annotations
from cifereca.plugin_api import description_text, description_element
from cifereca_paris_formats import projection
from cifereca_paris_bibliography import project_observed_resource_facets
import html
import re
import uuid
from datetime import datetime, timezone
from typing import Any
from cifereca.plugin_api import Booking, HistoryItem, Holding, ItemDetail, Loan, SearchResult

DATE_RE = re.compile(r"^/Date\((-?\d+)(?:[+-]\d{4})?\)/$")
TAG_RE = re.compile(r"<[^>]+>")
IMG_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.I)


def microsoft_date_to_iso(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    m = DATE_RE.match(value)
    if not m:
        return None
    try:
        ms = int(m.group(1))
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return None


def strip_html(value: Any) -> str | None:
    if value is None:
        return None
    text = html.unescape(TAG_RE.sub(" ", str(value)))
    return re.sub(r"\s+", " ", text).strip() or None


def first_image_url(*values: Any) -> str | None:
    for value in values:
        if not value:
            continue
        m = IMG_RE.search(str(value))
        if m:
            return html.unescape(m.group(1))
    return None


def normalize_loan(raw: dict[str, Any], provider: str) -> Loan:
    additional = raw.get("AdditionalProperties") if isinstance(raw.get("AdditionalProperties"), dict) else {}
    ident = str(raw.get("Id") or raw.get("HoldingId") or f"{provider}:{raw.get('RecordId') or raw.get('Title') or uuid.uuid4()}")
    return Loan(
        id=ident,
        provider=provider,  # type: ignore[arg-type]
        medium="digital" if provider == "DILICOM" else "physical",
        record_id=str(raw["RecordId"]) if raw.get("RecordId") is not None else None,
        title=str(raw.get("Title") or "Sans titre"),
        thumbnail_url=raw.get("ThumbnailUrl") or raw.get("DefaultThumbnailUrl"),
        document_type=raw.get("TypeOfDocument"),
        due_at=microsoft_date_to_iso(raw.get("WhenBack")),
        library=raw.get("Location"),
        barcode=raw.get("Barcode") or additional.get("Barcode"),
        state=raw.get("State"),
        is_late=bool(raw.get("IsLate")),
        is_soon_late=bool(raw.get("IsSoonLate")),
        renewable=bool(raw.get("CanRenew")),
        cannot_renew_reason=raw.get("CannotRenewReason"),
        can_return=bool(additional.get("CanReturn")),
        access_medium=additional.get("AccessMedium"),
        has_been_renewed=bool(additional.get("HasBeenRenewed")),
        ebook_url=additional.get("EbookUrl"),
        raw=raw,
    )


def normalize_history(raw: dict[str, Any], provider: str) -> HistoryItem:
    additional = raw.get("AdditionalProperties") if isinstance(raw.get("AdditionalProperties"), dict) else {}
    borrowed = microsoft_date_to_iso(raw.get("LoanDate"))
    returned = microsoft_date_to_iso(raw.get("HandingDate"))
    stable = raw.get("HoldingId") or raw.get("RecordId") or raw.get("Title") or uuid.uuid4()
    return HistoryItem(
        id=f"{provider}:{stable}:{borrowed or returned or ''}",
        provider=provider,  # type: ignore[arg-type]
        medium="digital" if provider == "DILICOM" else "physical",
        record_id=str(raw["RecordId"]) if raw.get("RecordId") is not None else None,
        title=str(raw.get("Title") or "Sans titre"),
        author=str(additional.get("Auteur")) if additional.get("Auteur") is not None else None,
        year=str(additional.get("Annee")) if additional.get("Annee") is not None else None,
        # Keep Syracuse's generic document-type image separate so linked-account
        # history can try to resolve the real catalogue cover first.
        thumbnail_url=raw.get("ThumbnailUrl"),
        default_thumbnail_url=raw.get("DefaultThumbnailUrl"),
        document_type=raw.get("TypeOfDocument"),
        borrowed_at=borrowed,
        returned_at=returned,
    )


def normalize_booking(raw: dict[str, Any], provider: str) -> Booking:
    return Booking(
        id=str(raw.get("Id") or raw.get("HoldingId") or raw.get("RecordId") or uuid.uuid4()),
        provider=provider,  # type: ignore[arg-type]
        medium="digital" if provider == "DILICOM" else "physical",
        title=str(raw.get("Title") or "Sans titre"),
        record_id=str(raw["RecordId"]) if raw.get("RecordId") is not None else None,
        booking_date=microsoft_date_to_iso(raw.get("BookingDate")),
        availability_date=microsoft_date_to_iso(raw.get("AvailabilityDate")),
        available_until=microsoft_date_to_iso(raw.get("AvailableUntilDate")),
        location=raw.get("LocationLabel") or raw.get("Location"),
        rank=str(raw.get("Rank")) if raw.get("Rank") is not None else None,
        state=raw.get("State"),
        raw=raw,
    )


def normalize_search_result(raw: dict[str, Any], scenario: str) -> SearchResult | None:
    resource = raw.get("Resource")
    if not isinstance(resource, dict) or not resource.get("RscId") or not resource.get("RscBase"):
        return None
    docbase = str(resource["RscBase"])
    record_id = str(resource["RscId"])
    digital, content_type, media_format, metadata = projection(resource, raw, scenario, docbase)
    # Resource-level fields observed in Syracuse JSON: no additional GET or
    # implied server-side filtering, ownership, edition or reader-age claim.
    metadata.update(project_observed_resource_facets(resource, scenario))
    return SearchResult(
        id=f"{docbase}:{record_id}",
        record_id=record_id,
        docbase=docbase,
        medium="digital" if digital else "physical",
        title=str(resource.get("Ttl") or "Sans titre"),
        author=strip_html(resource.get("Crtr")),
        type=str(resource.get("Type")) if resource.get("Type") is not None else None,
        format=media_format,
        publisher=strip_html(resource.get("Pbls")),
        published_at=str(resource.get("Dt")) if resource.get("Dt") is not None else None,
        description=description_text(resource.get("Desc")),
        thumbnail_url=first_image_url(raw.get("CompactResult"), raw.get("CustomResult")),
        friendly_url=str(raw.get("FriendlyUrl")) if raw.get("FriendlyUrl") else None,
        scenario=scenario,
        content_type=content_type,
        metadata=metadata,
    )


def _holding_status(raw: dict[str, Any]) -> str:
    if raw.get("IsAvailable") and raw.get("IsLoanable") is not False:
        return "available"
    if not raw.get("IsAvailable") and raw.get("WhenBack"):
        return "borrowed"
    if raw.get("IsLoanable") is False:
        return "non-loanable"
    if raw.get("IsAvailable"):
        return "available"
    return "unavailable"


def normalize_holding(raw: dict[str, Any]) -> Holding:
    status = _holding_status(raw)
    labels = {"available": "Disponible", "borrowed": "Emprunté", "non-loanable": "Non empruntable", "unavailable": "Indisponible"}
    return Holding(
        id=str(raw.get("HoldingId") or raw.get("Barcode") or uuid.uuid4()),
        library=raw.get("HoldingPlace") or raw.get("Place") or raw.get("Site"),
        section=raw.get("Section"),
        location=raw.get("Localisation"),
        call_number=raw.get("Cote") or raw.get("AlternativeCote"),
        status=status,
        status_label=str(raw.get("Statut") or labels[status]),
        is_available=bool(raw.get("IsAvailable")),
        is_loanable=bool(raw.get("IsLoanable")),
        is_reservable=bool(raw.get("IsReservable")),
        reservations=int(raw.get("NbResa") or 0),
        due_at=microsoft_date_to_iso(raw.get("WhenBack")),
    )


def first_field(fields: dict[str, Any], key: str) -> str | None:
    value = fields.get(key)
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        for candidate in ("Value", "DisplayValue", "value"):
            if candidate in value:
                value = value[candidate]
                break
    return strip_html(value)


def normalize_item_detail(docbase: str, record_id: str, payload: dict[str, Any]) -> ItemDetail:
    d = payload.get("d") if isinstance(payload.get("d"), dict) else {}
    fields = d.get("fieldList") if isinstance(d.get("fieldList"), dict) else {}
    raw_holdings = d.get("Holdings") if isinstance(d.get("Holdings"), list) else []
    holdings = [normalize_holding(x) for x in raw_holdings if isinstance(x, dict)]
    item_holding = d.get("ItemHoldingsData") if isinstance(d.get("ItemHoldingsData"), dict) else {}
    display_holdings = holdings
    if docbase.upper() == "DILICOM":
        display_holdings = [h for h in holdings if h.library or h.location or h.call_number]
    basis = display_holdings or holdings
    if any(h.status == "available" for h in basis) or item_holding.get("Availability") == 1 or item_holding.get("CanLoan") is True:
        status, label = "available", item_holding.get("HoldingLabel") or "Disponible"
    elif any(h.status == "borrowed" for h in basis):
        status, label = "borrowed", item_holding.get("HoldingLabel") or "Emprunté"
    elif basis and all(h.status == "non-loanable" for h in basis):
        status, label = "non-loanable", item_holding.get("HoldingLabel") or "Non empruntable"
    else:
        status, label = "unavailable", item_holding.get("HoldingLabel") or "Indisponible"
    return ItemDetail(
        record_id=record_id,
        docbase=docbase,
        title=first_field(fields, "Title") or "Sans titre",
        author=first_field(fields, "Author_sort"),
        type=first_field(fields, "TypeOfDocument"),
        year=first_field(fields, "YearOfPublication"),
        thumbnail_url=first_field(fields, "ThumbMedium") or first_field(fields, "ThumbSmall"),
        availability_status=status,
        availability_label=str(label),
        holdings=display_holdings,
    )
