"""#4276: bounded read-only projection of documentary facts for search UI.

Legacy search payloads remain supported, but must not override an explicitly
typed unknown/conflict. No rights, source references, provenance or assets are
combined by this module. Raw supplier codes are never returned to the UI.
"""
from __future__ import annotations

from collections.abc import Mapping

from cifereca.document_classification import (
    DOCUMENT_CLASSIFICATION_CONTRACT, ClassificationEvidence,
    DocumentClassification,
)

_EVIDENCE_AXES = ("medium", "document_kind", "material_format", "digital_format")


def public_document_classification(
    item: DocumentClassification | None,
) -> dict[str, object] | None:
    """Present bounded evidence and only closed codes, never raw upstream data."""
    if not isinstance(item, DocumentClassification):
        return None
    return {
        "contract": DOCUMENT_CLASSIFICATION_CONTRACT,
        **{
            key: {
                "value": getattr(item, key).value,
                "status": getattr(item, key).status,
            }
            for key in _EVIDENCE_AXES
        },
        "offered_file_formats": tuple(item.offered_file_formats),
    }


def classification_from_row(
    row: Mapping[str, object],
) -> DocumentClassification | None:
    """Decode a typed search projection; absent means a historical provider.

    None -> legacy provider: preserve its earlier filtering semantics.
    An *invalid but present* projection -> unknown, not an invitation to
    trust legacy medium/content_type/format on the same potentially
    contradictory notice.
    """
    value = row.get("document_classification")
    if value is None:
        return None
    if isinstance(value, DocumentClassification):
        return value
    if type(value) is not dict or value.get("contract") not in (
        DOCUMENT_CLASSIFICATION_CONTRACT, None,
    ):
        return DocumentClassification()
    if set(value) - {*_EVIDENCE_AXES, "contract", "offered_file_formats"}:
        return DocumentClassification()
    if set(_EVIDENCE_AXES) - set(value):
        return DocumentClassification()
    try:
        facts = {}
        for key in _EVIDENCE_AXES:
            raw = value[key]
            # Canonical public DTO has only value/status. Dataclass asdict()
            # can additionally carry source_key/raw_code: do NOT repeat those
            # values in rendered UI/diagnostics or decide from them.
            if type(raw) is not dict or set(raw) - {
                "value", "status", "source_key", "raw_code",
            } or not {"value", "status"} <= set(raw):
                return DocumentClassification()
            if type(raw["value"]) is not str or type(raw["status"]) is not str:
                return DocumentClassification()
            facts[key] = ClassificationEvidence(
                raw["value"], raw["status"],
            )
        offered = value.get("offered_file_formats", ())
        if type(offered) is list:
            offered = tuple(offered)
        return DocumentClassification(**facts, offered_file_formats=offered)
    except (ValueError, TypeError, KeyError):
        return DocumentClassification()


def classified_content_type(item: DocumentClassification) -> str:
    """Use positively attested kind+medium, never a search route."""
    if item.medium.status != "attested" or item.document_kind.status != "attested":
        return "unknown"
    if item.meta_type == "EBOOK":
        return "ebook"
    if item.meta_type == "AUDIOBOOK":
        return "audiobook"
    if item.meta_type == "PRINTED_BOOK":
        return "printed_book"
    return {
        "comics": "comics",
        "music": "music",
        "video": "video",
        "periodical": "periodical",
        "audio": "audio",
    }.get(item.document_kind.value, "unknown")


def matches_classified_type(item: DocumentClassification, selected: str) -> bool:
    """Closed type/format facet. Medium filtering remains a separate axis."""
    if selected == "all":
        return True
    if selected == "unknown":
        return classified_content_type(item) == "unknown"
    if selected in {"physical", "digital"}:
        return item.matches_filter(selected)
    if selected == "ebook":
        return item.matches_filter("ebook")
    if selected == "printed_book":
        return item.matches_filter("printed_book")
    if selected in {"audiobook", "comics", "music", "video", "periodical", "audio"}:
        return classified_content_type(item) == selected
    # An advertised alternative is not evidence of locally received bytes.
    if selected in {"epub", "kepub", "pdf", "mp3", "m4a"}:
        if item.medium.status != "attested" or item.medium.value != "digital":
            return False
        return (
            selected in item.offered_file_formats or
            (item.digital_format.status == "attested"
             and item.digital_format.value == selected)
        )
    return False


def loaded_document_facets(item, formats: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """Read the same support and file-format evidence used by loaded Search."""
    from .search import available_formats, format_family, support_kind
    from ..models import SearchResult

    if isinstance(item, SearchResult):
        row = {
            "document_classification": item.document_classification,
            "medium": item.medium, "format": item.format,
            "content_type": item.content_type, "metadata": item.metadata,
        }
    elif isinstance(item, Mapping):
        row = item
    else:
        return "unknown", ()
    classification = classification_from_row(row)
    if classification is None:
        families = {format_family(value) for value in available_formats(row)}
        return support_kind(row), tuple(value for value in formats if value in families)
    medium = classification.medium
    return (
        medium.value if medium.status == "attested" else "unknown",
        tuple(value for value in formats if matches_classified_type(classification, value)),
    )


def search_medium_label(item: DocumentClassification) -> str:
    """One closed label per document nature; unknown stays visibly unknown."""
    if item.medium.status != "attested":
        return "Support indéterminé"
    medium = item.medium.value
    if item.document_kind.status != "attested":
        return (
            "Support numérique · type de document indéterminé"
            if medium == "digital"
            else "Support physique · type de document indéterminé"
        )
    kind = item.document_kind.value
    if kind == "book":
        return "Livre numérique" if medium == "digital" else "Livre imprimé"
    if kind == "comics":
        return "Bande dessinée · numérique" if medium == "digital" else "Bande dessinée · physique"
    if kind in {"audio", "audiobook"}:
        return "Livre audio" if kind == "audiobook" else "Document audio"
    if kind == "music":
        return "Musique · numérique" if medium == "digital" else "Musique · physique"
    if kind == "video":
        return "Vidéo · numérique" if medium == "digital" else "Film / vidéo · physique"
    if kind == "periodical":
        return "Revue · numérique" if medium == "digital" else "Revue · physique"
    return "Support numérique" if medium == "digital" else "Support physique"


def filter_loaded_with_documentary_proofs(
    rows, query="", *, providers=(), content_types=(), languages=(),
    scope="all", field="all",
):
    """Filter a loaded UI set, preserving order and legacy provider behavior.

    Text, language and provider ranking stay with the existing Core search.
    A *present* typed classification takes precedence over legacy guessed
    medium/content_type/format. No extra provider access or rights inference.
    """
    from cifereca.core.search import filter_loaded_results

    base = filter_loaded_results(
        rows, query, providers=providers, languages=languages,
        content_types=(), scope="all", field=field,
    )
    wanted = str(scope or "all").casefold()
    selected = {
        value.casefold() for raw in (content_types or ())
        if type(raw) is str and (value := raw.strip()) and value.casefold() != "all"
    }
    out = []
    for row in base:
        classification = classification_from_row(row)
        if classification is None:
            # Historical providers keep precisely the existing filter rules.
            if filter_loaded_results(
                (row,), "", content_types=content_types, scope=scope,
            ):
                out.append(row)
            continue
        if wanted in {"digital", "physical"}:
            if not classification.matches_filter(wanted):
                continue
        elif wanted == "unknown":
            if classification.medium.status == "attested":
                continue
        elif wanted != "all":
            # Unknown scope must not select a new set of data.
            continue
        if selected and not any(
            matches_classified_type(classification, item) for item in selected
        ):
            continue
        out.append(row)
    return out
