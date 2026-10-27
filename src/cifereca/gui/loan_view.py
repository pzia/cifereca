from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime


LOAN_FILTER_LABELS = {
    "all": "Tous",
    "physical": "Physiques",
    "digital": "Numériques",
}

_LOAN_SCOPE_NOUNS = {
    "physical": "physiques",
    "digital": "numériques",
}


def loan_medium_counts(loans: Iterable[object]) -> dict[str, int]:
    rows = list(loans)
    physical = sum(str(getattr(row, "medium", "") or "") == "physical" for row in rows)
    digital = sum(str(getattr(row, "medium", "") or "") == "digital" for row in rows)
    return {
        "all": len(rows),
        "physical": physical,
        "digital": digital,
    }


def loan_context_label(
    *,
    filter_key: str,
    visible_count: int,
    medium_count: int,
    total_count: int,
    language_filtered: bool,
) -> str:
    visible = int(visible_count)
    medium = int(medium_count)
    total = int(total_count)
    noun = _LOAN_SCOPE_NOUNS.get(str(filter_key), "")

    if filter_key == "all":
        if language_filtered:
            return f"{visible} affiché(s) · {total} prêts"
        return f"{total} prêts"

    scope = f"{medium} {noun}"
    if language_filtered:
        return f"{visible} affiché(s) · {scope} · {total} prêts"
    return f"{scope} · {total} prêts"


@dataclass(frozen=True)
class LoanDuePresentation:
    label: str
    style: str
    sort_key: tuple[int, int]


def loan_row_metadata(
    due_at: str | None, due: LoanDuePresentation,
    *, document_type: str | None, provider: str,
) -> str:
    """Keep unknown/invalid due dates in the badge, never as 'Retour —'."""
    document = str(document_type or provider)
    if due.label in {"Échéance inconnue", "En retard"}:
        return document
    from .detail_model import fr_date
    return f"Retour {fr_date(due_at)} · {document}"


def loan_due_presentation(
    due_at: str | None,
    *,
    is_late: bool = False,
    today: date | None = None,
) -> LoanDuePresentation:
    current_day = today or date.today()
    due_day = None
    if due_at:
        try:
            parsed = datetime.fromisoformat(str(due_at).replace("Z", "+00:00"))
            due_day = parsed.astimezone().date() if parsed.tzinfo is not None else parsed.date()
        except (TypeError, ValueError):
            due_day = None

    if due_day is None:
        if is_late:
            return LoanDuePresentation("En retard", "loan-overdue", (0, 0))
        return LoanDuePresentation("Échéance inconnue", "loan-normal", (5, 0))

    delta = (due_day - current_day).days
    if delta < 0:
        days = abs(delta)
        unit = "jour" if days == 1 else "jours"
        return LoanDuePresentation(
            f"En retard de {days} {unit}",
            "loan-overdue",
            (0, delta),
        )
    if delta == 0:
        return LoanDuePresentation("Retour aujourd’hui", "loan-imminent", (1, 0))
    if delta == 1:
        return LoanDuePresentation("Retour demain", "loan-imminent", (2, 1))
    style = "loan-imminent" if delta <= 3 else "loan-normal"
    return LoanDuePresentation(f"Retour dans {delta} jours", style, (3, delta))



def loan_due_detail_value(due_at: str | None, *, is_late: bool = False) -> str:
    """Show a real parsed return date or an explicit unknown/late state.

    Details and list rows must agree. An invalid supplier date or missing
    field is never echoed as a return date or silently turned into today's
    date. The provider's independent is_late flag may still say late without
    disclosing a due date.
    """
    due = loan_due_presentation(due_at, is_late=is_late)
    if due.label in ("Échéance inconnue", "En retard"):
        return due.label
    from .detail_model import fr_date
    return fr_date(due_at)
