from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class BookingPresentation:
    status_label: str
    rank_label: str


def booking_rank_label(rank: object) -> str:
    text = str(rank or "").strip()
    if not text:
        return ""
    try:
        value = int(text)
    except (TypeError, ValueError):
        return f"Position {text} en file"
    if value <= 0:
        return ""
    suffix = "er" if value == 1 else "e"
    return f"{value}{suffix} en file"


def _date_label(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return parsed.strftime("%d/%m/%Y")


_CANCELLED = {"cancelled", "canceled", "annule", "annulé", "annulee", "annulée"}
_EXPIRED = {"expired", "expire", "expiré", "expiree", "expirée"}
_READY = {"available", "ready", "pickup", "available_for_pickup", "disponible"}
_WAITING = {"active", "pending", "waiting", "queued", "reserved", "reservation", "en_attente"}


def booking_presentation(item: object) -> BookingPresentation:
    raw_state = str(getattr(item, "state", "") or "").strip().casefold()
    rank_label = booking_rank_label(getattr(item, "rank", None))
    available_until = _date_label(getattr(item, "available_until", None))
    availability_date = _date_label(getattr(item, "availability_date", None))

    if raw_state in _CANCELLED:
        status = "Annulée"
    elif raw_state in _EXPIRED:
        status = "Expirée"
    elif available_until:
        status = f"Disponible jusqu’au {available_until}"
    elif raw_state in _READY or availability_date:
        status = "Prête à retirer"
    elif raw_state in _WAITING or rank_label:
        status = "En attente"
        if rank_label:
            status += f" · {rank_label}"
    elif raw_state:
        status = "État à vérifier"
    else:
        status = "Réservation"

    return BookingPresentation(status, rank_label)
