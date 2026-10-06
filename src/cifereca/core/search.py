from __future__ import annotations

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


def _matches_query(row: Mapping[str, Any], query: str, field: str) -> bool:
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


def _content_type(row: Mapping[str, Any]) -> str:
    explicit = str(row.get("content_type") or "").casefold()
    if explicit in {"ebook", "audiobook"}:
        return explicit
    media = str(row.get("media_format") or row.get("format") or "").casefold()
    kind = str(row.get("type") or "").casefold()
    audio_formats = {"mp3", "m4a", "ogg", "opus", "flac", "wav", "audiobook"}
    if media in audio_formats or "audio" in kind:
        return "audiobook"
    return "ebook"


def filter_loaded_results(
    rows: Iterable[Mapping[str, Any]],
    query: str = "",
    *,
    providers: Sequence[str] | None = None,
    content_types: Sequence[str] | None = None,
    languages: Sequence[str] | None = None,
    scope: str = "all",
    field: str = "all",
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
        medium = str(row.get("medium") or "digital").casefold()
        if wanted_scope == "physical" and medium != "physical":
            continue
        if wanted_scope == "digital" and medium == "physical":
            continue
        if query and not _matches_query(row, query, field):
            continue
        result.append(row)
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
    ) -> list[dict[str, Any]]:
        text = str(query or "").strip()
        if len(text) < 2:
            return []
        if str(scope or "all").strip().lower() == "physical":
            return []
        rows = self._entries(identity_id=identity_id, scan=False)
        return filter_loaded_results(rows, text, scope=scope, field=field)


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
                "medium": str(meta.get("medium") or "digital"),
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
            merged.values(), text, providers=providers, content_types=content_types, scope=scope, field=field,
        )


__all__ = ["LocalLibrarySearch", "KnownCatalogSearch", "filter_loaded_results"]
