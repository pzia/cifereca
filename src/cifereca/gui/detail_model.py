"""Canonical neutral book-detail projection and pure display formatting."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from ..language import language_label
from ..book_detail_metadata import detail_metadata_items, safe_detail_value
from ..plugin_api import DetailField, DetailSection, DocumentLink, UiBookDetailPresentation


def fr_date(value: str | None, with_time: bool = False) -> str:
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%d/%m/%Y %H:%M" if with_time else "%d/%m/%Y")
    except Exception:
        return str(value)

def text_or_dash(value: Any) -> str:
    text = str(value or "").strip()
    return text or "—"

def detail_display_value(label: str, value: Any) -> tuple[str, str]:
    """Return the compact value and the complete tooltip value for a detail row."""
    full = text_or_dash(value)
    if label == "Fichier local" and full != "—":
        compact = Path(full).name or full
        return compact, full
    return full, full

def detail_language(metadata: object) -> str:
    """Return a compact, human-readable language list for shared book details."""
    data = metadata if isinstance(metadata, dict) else {}
    raw = data.get("languages") or data.get("language") or ()
    values = raw if isinstance(raw, (list, tuple, set)) else (raw,)
    labels: list[str] = []
    for value in values:
        label = language_label(value)
        if label and label not in labels:
            labels.append(label)
    return ", ".join(labels)

def compact_availability_label(value: object) -> str:
    """Normalize verbose provider prose into a short business status."""
    text = str(value or "").strip()
    folded = text.casefold()
    if not text:
        return ""
    if "non vérifi" in folded or "non verifi" in folded:
        return "Non vérifiée"
    if "dans votre bibliothèque" in folded or "dans votre bibliotheque" in folded:
        return "Dans la bibliothèque"
    if "téléchargement libre" in folded or "telechargement libre" in folded:
        return "Téléchargement libre"
    if "disponible à l’emprunt" in folded or "disponible a l'emprunt" in folded:
        return "Empruntable"
    if "disponible" in folded and len(text) > 32:
        return "Disponible"
    if len(text) > 40 and " — " in text:
        return text.split(" — ", 1)[0].strip()
    return text

def compact_detail_rows(rows: Iterable[tuple[str, object]]) -> tuple[tuple[str, object], ...]:
    """Drop empty filler cells from dense detail sections."""
    result: list[tuple[str, object]] = []
    for label, value in rows:
        text = str(value or "").strip()
        if not text or text in {"—", "Inconnu"}:
            continue
        result.append((str(label), value))
    return tuple(result)

def detail_subtitle_lines(section: str, value: object) -> int:
    """Keep short metadata dense while leaving long values fully readable."""
    text = str(value or "")
    if "\n" in text:
        return 0
    limit = 36 if section == "Essentiel" else 72
    if len(text) > limit:
        return 0
    return 1 if section == "Essentiel" else 2

def book_detail_presentation(
    *,
    title: str,
    subtitle: str | None = None,
    thumbnail: str | None = None,
    badges: Iterable[str] = (),
    group_title: str | None = None,
    rows: Iterable[tuple[str, object]] = (),
    section_rows: Iterable[tuple[str, Iterable[tuple[str, object]]]] = (),
    links: Iterable[DocumentLink] = (),
    metadata: object = None,
) -> UiBookDetailPresentation:
    """Build the neutral book-detail projection used by Core and plugin views."""
    sections_list: list[DetailSection] = []
    for order, (section_title, section_values) in enumerate(section_rows):
        compact_rows = compact_detail_rows(section_values)
        fields = tuple(
            DetailField(id=f"section-{order}-field-{index}", label=str(label), value=value)
            for index, (label, value) in enumerate(compact_rows)
        )
        if fields:
            sections_list.append(
                DetailSection(id=f"section-{order}", title=str(section_title), fields=fields, order=order)
            )
    if not sections_list and group_title:
        fields = tuple(
            DetailField(id=f"field-{index}", label=str(label), value=value)
            for index, (label, value) in enumerate(compact_detail_rows(rows))
        )
        if fields:
            sections_list.append(DetailSection(id="primary", title=group_title, fields=fields, order=0))
    details = list(detail_metadata_items(metadata))
    # Legacy summaries and loans without bibliographic metadata keep their
    # original shape: the generic helper must not fabricate a new section.
    if metadata is not None:
        primary_title = safe_detail_value(title)
        if primary_title and not any(label == "Titre" and value == primary_title
                                     for label, value, _kind in details):
            details.insert(0, ("Titre", primary_title, "title"))
        # Avoid merging known individual authors into a synthetic author field.
        if subtitle and not any(kind == "author" for _label, _value, kind in details):
            single_author = safe_detail_value(subtitle)
            if single_author:
                details.append(("Auteur", single_author, "author"))
    if details:
        # A summary row has no per-value copy/search controls. Keep the
        # complete actionable value in the collapsed detail even if its
        # concise counterpart appears in Essentiel or Bibliothèque.
        already_shown = {(field.label, str(field.value))
                         for section in sections_list if section.title == "Détail"
                         for field in section.fields}
        extra = tuple(
            DetailField(id=f"detail-{index}", label=label, value=value, kind=kind)
            for index, (label, value, kind) in enumerate(details)
            if (label, value) not in already_shown
        )
        if extra:
            sections_list.append(DetailSection(
                id="metadata-detail", title="Détail", fields=extra, order=1000,
            ))
    return UiBookDetailPresentation(
        title=str(title or "Sans titre"),
        subtitle=str(subtitle or ""),
        thumbnail_url=thumbnail,
        badges=tuple(dict.fromkeys(str(value) for value in badges if value)),
        sections=tuple(sections_list),
        links=tuple(links),
    )
