"""Offline descriptive leads for the shared Kobo/Numilog/Paris case browser.

Title and author equality are NEVER supplier provenance, purchase ownership,
file lineage, a licence, a positive link, or a placement capability. This
private index contains only same-identity OPF metadata already scanned. It
performs no network, file read, SQLite query, cache write, or normalization of
paper ISBN to digital editions.
"""
from __future__ import annotations

from collections import defaultdict
import unicodedata


_UNINFORMATIVE = frozenset({
    "non renseigne", "inconnu", "sans titre", "untitled", "unknown", "none",
})


def _fold(value):
    """Conservative exact title/author comparison, never fuzzy substring."""
    if type(value) is not str or not 3 <= len(value) <= 256:
        return ""
    normalized = unicodedata.normalize("NFKD", value.casefold())
    words = "".join(
        character if character.isalnum() else " "
        for character in normalized if not unicodedata.combining(character)
    )
    result = " ".join(words.split())
    return result if len(result) >= 3 and result not in _UNINFORMATIVE else ""


def _authors(value):
    if type(value) is str:
        rows = [value]
    elif type(value) is list:
        rows = value[:12]
    else:
        rows = []
    return tuple(sorted({
        normalized for row in rows
        if (normalized := _fold(row))
    }))


def index_file_descriptions(
    files, file_states, *, identity_id: str,
    max_files: int = 10_000, max_per_key: int = 50,
):
    """Index only positive same-profile, unlinked file descriptions.

    This limit bounds construction and candidate display; a truncated index
    never conveys negative evidence. Callers must still expose manual file
    exploration in any coverage state.
    """
    result = defaultdict(list)
    if (type(identity_id) is not str
            or type(max_files) is not int or not 1 <= max_files <= 10_000
            or type(max_per_key) is not int or not 1 <= max_per_key <= 50):
        return {}
    for i, file in enumerate(files[:max_files]):
        states = file_states.get(i, set())
        if (file.identity_id != identity_id
                or type(file.sha256) is not str or len(file.sha256) != 64
                or file.validation != "ok"
                or file.media_format not in ("epub", "pdf")
                or "local_file_without_attested_right" not in states
                or "confirmed_link" in states
                or type(file.metadata) is not dict):
            continue
        title = _fold(file.metadata.get("title"))
        authors = _authors(file.metadata.get("authors"))
        if not title or not authors:
            continue
        for author in authors:
            key = (title, author)
            if len(result[key]) < max_per_key:
                result[key].append((i, file.media_format))
    return dict(result)


def descriptive_file_indices(
    index, *, title, authors, media_format=None,
    exclude=(), max_results: int = 20,
):
    """Bounded human-only hypotheses, exact folded title AND author.

    'match' only says the two descriptive strings agree: an EPUB can have
    those strings without belonging to this right or even this provider.
    """
    if type(max_results) is not int or not 1 <= max_results <= 20:
        return ()
    key_title = _fold(title)
    key_authors = _authors(authors)
    if not key_title or not key_authors:
        return ()
    allowed = None if media_format not in ("epub", "pdf") else media_format
    excluded = set(exclude)
    found = set()
    for author in key_authors:
        for i, fmt in index.get((key_title, author), ()):
            if i not in excluded and (allowed is None or fmt == allowed):
                found.add(i)
    return tuple(sorted(found)[:max_results])
