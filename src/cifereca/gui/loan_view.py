from __future__ import annotations

from collections.abc import Iterable


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
