from __future__ import annotations

from collections.abc import Iterable

from cifereca.core.search import file_format, format_family

from .models import AcquisitionOffer, AcquisitionOfferSet, AcquisitionPreferences


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if value and value not in out:
            out.append(value)
    return tuple(out)


def normalize_acquisition_offers(
    value: AcquisitionOfferSet | Iterable[AcquisitionOffer] | None,
) -> AcquisitionOfferSet:
    """Normalize legacy offer lists into the public choice-set contract.

    No format is inferred from source URL, bibliographic code, media type or
    metadata. Only AcquisitionOffer.format can attest a file format.
    """
    if value is None:
        result = AcquisitionOfferSet()
    elif isinstance(value, AcquisitionOfferSet):
        result = value
    else:
        if isinstance(value, (str, bytes)):
            raise TypeError("acquisition offers must be AcquisitionOffer values")
        result = AcquisitionOfferSet(offers=tuple(value))

    offers = tuple(result.offers)
    if any(not isinstance(offer, AcquisitionOffer) for offer in offers):
        raise TypeError("acquisition offers must contain AcquisitionOffer values")

    ids = [str(offer.offer_id or "").strip() for offer in offers]
    if any(not offer_id for offer_id in ids):
        raise ValueError("acquisition offer_id must be non-empty")
    if len(ids) != len(set(ids)):
        raise ValueError("acquisition offer_id values must be unique")

    available = attested_offer_formats(AcquisitionOfferSet(offers=offers))
    format_order: list[str] = []
    for raw in result.preferences.format_order:
        normalized = file_format(raw)
        if not normalized:
            raise ValueError(f"unknown acquisition format preference: {raw}")
        if normalized not in available:
            raise ValueError(f"format preference is not attested by an offer: {raw}")
        if normalized not in format_order:
            format_order.append(normalized)

    known_ids = set(ids)
    offer_order = _ordered_unique(result.preferences.offer_order)
    unknown_ids = [offer_id for offer_id in offer_order if offer_id not in known_ids]
    if unknown_ids:
        raise ValueError(f"unknown acquisition offer preference: {unknown_ids[0]}")

    return AcquisitionOfferSet(
        offers=offers,
        preferences=AcquisitionPreferences(
            format_order=tuple(format_order),
            offer_order=offer_order,
        ),
        metadata=dict(result.metadata),
    )


def attested_offer_formats(
    value: AcquisitionOfferSet | Iterable[AcquisitionOffer] | None,
    *,
    families: bool = False,
) -> tuple[str, ...]:
    """Return only formats explicitly attested by offers, in stable order."""
    if value is None:
        offers: tuple[AcquisitionOffer, ...] = ()
    elif isinstance(value, AcquisitionOfferSet):
        offers = tuple(value.offers)
    else:
        if isinstance(value, (str, bytes)):
            raise TypeError("acquisition offers must be AcquisitionOffer values")
        offers = tuple(value)

    out: list[str] = []
    for offer in offers:
        if not isinstance(offer, AcquisitionOffer):
            raise TypeError("acquisition offers must contain AcquisitionOffer values")
        normalized = file_format(offer.format)
        if families and normalized:
            normalized = format_family(normalized)
        if normalized and normalized not in out:
            out.append(normalized)
    return tuple(out)


def rank_acquisition_offers(
    value: AcquisitionOfferSet | Iterable[AcquisitionOffer] | None,
) -> tuple[AcquisitionOffer, ...]:
    """Rank by explicit offer, explicit format, then provider priority."""
    result = normalize_acquisition_offers(value)
    offer_order = {offer_id: index for index, offer_id in enumerate(result.preferences.offer_order)}
    format_order = {fmt: index for index, fmt in enumerate(result.preferences.format_order)}
    default_offer_rank = len(offer_order)
    default_format_rank = len(format_order)

    indexed = list(enumerate(result.offers))

    def score(entry):
        index, offer = entry
        offer_id = str(offer.offer_id or "").strip()
        fmt = file_format(offer.format)
        return (
            offer_order.get(offer_id, default_offer_rank),
            format_order.get(fmt, default_format_rank),
            -int(offer.priority or 0),
            index,
        )

    return tuple(offer for _index, offer in sorted(indexed, key=score))


def acquisition_format_projection(
    value: AcquisitionOfferSet | Iterable[AcquisitionOffer] | None,
) -> dict[str, object]:
    """Project attested and preferred formats for SearchResult metadata."""
    result = normalize_acquisition_offers(value)
    available = attested_offer_formats(result)
    preferred = tuple(result.preferences.format_order)
    return {
        "available_formats": available,
        "preferred_formats": preferred,
        "format_evidence": "acquisition_offers" if available else "unknown",
    }


__all__ = [
    "normalize_acquisition_offers",
    "attested_offer_formats",
    "rank_acquisition_offers",
    "acquisition_format_projection",
]
