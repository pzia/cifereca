from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
import re
import unicodedata
from typing import Any, Iterable, Mapping, Sequence

_STOPWORDS = {
    "a", "au", "aux", "avec", "ce", "ces", "dans", "de", "des", "du", "elle", "en", "et",
    "eux", "il", "je", "la", "le", "les", "leur", "lui", "ma", "mais", "me", "meme", "mes",
    "moi", "mon", "ne", "nos", "notre", "nous", "on", "ou", "par", "pas", "pour", "qu", "que",
    "qui", "sa", "se", "ses", "son", "sur", "ta", "te", "tes", "toi", "ton", "tu", "un", "une",
    "vos", "votre", "vous", "the", "and", "of", "to", "in", "for", "on", "with", "an", "is",
}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", _text(value)).encode("ascii", "ignore").decode("ascii")
    return " ".join(re.findall(r"[a-z0-9]+", raw.lower()))


def tokens(value: Any) -> set[str]:
    return {t for t in normalize(value).split() if len(t) >= 3 and t not in _STOPWORDS}


def get_field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def metadata(item: Any) -> Mapping[str, Any]:
    value = get_field(item, "metadata", {})
    return value if isinstance(value, Mapping) else {}


def _as_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        preferred = _text(value.get("value") or value.get("label") or value.get("name") or "")
        if preferred:
            return [preferred]
        return [_text(v) for v in value.values() if _text(v)]
    if isinstance(value, Iterable):
        out: list[str] = []
        for row in value:
            if isinstance(row, Mapping):
                out.append(_text(row.get("value") or row.get("label") or row.get("name") or ""))
            else:
                out.append(_text(row))
        return [x for x in out if x]
    return [_text(value)]


def field_values(item: Any, *names: str) -> list[str]:
    md = metadata(item)
    epub = md.get("epub_metadata") if isinstance(md.get("epub_metadata"), Mapping) else {}
    out: list[str] = []
    for name in names:
        out.extend(_as_values(get_field(item, name, None)))
        out.extend(_as_values(md.get(name)))
        out.extend(_as_values(epub.get(name)))
    return [x for x in out if _text(x)]


def author_values(item: Any) -> list[str]:
    values = field_values(item, "author", "authors", "creator", "creators")
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = normalize(value)
        if key and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def subject_values(item: Any) -> list[str]:
    return field_values(item, "subjects", "subject", "tags", "genres", "genre", "categories")


def series_values(item: Any) -> list[str]:
    return field_values(item, "series", "collection")


def _canonical_isbn(value: Any) -> str:
    raw = re.sub(r"[^0-9Xx]", "", _text(value)).upper()
    if len(raw) == 10 and re.fullmatch(r"[0-9]{9}[0-9X]", raw):
        total = sum((10 - index) * (10 if char == "X" else int(char)) for index, char in enumerate(raw))
        if total % 11:
            return ""
        prefix = "978" + raw[:9]
        check = (10 - sum(int(char) * (1 if index % 2 == 0 else 3) for index, char in enumerate(prefix)) % 10) % 10
        return prefix + str(check)
    if len(raw) == 13 and raw.isdigit():
        total = sum(int(char) * (1 if index % 2 == 0 else 3) for index, char in enumerate(raw))
        return raw if total % 10 == 0 else ""
    return ""


def _identifier_mappings(item: Any) -> tuple[Mapping[str, Any], ...]:
    out: list[Mapping[str, Any]] = []
    direct = get_field(item, "identifiers", {})
    if isinstance(direct, Mapping):
        out.append(direct)
    md = metadata(item)
    nested = md.get("identifiers")
    if isinstance(nested, Mapping):
        out.append(nested)
    epub = md.get("epub_metadata")
    if isinstance(epub, Mapping):
        nested = epub.get("identifiers")
        if isinstance(nested, Mapping):
            out.append(nested)
    return tuple(out)


def _identifier_scalar(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", raw)).casefold()


def identifier_values(item: Any) -> set[str]:
    """Return only strong, namespaced bibliographic identifiers.

    Provider names and source item ids are never authoritative alone. ISBNs are
    checksum-validated and canonicalised to ISBN-13. Other explicit identifier
    fields stay namespaced by their key so unrelated schemes cannot collide.
    """

    cleaned: set[str] = set()
    provider = _text(
        get_field(item, "source_provider", "")
        or get_field(item, "provider_id", "")
        or get_field(item, "provider", "")
    )
    source_item = _text(
        get_field(item, "source_item_id", "")
        or get_field(item, "item_id", "")
        or get_field(item, "record_id", "")
    )

    isbn_values = field_values(
        item,
        "isbn",
        "isbns",
        "isbn10",
        "isbn_10",
        "isbn13",
        "isbn_13",
    )
    for value in isbn_values:
        isbn = _canonical_isbn(value)
        if isbn:
            cleaned.add("isbn:" + isbn)

    ignored_mapping_keys = {
        "provider",
        "provider_id",
        "source_provider",
        "source",
        "source_id",
        "item_id",
        "source_item_id",
        "record_id",
        "id",
    }
    isbn_mapping_keys = {"isbn", "isbns", "isbn10", "isbn_10", "isbn13", "isbn_13"}

    for mapping in _identifier_mappings(item):
        provider = _text(
            mapping.get("provider")
            or mapping.get("provider_id")
            or mapping.get("source_provider")
            or provider
        )
        source_item = _text(
            mapping.get("source_item_id")
            or mapping.get("item_id")
            or mapping.get("record_id")
            or source_item
        )
        for raw_key, raw_value in mapping.items():
            key = normalize(raw_key).replace(" ", "_")
            if not key:
                continue
            values = _as_values(raw_value)
            if key in isbn_mapping_keys:
                for value in values:
                    isbn = _canonical_isbn(value)
                    if isbn:
                        cleaned.add("isbn:" + isbn)
                continue
            if key in ignored_mapping_keys:
                continue
            for value in values:
                scalar = _identifier_scalar(value)
                if scalar:
                    cleaned.add(f"{key}:{scalar}")

    provider_key = _identifier_scalar(provider)
    source_key = _identifier_scalar(source_item)
    if provider_key and source_key:
        cleaned.add(f"source:{provider_key}:{source_key}")
    return cleaned


def candidate_key(item: Any) -> str:
    ids = identifier_values(item)
    if ids:
        return "id:" + sorted(ids)[0]
    title = normalize(get_field(item, "title", ""))
    authors = sorted(normalize(a) for a in author_values(item) if normalize(a))
    return f"ta:{title}|{'/'.join(authors)}"


@dataclass(frozen=True)
class ProfileVector:
    authors: Counter[str]
    subjects: Counter[str]
    series: Counter[str]
    title_terms: Counter[str]
    description_terms: Counter[str]
    lexical_terms: Counter[str]
    author_labels: dict[str, str]
    series_labels: dict[str, str]
    max_author_count: int
    max_subject_count: int
    max_series_count: int
    max_lexical_count: int
    identifiers: frozenset[str]
    existing_pairs: frozenset[str]
    total_books: int


def build_profile(books: Sequence[Any]) -> ProfileVector:
    authors: Counter[str] = Counter()
    subjects: Counter[str] = Counter()
    series: Counter[str] = Counter()
    title_terms: Counter[str] = Counter()
    description_terms: Counter[str] = Counter()
    author_labels: dict[str, str] = {}
    series_labels: dict[str, str] = {}
    identifiers: set[str] = set()
    existing_pairs: set[str] = set()

    for book in books:
        for author in author_values(book):
            key = normalize(author)
            if key:
                authors[key] += 1
                author_labels.setdefault(key, author)
        for subject in subject_values(book):
            for token in tokens(subject):
                subjects[token] += 1
        for value in series_values(book):
            key = normalize(value)
            if key:
                series[key] += 1
                series_labels.setdefault(key, value)
        for token in tokens(get_field(book, "title", "")):
            title_terms[token] += 1
        for token in tokens(get_field(book, "description", "") or metadata(book).get("description")):
            description_terms[token] += 1
        identifiers.update(identifier_values(book))
        title = normalize(get_field(book, "title", ""))
        auth = "/".join(sorted(normalize(a) for a in author_values(book) if normalize(a)))
        if title and auth:
            existing_pairs.add(f"{title}|{auth}")

    lexical_terms = title_terms + description_terms
    return ProfileVector(
        authors=authors,
        subjects=subjects,
        series=series,
        title_terms=title_terms,
        description_terms=description_terms,
        lexical_terms=lexical_terms,
        author_labels=author_labels,
        series_labels=series_labels,
        max_author_count=max(authors.values(), default=1),
        max_subject_count=max(subjects.values(), default=1),
        max_series_count=max(series.values(), default=1),
        max_lexical_count=max(lexical_terms.values(), default=1),
        identifiers=frozenset(identifiers),
        existing_pairs=frozenset(existing_pairs),
        total_books=len(books),
    )


def is_already_present(profile: ProfileVector, item: Any) -> bool:
    ids = identifier_values(item)
    if ids:
        # Explicit identifiers are authoritative for edition-level ownership.
        # If the candidate carries identifiers and none match the library,
        # do not collapse a different edition merely because title/author match.
        return bool(profile.identifiers.intersection(ids))
    title = normalize(get_field(item, "title", ""))
    auth = "/".join(sorted(normalize(a) for a in author_values(item) if normalize(a)))
    return bool(title and auth and f"{title}|{auth}" in profile.existing_pairs)


def _weighted_exact(
    counter: Counter[str],
    values: Iterable[str],
    cap: float,
    *,
    max_count: int | None = None,
) -> tuple[float, list[str]]:
    if not counter:
        return 0.0, []
    max_count = max(1, int(max_count if max_count is not None else max(counter.values(), default=1)))
    raw = 0.0
    matched: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = normalize(value)
        if key in counter and key not in seen:
            seen.add(key)
            matched.append(value)
            raw += 0.65 + 0.35 * counter[key] / max_count
    return min(cap, cap * (1.0 - math.exp(-raw))), matched


def _weighted_tokens(
    counter: Counter[str],
    values: set[str],
    cap: float,
    *,
    max_count: int | None = None,
) -> tuple[float, list[str]]:
    if not counter or not values:
        return 0.0, []
    common = sorted(values.intersection(counter.keys()), key=lambda x: (-counter[x], x))
    if not common:
        return 0.0, []
    max_count = max(1, int(max_count if max_count is not None else max(counter.values(), default=1)))
    strength = sum(0.5 + 0.5 * counter[x] / max_count for x in common[:8])
    return min(cap, cap * (1.0 - math.exp(-strength / 2.0))), common


def content_score(profile: ProfileVector, item: Any) -> tuple[float, tuple[str, ...]]:
    if profile.total_books <= 0 or is_already_present(profile, item):
        return 0.0, ()
    return _content_score_prevalidated(profile, item)


def _content_score_prevalidated(
    profile: ProfileVector,
    item: Any,
) -> tuple[float, tuple[str, ...]]:
    """Score an item after the caller has already excluded library presence."""
    if profile.total_books <= 0:
        return 0.0, ()
    score = 0.0
    reasons: list[str] = []

    author_score, authors = _weighted_exact(
        profile.authors,
        author_values(item),
        42.0,
        max_count=profile.max_author_count,
    )
    score += author_score
    if authors:
        count = max(profile.authors[normalize(a)] for a in authors)
        reasons.append(f"auteur déjà présent ({count} livre{'s' if count > 1 else ''})")

    subject_tokens: set[str] = set()
    for value in subject_values(item):
        subject_tokens.update(tokens(value))
    subject_score, subjects = _weighted_tokens(
        profile.subjects,
        subject_tokens,
        27.0,
        max_count=profile.max_subject_count,
    )
    score += subject_score
    if subjects:
        reasons.append("thèmes proches : " + ", ".join(subjects[:3]))

    series_score, series = _weighted_exact(
        profile.series,
        series_values(item),
        16.0,
        max_count=profile.max_series_count,
    )
    score += series_score
    if series:
        reasons.append("série / collection proche")

    candidate_text = tokens(get_field(item, "title", ""))
    candidate_text.update(tokens(get_field(item, "description", "") or metadata(item).get("description")))
    lexical_score, lexical = _weighted_tokens(
        profile.lexical_terms,
        candidate_text,
        10.0,
        max_count=profile.max_lexical_count,
    )
    score += lexical_score
    if lexical and len(reasons) < 3:
        reasons.append("vocabulaire proche : " + ", ".join(lexical[:3]))

    # Small quality tiebreaker only: it cannot create a recommendation alone.
    if reasons:
        completeness = sum(bool(_text(get_field(item, name, ""))) for name in ("author", "description", "published_at", "format"))
        score += min(5.0, completeness * 1.25)
    return min(100.0, score), tuple(reasons[:3])


def build_queries(
    books: Sequence[Any],
    max_queries: int = 8,
    *,
    profile: ProfileVector | None = None,
) -> tuple[str, ...]:
    profile = profile if profile is not None else build_profile(books)

    queries: list[str] = []
    for key, _ in profile.authors.most_common(4):
        if key in profile.author_labels:
            queries.append(profile.author_labels[key])
    for key, _ in profile.series.most_common(2):
        if key in profile.series_labels:
            queries.append(profile.series_labels[key])
    queries.extend(term for term, _ in profile.subjects.most_common(4))
    if len(queries) < 3:
        terms = [term for term, _ in profile.title_terms.most_common(5)]
        if terms:
            queries.append(" ".join(terms[:3]))

    seen: set[str] = set()
    out: list[str] = []
    for query in queries:
        key = normalize(query)
        if key and key not in seen:
            seen.add(key)
            out.append(query)
        if len(out) >= max(1, int(max_queries)):
            break
    return tuple(out)


@dataclass(frozen=True)
class RankedCandidate:
    item: Any
    score: int
    reasons: tuple[str, ...]
    sources: tuple[str, ...]
    query_hits: int = 0


def _rrf_scores(batches: Sequence[Sequence[Any]], k: int = 60) -> tuple[dict[str, float], dict[str, int]]:
    raw: defaultdict[str, float] = defaultdict(float)
    hits: defaultdict[str, int] = defaultdict(int)
    for batch in batches:
        seen: set[str] = set()
        for rank, item in enumerate(batch, 1):
            key = candidate_key(item)
            if key in seen:
                continue
            seen.add(key)
            raw[key] += 1.0 / (k + rank)
            hits[key] += 1
    if not raw:
        return {}, {}
    maximum = max(raw.values()) or 1.0
    return {key: value / maximum * 100.0 for key, value in raw.items()}, dict(hits)


def _candidate_features(item: Any) -> tuple[set[str], set[str], set[str], set[str]]:
    authors = {normalize(x) for x in author_values(item) if normalize(x)}
    subjects: set[str] = set()
    for value in subject_values(item):
        subjects.update(tokens(value))
    title = tokens(get_field(item, "title", ""))
    series = {normalize(x) for x in series_values(item) if normalize(x)}
    return authors, subjects, title, series


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def candidate_similarity(left: Any, right: Any) -> float:
    la, ls, lt, lser = _candidate_features(left)
    ra, rs, rt, rser = _candidate_features(right)
    return min(1.0, 0.45 * _jaccard(la, ra) + 0.25 * _jaccard(ls, rs) + 0.20 * _jaccard(lt, rt) + 0.10 * _jaccard(lser, rser))


def _mmr(order: Sequence[RankedCandidate], exploration: int, limit: int) -> list[RankedCandidate]:
    if exploration <= 0 or len(order) <= 2:
        return list(order[:limit])
    remaining = list(order)
    selected: list[RankedCandidate] = []
    penalty = min(0.60, max(0.0, exploration / 100.0 * 0.60))
    while remaining and len(selected) < limit:
        if not selected:
            selected.append(remaining.pop(0))
            continue
        best_i = 0
        best_value = -1e9
        for index, candidate in enumerate(remaining):
            similarity = max(candidate_similarity(candidate.item, chosen.item) for chosen in selected)
            value = candidate.score / 100.0 - penalty * similarity
            if value > best_value:
                best_value = value
                best_i = index
        selected.append(remaining.pop(best_i))
    return selected


def rank_candidates(
    books: Sequence[Any],
    batches: Sequence[Sequence[Any]],
    *,
    limit: int = 120,
    exploration: int = 25,
    exclude_owned: bool = True,
) -> list[RankedCandidate]:
    profile = build_profile(books)
    rrf, query_hits = _rrf_scores(batches)
    merged: dict[str, tuple[Any, tuple[str, ...]]] = {}
    sources_by_key: defaultdict[str, list[str]] = defaultdict(list)

    for batch in batches:
        for item in batch:
            key = candidate_key(item)
            if exclude_owned and is_already_present(profile, item):
                continue
            source = _text(get_field(item, "source_provider", ""))
            if source and source not in sources_by_key[key]:
                sources_by_key[key].append(source)
            scorer = content_score if exclude_owned else _content_score_prevalidated
            cscore, reasons = scorer(profile, item)
            previous = merged.get(key)
            if previous is None or cscore > scorer(profile, previous[0])[0]:
                merged[key] = (item, reasons)

    ranked: list[RankedCandidate] = []
    scorer = content_score if exclude_owned else _content_score_prevalidated
    for key, (item, reasons) in merged.items():
        cscore, current_reasons = scorer(profile, item)
        rscore = rrf.get(key, 0.0)
        hits = query_hits.get(key, 0)
        # RRF can rescue sparse catalogue metadata, but not arbitrary zero-signal rows.
        if cscore <= 0.0 and hits < 2:
            continue
        final = 0.72 * cscore + 0.28 * rscore
        reason_list = list(current_reasons or reasons)
        if hits >= 2 and len(reason_list) < 3:
            reason_list.append(f"retrouvé par {hits} signaux du profil")
        if not reason_list:
            reason_list.append("plusieurs signaux concordants")
        ranked.append(RankedCandidate(
            item=item,
            score=max(1, min(100, int(round(final)))),
            reasons=tuple(reason_list[:3]),
            sources=tuple(sources_by_key.get(key, ())),
            query_hits=hits,
        ))

    ranked.sort(key=lambda row: (-row.score, normalize(get_field(row.item, "title", "")), normalize(get_field(row.item, "author", ""))))
    return _mmr(ranked, exploration=max(0, min(100, int(exploration))), limit=max(1, int(limit)))
