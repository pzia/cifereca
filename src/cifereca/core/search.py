from __future__ import annotations

import re
import unicodedata
from typing import Any, Callable, Iterable, Mapping, Sequence


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _values(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        out: list[str] = []
        for child in value.values():
            out.extend(_values(child))
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        out = []
        for child in value:
            out.extend(_values(child))
        return out
    return [str(value)]


def _metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("metadata")
    return dict(value) if isinstance(value, Mapping) else {}


def _bundle_member_values(row: Mapping[str, Any], field: str = "all") -> list[str]:
    meta = _metadata(row)
    members = meta.get("bundle_members")
    if not isinstance(members, list):
        return []
    values: list[str] = []
    for member in members:
        if not isinstance(member, Mapping):
            continue
        member_meta = member.get("metadata") if isinstance(member.get("metadata"), Mapping) else {}
        if field in {"all", "title"}:
            values.extend(_values(member.get("title")))
        if field in {"all", "author"}:
            values.extend(_values(member.get("author")))
        if field in {"all", "subject"}:
            values.extend(_values(member_meta.get("subjects")))
        if field in {"all", "series"}:
            values.extend(_values(member_meta.get("series")))
        if field in {"all", "collection"}:
            values.extend(_values(member_meta.get("collection")))
        if field == "all":
            values.extend(_values(member.get("path")))
    return values


def _field_values(row: Mapping[str, Any], field: str) -> list[str]:
    field = str(field or "all").casefold()
    meta = _metadata(row)
    if field == "title":
        return _values(row.get("title")) + _bundle_member_values(row, field)
    if field == "author":
        return _values(row.get("author")) + _bundle_member_values(row, field)
    if field == "isbn":
        return _values(row.get("isbn")) + _values(row.get("item_id")) + _values(meta.get("isbn")) + _values(meta.get("isbns"))
    if field == "subject":
        return _values(meta.get("subjects")) + _values(meta.get("subject")) + _bundle_member_values(row, field)
    if field == "series":
        return _values(meta.get("series")) + _bundle_member_values(row, field)
    if field == "collection":
        return _values(meta.get("collection")) + _bundle_member_values(row, field)
    if field == "language":
        from cifereca.language import normalize_language_tag
        raw = _values(meta.get("languages")) + _values(meta.get("language"))
        return [tag for value in raw if (tag := normalize_language_tag(value))]
    values = [
        *_values(row.get("title")), *_values(row.get("author")), *_values(row.get("item_id")),
        *_values(row.get("provider_id")), *_values(row.get("media_format")),
        *_values(meta.get("subjects")), *_values(meta.get("subject")), *_values(meta.get("series")),
        *_values(meta.get("collection")), *_values(row.get("publisher")), *_values(meta.get("publisher")), *_values(meta.get("languages")),
        *_values(meta.get("isbn")), *_values(meta.get("isbns")), *_bundle_member_values(row, "all"),
    ]
    return values


# Common French grammatical words should not hide a book whose meaningful
# terms occur in different attested fields. An all-stopword query still uses
# its original tokens; otherwise "du" would match the entire catalogue.
_LOCAL_STOPWORDS = frozenset({
    "a", "au", "aux", "d", "de", "des", "du", "en", "et", "l", "la",
    "le", "les", "un", "une",
})


def _words(value: str) -> list[str]:
    return re.findall(r"[^\W_]+", _fold(value))


def _local_query_parts(query: str) -> tuple[list[str], list[str]]:
    # A quoted expression remains a contiguous, strict phrase within ONE
    # bibliographic field. It is never silently relaxed by stop-word removal.
    phrases = [" ".join(_words(text)) for text in re.findall(r'"([^"]+)"', query)]
    remainder = re.sub(r'"[^"]+"', " ", query)
    all_terms = _words(remainder)
    terms = [term for term in all_terms if term not in _LOCAL_STOPWORDS]
    return terms or (all_terms if not phrases else []), [p for p in phrases if p]


def local_search_evidence(row: Mapping[str, Any], query: str) -> tuple[int, tuple[str, ...]]:
    """Return a local, network-free relevance score and attested match fields.

    (0, ()) means no match. A result can match across title, awards, subjects
    and an already indexed summary, but never searches free-form credential,
    path, or token metadata. No fuzzy semantic/LLM claim is made.
    """
    if not isinstance(row, Mapping) or not isinstance(query, str):
        return 0, ()
    terms, phrases = _local_query_parts(query)
    if not terms and not phrases:
        return 0, ()
    meta = _metadata(row)
    search = meta.get("search_result")
    search = search if isinstance(search, Mapping) else {}
    groups = (
        ("title", 100, _values(row.get("title")) + _values(search.get("title"))
         + _bundle_member_values(row, "title")),
        ("series", 80, _values(meta.get("series")) + _values(search.get("series"))
         + _bundle_member_values(row, "series")),
        ("collection", 75, _values(meta.get("collection")) + _values(search.get("collection"))
         + _bundle_member_values(row, "collection")),
        ("author", 70, _values(row.get("author")) + _values(search.get("author"))
         + _bundle_member_values(row, "author")),
        ("subject", 50, _values(meta.get("subjects")) + _values(meta.get("subject"))
         + _values(meta.get("themes")) + _values(meta.get("keywords"))
         + _values(meta.get("tags")) + _values(meta.get("genres"))
         + _values(meta.get("categories")) + _values(search.get("subjects"))
         + _bundle_member_values(row, "subject")),
        ("award", 45, _values(meta.get("awards")) + _values(meta.get("prizes"))
         + _values(meta.get("literary_prizes")) + _values(search.get("awards"))),
        ("description", 30, _values(row.get("description"))
         + _values(meta.get("description")) + _values(meta.get("summary"))
         + _values(meta.get("synopsis")) + _values(search.get("description"))),
        ("publisher", 20, _values(row.get("publisher")) + _values(meta.get("publisher"))
         + _values(search.get("publisher"))),
        ("identifier", 10, _values(row.get("item_id")) + _values(row.get("isbn"))
         + _values(meta.get("isbn")) + _values(meta.get("isbns"))),
        ("language", 5, _values(meta.get("languages")) + _values(meta.get("language"))),
    )
    scored = [(name, weight, tuple(" ".join(_words(value)) for value in values
                                     if type(value) is str and value))
              for name, weight, values in groups]
    total = 0
    matched_fields: list[str] = []
    for token in [*terms, *phrases]:
        # A phrase must occur within one value; a word may match a value
        # in a different field from the previous word.
        matches = [(weight, name) for name, weight, values in scored
                   if any(token in value for value in values)]
        if not matches:
            return 0, ()
        weight, name = max(matches)
        total += weight
        if name not in matched_fields:
            matched_fields.append(name)
    return total, tuple(matched_fields)


_LOCAL_MATCH_LABELS = {
    "title": "Titre",
    "series": "Série",
    "collection": "Collection",
    "author": "Auteur",
    "subject": "Sujet",
    "award": "Prix littéraire",
    "description": "Résumé",
    "publisher": "Éditeur",
    "identifier": "Identifiant",
    "language": "Langue",
}


def local_match_explanation(row: Mapping[str, Any], query: str, *, field: str = "all") -> str:
    """Explain attested local matches; never claim a remote catalogue hit.

    Explicit field searches retain their strict semantics and cannot acquire
    an all-fields reason. The score remains available via local_search_evidence.
    """
    if (type(query) is not str or not query.strip()
            or str(field or "all").casefold() != "all"):
        return ""
    score, fields = local_search_evidence(row, query)
    if not score or not fields:
        return ""
    return "Correspondance : " + " · ".join(
        _LOCAL_MATCH_LABELS[name] for name in fields if name in _LOCAL_MATCH_LABELS
    )


def _matches_query(row: Mapping[str, Any], query: str, field: str) -> bool:
    # Explicit Author/Title/ISBN/... keep their old strict field semantics.
    if str(field or "all").casefold() == "all":
        return local_search_evidence(row, query)[0] > 0 if query.strip() else True
    tokens = [token for token in _fold(query).split() if token]
    if not tokens:
        return True
    folded = _fold(" ".join(_field_values(row, field)))
    return all(token in folded for token in tokens)


FILE_FORMATS = {'epub', 'kepub', 'pdf', 'mp3', 'm4a', 'ogg', 'opus', 'flac', 'wav'}

def file_format(value):
    raw = str(value or '').strip().casefold()
    mime = {'application/epub+zip':'epub', 'application/pdf':'pdf',
            'audio/mpeg':'mp3', 'audio/mp4':'m4a', 'audio/ogg':'ogg', 'audio/flac':'flac'}
    return raw if raw in FILE_FORMATS else mime.get(raw, '')


def format_family(value):
    normalized = file_format(value)
    return 'epub' if normalized == 'kepub' else normalized


def available_formats(row):
    """Explicit file formats only; bibliographic codes are never file formats.

    An observed local artifact is authoritative for its materialization.
    Remote records may advertise several alternatives.
    """
    meta = _metadata(row)
    if row.get('local') and file_format(row.get('media_format')):
        return (file_format(row['media_format']),)
    values = [row.get('media_format'), row.get('format')]
    for owner in (row, meta):
        for key in ('formats', 'available_formats', 'proposed_formats', 'audio_formats'):
            value = owner.get(key)
            values.extend(value if isinstance(value, (list, tuple)) else [value])
    out = []
    for value in values:
        if isinstance(value, Mapping):
            value = value.get('format') or value.get('media_format')
        normalized = file_format(value)
        if normalized and normalized not in out:
            out.append(normalized)
    return tuple(out)


def project_acquisition_offer_formats(
    row: Mapping[str, Any],
    offers: object,
) -> dict[str, Any]:
    """Add attested offer alternatives to filter metadata without choosing a format."""
    from cifereca.plugin_api import acquisition_offers_metadata

    projected = dict(row)
    meta = _metadata(row)
    offer_meta = acquisition_offers_metadata(offers)
    offered = tuple(offer_meta.get("available_formats") or ())
    if not offered:
        projected["metadata"] = meta
        return projected

    merged: list[str] = list(available_formats(row))
    for value in offered:
        normalized = file_format(value)
        if normalized and normalized not in merged:
            merged.append(normalized)
    meta["available_formats"] = merged
    meta["offer_format_source"] = "acquisition_offers"
    preferred = file_format(offer_meta.get("preferred_format"))
    if preferred and preferred in merged:
        meta["preferred_format"] = preferred
    projected["metadata"] = meta
    return projected


def support_kind(row: Mapping[str, Any]) -> str:
    """Return physical/digital only from explicit evidence; otherwise unknown."""
    raw = str(row.get("medium") or "").strip().casefold()
    if raw in {"physical", "digital"}:
        return raw
    meta = _metadata(row)
    declared = str(meta.get("medium") or meta.get("support") or "").strip().casefold()
    if declared in {"physical", "digital"}:
        return declared
    if row.get("local") or row.get("local_path") or file_format(row.get("media_format")) or file_format(row.get("format")):
        return "digital"
    explicit_type = str(row.get("content_type") or "").strip().casefold()
    if explicit_type in {"ebook", "audiobook"}:
        return "digital"
    return "unknown"


def _content_type(row: Mapping[str, Any]) -> str:
    explicit = str(row.get("content_type") or "").casefold()
    if explicit in {"ebook", "audiobook", "physical", "unknown"}:
        return explicit
    support = support_kind(row)
    if support == "physical":
        return "physical"
    media = str(row.get("media_format") or row.get("format") or "").casefold()
    kind = str(row.get("type") or "").casefold()
    audio_formats = {"mp3", "m4a", "ogg", "opus", "flac", "wav", "audiobook"}
    if media in audio_formats or "audio" in kind:
        return "audiobook"
    if file_format(media):
        return "ebook"
    return "unknown" if support == "unknown" else "ebook"


def filter_loaded_results(
    rows: Iterable[Mapping[str, Any]],
    query: str = "",
    *,
    providers: Sequence[str] | None = None,
    content_types: Sequence[str] | None = None,
    languages: Sequence[str] | None = None,
    scope: str = "all",
    field: str = "all",
    age: int | None = None,
    age_kind: str | None = None,
) -> list[dict[str, Any]]:
    """Pure in-memory filtering for UI state changes.

    It deliberately performs no provider call. The same filter state can later
    be supplied to an explicit online search, which is the only operation that
    may extend the loaded result set.
    """
    provider_set = {str(value).casefold() for value in (providers or ()) if str(value).strip() and str(value).casefold() != "all"}
    type_set = {str(value).casefold() for value in (content_types or ()) if str(value).strip() and str(value).casefold() != "all"}
    from cifereca.language import normalize_language_tag
    language_set = {tag for value in (languages or ()) if (tag := normalize_language_tag(value))}
    wanted_scope = str(scope or "all").casefold()
    result: list[dict[str, Any]] = []
    scored_rows: list[tuple[int, dict[str, Any]]] = []
    rank_local = bool(query and str(field or "all").casefold() == "all")
    for raw in rows:
        row = dict(raw)
        provider = str(row.get("provider_id") or row.get("source_provider") or "").casefold()
        if provider_set and provider not in provider_set:
            continue
        if type_set and _content_type(row) not in type_set and not ({format_family(v) for v in available_formats(row)} & {format_family(v) or v for v in type_set}):
            continue
        if language_set:
            row_languages = set(_field_values(row, "language"))
            if not row_languages.intersection(language_set):
                continue
        medium = support_kind(row)
        if wanted_scope in {"physical", "digital", "unknown"} and medium != wanted_scope:
            continue
        if age is not None:
            from .attested_age_filter import attested_age_status
            if attested_age_status(row, age, age_kind=age_kind) != "match":
                continue
        if rank_local:
            score, _fields = local_search_evidence(row, query)
            if not score:
                continue
            scored_rows.append((score, row))
        else:
            if query and not _matches_query(row, query, field):
                continue
            result.append(row)
    # Stable sort: equal-strength matches retain their existing source order.
    if rank_local:
        scored_rows.sort(key=lambda item: item[0], reverse=True)
        return [row for _score, row in scored_rows]
    return result


class LocalLibrarySearch:
    """Network-free search over cifereca's durable possession projection."""

    def __init__(self, entries: Callable[..., Iterable[dict[str, Any]]]) -> None:
        self._entries = entries

    def search(
        self,
        query: str,
        *,
        identity_id: str | None = None,
        scope: str = "all",
        field: str = "all",
        age: int | None = None,
        age_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        text = str(query or "").strip()
        if len(text) < 2:
            return []
        if str(scope or "all").strip().lower() == "physical":
            return []
        rows = self._entries(identity_id=identity_id, scan=False)
        return filter_loaded_results(
            rows, text, scope=scope, field=field, age=age, age_kind=age_kind,
        )


class KnownCatalogSearch:
    """Network-free search over all already-known SQLite catalogue facts.

    The source may include local materializations, active rights and mirrored
    remote catalogue rows. Duplicate parent items are collapsed before the UI
    sees them. Bundle members contribute searchable text but never become rows.
    """

    def __init__(
        self,
        catalog_entries: Callable[..., Iterable[dict[str, Any]]],
        library_entries: Callable[..., Iterable[dict[str, Any]]],
    ) -> None:
        self._catalog_entries = catalog_entries
        self._library_entries = library_entries

    def search(
        self,
        query: str,
        *,
        identity_id: str | None = None,
        scope: str = "all",
        field: str = "all",
        providers: Sequence[str] | None = None,
        content_types: Sequence[str] | None = None,
        age: int | None = None,
        age_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        text = str(query or "").strip()
        if len(text) < 2:
            return []
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        # Catalogue is the broad known set. Local/right rows below override it
        # because they carry stronger state and paths.
        for raw in self._catalog_entries():
            row = dict(raw)
            row_identity = str(row.get("identity_id") or "")
            if identity_id and row_identity not in {"", identity_id}:
                continue
            provider = str(row.get("provider_id") or "local")
            item_id = str(row.get("item_id") or row.get("artifact_id") or row.get("catalog_entry_id") or "")
            meta = _metadata(row)
            merged[(provider, item_id)] = {
                "key": f"known:{row.get('catalog_entry_id')}",
                "title": str(row.get("title") or "Sans titre"),
                "author": row.get("author"),
                "provider_id": provider,
                "identity_id": row.get("identity_id"),
                "item_id": item_id,
                "state": str(row.get("local_state") or "remote_catalog"),
                "local": row.get("local_state") == "local",
                "local_path": meta.get("path"),
                "media_format": row.get("media_format"),
                "medium": str(meta.get("medium") or "unknown"),
                "source": "known_catalog",
                "thumbnail_url": meta.get("cover_url") or meta.get("thumbnail_url"),
                "metadata": meta,
                "artifact_id": row.get("artifact_id"),
                "placement_id": row.get("placement_id"),
            }
        for raw in self._library_entries(identity_id=identity_id, scan=False):
            row = dict(raw)
            provider = str(row.get("provider_id") or "local")
            item_id = str(row.get("item_id") or row.get("artifact_id") or row.get("key") or "")
            key = (provider, item_id)
            previous = merged.get(key, {})
            merged[key] = {**previous, **row, "metadata": {**_metadata(previous), **_metadata(row)}}
        return filter_loaded_results(
            merged.values(), text, providers=providers, content_types=content_types,
            scope=scope, field=field, age=age, age_kind=age_kind,
        )


__all__ = ["LocalLibrarySearch", "KnownCatalogSearch", "filter_loaded_results", "local_search_evidence", "local_match_explanation", "available_formats", "project_acquisition_offer_formats", "support_kind"]
