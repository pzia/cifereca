from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence
import math
import re

_WS = re.compile(r"\s+")


def _text(value: object) -> str:
    return _WS.sub(" ", str(value or "").strip())


def _fold(value: object) -> str:
    return _text(value).casefold()


def _field(value: object, name: str, default: object = "") -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _values(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return (value,)
    if isinstance(value, Mapping):
        return tuple(value.values())
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError:
        return (value,)


def _names(value: object) -> tuple[str, ...]:
    out: list[str] = []
    for raw in _values(value):
        if isinstance(raw, Mapping):
            raw = raw.get("name") or raw.get("label") or ""
        for part in str(raw or "").split(";"):
            item = _text(part)
            if item and item not in out:
                out.append(item)
    return tuple(out)


def _tokens(value: object) -> tuple[str, ...]:
    out: list[str] = []
    for raw in _values(value):
        if isinstance(raw, Mapping):
            raw = raw.get("name") or raw.get("label") or raw.get("value") or ""
        for part in re.split(r"[,;|]", str(raw or "")):
            item = _fold(part)
            if item and item not in out:
                out.append(item)
    return tuple(out)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first(*values: object) -> str:
    for value in values:
        item = _text(value)
        if item:
            return item
    return ""


def _metadata(book: object) -> Mapping[str, Any]:
    return _mapping(_field(book, "metadata", {}))


def _search_metadata(book: object) -> Mapping[str, Any]:
    return _mapping(_metadata(book).get("search_result"))


def _meta_value(book: object, *names: str) -> object:
    metadata = _metadata(book)
    search = _search_metadata(book)
    for name in names:
        direct = _field(book, name, None)
        if direct not in (None, "", (), [], {}):
            return direct
        value = metadata.get(name)
        if value not in (None, "", (), [], {}):
            return value
        value = search.get(name)
        if value not in (None, "", (), [], {}):
            return value
    return ""


def _identifier_map(book: object) -> dict[str, str]:
    identifiers = _mapping(_field(book, "identifiers", {}))
    out = {str(key).strip().casefold(): _text(value) for key, value in identifiers.items() if _text(value)}
    provider = _first(out.get("provider"), _meta_value(book, "provider_id", "source_provider"))
    source_item = _first(out.get("source_item_id"), _meta_value(book, "item_id", "record_id"))
    if provider:
        out.setdefault("provider", provider)
    if source_item:
        out.setdefault("source_item_id", source_item)
    return out


def _authors(book: object) -> tuple[str, ...]:
    return _names(_field(book, "authors", ()) or _meta_value(book, "authors", "author"))


def _title(book: object) -> str:
    return _first(_field(book, "title", ""), _meta_value(book, "title"))


def _year(book: object) -> int | None:
    raw = _meta_value(book, "publication_year", "year", "published_year")
    try:
        year = int(str(raw).strip()[:4])
    except (TypeError, ValueError):
        return None
    return year if 1000 <= year <= 3000 else None


def _period(book: object) -> str:
    explicit = _fold(_meta_value(book, "period", "era"))
    if explicit:
        return explicit
    year = _year(book)
    return f"{year // 10 * 10}s" if year is not None else ""


def _length_bucket(book: object) -> str:
    explicit = _fold(_meta_value(book, "length", "length_class"))
    if explicit:
        return explicit
    raw = _meta_value(book, "page_count", "pages")
    try:
        pages = int(raw)
    except (TypeError, ValueError):
        return ""
    if pages < 180:
        return "short"
    if pages < 420:
        return "medium"
    return "long"


def _difficulty(book: object) -> int | None:
    raw = _meta_value(book, "difficulty", "reading_level", "level")
    if isinstance(raw, (int, float)):
        return max(0, min(100, int(raw)))
    label = _fold(raw)
    table = {"beginner": 20, "accessible": 25, "intermediate": 50, "advanced": 75, "expert": 90}
    return table.get(label)


def _availability(book: object) -> str:
    metadata = _metadata(book)
    search = _search_metadata(book)
    for source in (book if isinstance(book, Mapping) else {}, metadata, search):
        if isinstance(source, Mapping):
            if source.get("owned") is True or source.get("possessed") is True:
                return "owned"
            if source.get("free") is True:
                return "free"
            if source.get("borrowable") is True or source.get("available_to_borrow") is True:
                return "borrowable"
    explicit = _fold(_meta_value(book, "availability", "availability_state", "access"))
    aliases = {
        "owned": "owned", "possessed": "owned", "local": "owned",
        "borrowable": "borrowable", "loan": "borrowable", "available": "borrowable",
        "free": "free", "public-domain": "free", "public_domain": "free",
        "external": "external",
    }
    return aliases.get(explicit, "external")


def _mood(book: object) -> tuple[tuple[str, ...], bool]:
    tags = _tokens(_meta_value(book, "mood", "moods", "tone", "tones"))
    metadata = _metadata(book)
    search = _search_metadata(book)
    inferred = bool(metadata.get("mood_inferred") or metadata.get("tone_inferred") or search.get("mood_inferred") or search.get("tone_inferred"))
    qualified = metadata.get("mood_qualified", metadata.get("tone_qualified", search.get("mood_qualified", search.get("tone_qualified", True))))
    if qualified is False:
        return (), inferred
    return tags, inferred


@dataclass(frozen=True, slots=True)
class ConstellationBook:
    key: str
    title: str
    authors: tuple[str, ...] = ()
    identifiers: Mapping[str, str] = field(default_factory=dict)
    series: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()
    period: str = ""
    language: str = ""
    length: str = ""
    formats: tuple[str, ...] = ()
    moods: tuple[str, ...] = ()
    mood_inferred: bool = False
    publisher: str = ""
    difficulty: int | None = None
    availability: str = "external"
    sources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RelationReason:
    kind: str
    value: str
    weight: float


@dataclass(frozen=True, slots=True)
class Relation:
    left: str
    right: str
    strength: float
    reasons: tuple[RelationReason, ...]


@dataclass(frozen=True, slots=True)
class RankedBook:
    book: ConstellationBook
    score: float
    reasons: tuple[str, ...]
    availability: str
    inferred: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WalkStep:
    book: ConstellationBook
    from_previous: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Walk:
    variant: str
    steps: tuple[WalkStep, ...]
    score: float


def normalize_book(book: object) -> ConstellationBook:
    identifiers = _identifier_map(book)
    title = _title(book)
    authors = _authors(book)
    isbn = _text(identifiers.get("isbn"))
    provider = _fold(identifiers.get("provider"))
    source_item = _text(identifiers.get("source_item_id"))
    book_id = _text(_field(book, "book_id", ""))
    if isbn:
        key = f"isbn:{isbn}"
    elif provider and source_item:
        key = f"source:{provider}:{source_item}"
    elif book_id:
        key = f"book:{book_id}"
    elif title and authors:
        key = f"work:{_fold(title)}|{_fold(authors[0])}"
    else:
        key = f"opaque:{_fold(title)}|{id(book)}"

    series = _tokens(_meta_value(book, "series", "collection"))
    subjects = _tokens(_meta_value(book, "subjects", "subject", "themes", "tags"))
    language = _fold(_meta_value(book, "language", "lang"))
    formats = _tokens(_meta_value(book, "formats", "format", "media_type"))
    moods, mood_inferred = _mood(book)
    publisher = _fold(_meta_value(book, "publisher", "imprint"))
    source_labels = []
    if provider:
        source_labels.append(provider)
    for raw in _values(_meta_value(book, "sources", "source")):
        value = _fold(raw)
        if value and value not in source_labels:
            source_labels.append(value)
    return ConstellationBook(
        key=key,
        title=title,
        authors=authors,
        identifiers=identifiers,
        series=series,
        subjects=subjects,
        period=_period(book),
        language=language,
        length=_length_bucket(book),
        formats=formats,
        moods=moods,
        mood_inferred=mood_inferred,
        publisher=publisher,
        difficulty=_difficulty(book),
        availability=_availability(book),
        sources=tuple(source_labels),
    )


def _merge_books(left: ConstellationBook, right: ConstellationBook) -> ConstellationBook:
    availability_order = {"owned": 4, "borrowable": 3, "free": 2, "external": 1}
    availability = left.availability if availability_order[left.availability] >= availability_order[right.availability] else right.availability
    identifiers = dict(left.identifiers)
    for key, value in right.identifiers.items():
        identifiers.setdefault(key, value)
    return ConstellationBook(
        key=left.key,
        title=left.title or right.title,
        authors=tuple(dict.fromkeys(left.authors + right.authors)),
        identifiers=identifiers,
        series=tuple(dict.fromkeys(left.series + right.series)),
        subjects=tuple(dict.fromkeys(left.subjects + right.subjects)),
        period=left.period or right.period,
        language=left.language or right.language,
        length=left.length or right.length,
        formats=tuple(dict.fromkeys(left.formats + right.formats)),
        moods=tuple(dict.fromkeys(left.moods + right.moods)),
        mood_inferred=left.mood_inferred or right.mood_inferred,
        publisher=left.publisher or right.publisher,
        difficulty=left.difficulty if left.difficulty is not None else right.difficulty,
        availability=availability,
        sources=tuple(dict.fromkeys(left.sources + right.sources)),
    )


def deduplicate_books(books: Iterable[object]) -> tuple[ConstellationBook, ...]:
    by_key: dict[str, ConstellationBook] = {}
    fallback_index: dict[tuple[str, str], str] = {}
    for raw in books:
        book = normalize_book(raw)
        key = book.key
        if key.startswith("opaque:") and book.title and book.authors:
            fallback = (_fold(book.title), _fold(book.authors[0]))
            key = fallback_index.get(fallback, key)
            fallback_index.setdefault(fallback, key)
        elif book.title and book.authors:
            fallback_index.setdefault((_fold(book.title), _fold(book.authors[0])), key)
        current = by_key.get(key)
        by_key[key] = book if current is None else _merge_books(current, book)
    return tuple(sorted(by_key.values(), key=lambda item: item.key))


def _common(left: Sequence[str], right: Sequence[str]) -> tuple[str, ...]:
    right_set = set(right)
    return tuple(value for value in left if value in right_set)


def relation(left: ConstellationBook, right: ConstellationBook) -> Relation | None:
    if left.key == right.key:
        return None
    reasons: list[RelationReason] = []
    for author in _common(tuple(_fold(v) for v in left.authors), tuple(_fold(v) for v in right.authors))[:1]:
        reasons.append(RelationReason("author", author, 0.35))
    for series in _common(left.series, right.series)[:1]:
        reasons.append(RelationReason("series", series, 0.40))
    subjects = _common(left.subjects, right.subjects)[:3]
    for subject in subjects:
        reasons.append(RelationReason("subject", subject, 0.10))
    if left.period and left.period == right.period:
        reasons.append(RelationReason("period", left.period, 0.08))
    if left.language and left.language == right.language:
        reasons.append(RelationReason("language", left.language, 0.05))
    if left.length and left.length == right.length:
        reasons.append(RelationReason("length", left.length, 0.04))
    for fmt in _common(left.formats, right.formats)[:1]:
        reasons.append(RelationReason("format", fmt, 0.03))
    for mood in _common(left.moods, right.moods)[:1]:
        reasons.append(RelationReason("mood", mood, 0.12))
    if left.publisher and left.publisher == right.publisher:
        reasons.append(RelationReason("publisher", left.publisher, 0.05))
    if not reasons:
        return None
    strength = min(1.0, round(sum(item.weight for item in reasons), 4))
    return Relation(left.key, right.key, strength, tuple(reasons))


class ConstellationGraph:
    def __init__(self, books: Iterable[object]) -> None:
        normalized = deduplicate_books(books)
        self.books = {book.key: book for book in normalized}
        self._edges: dict[tuple[str, str], Relation] = {}
        keys = tuple(self.books)
        for index, left_key in enumerate(keys):
            for right_key in keys[index + 1:]:
                edge = relation(self.books[left_key], self.books[right_key])
                if edge is not None:
                    self._edges[(left_key, right_key)] = edge

    def edge(self, left: str, right: str) -> Relation | None:
        if left == right:
            return None
        key = (left, right) if left < right else (right, left)
        edge = self._edges.get(key)
        if edge is None:
            return None
        if edge.left == left:
            return edge
        return Relation(left, right, edge.strength, edge.reasons)

    def neighbors(self, key: str) -> tuple[Relation, ...]:
        out: list[Relation] = []
        for other in self.books:
            edge = self.edge(key, other)
            if edge is not None:
                out.append(edge)
        out.sort(key=lambda item: (-item.strength, item.right))
        return tuple(out)

    def _reason_labels(self, edge: Relation) -> tuple[str, ...]:
        return tuple(f"{reason.kind}:{reason.value}" for reason in edge.reasons)

    def rank(self, seed_key: str, *, mode: str = "comfort", exploration: int = 25, mood: str = "") -> tuple[RankedBook, ...]:
        seed = self.books[seed_key]
        x = max(0.0, min(1.0, float(exploration) / 100.0))
        mode = _fold(mode)
        target_mood = _fold(mood)
        ranked: list[RankedBook] = []
        for edge in self.neighbors(seed_key):
            book = self.books[edge.right]
            reasons = list(self._reason_labels(edge))
            inferred: list[str] = []
            if mode == "explorer":
                surprise = 1.0 - edge.strength
                relation_variety = min(1.0, len({r.kind for r in edge.reasons}) / 4.0)
                score = (1 - x) * edge.strength + x * (0.65 * surprise + 0.35 * relation_variety)
            elif mode == "project":
                shared_subjects = _common(seed.subjects, book.subjects)
                if not shared_subjects:
                    continue
                score = edge.strength + 0.18 * len(shared_subjects)
                if seed.difficulty is not None and book.difficulty is not None:
                    delta = book.difficulty - seed.difficulty
                    if delta >= 0:
                        score += max(0.0, 0.22 - min(delta, 40) / 200.0)
                        reasons.append(f"progression:+{delta}")
                    else:
                        score -= min(abs(delta) / 200.0, 0.20)
                reasons.append("project:shared-subject")
            elif mode == "mood":
                if not target_mood or target_mood not in book.moods:
                    continue
                score = 0.55 + 0.35 * edge.strength
                reasons.append(f"mood-target:{target_mood}")
                if book.mood_inferred:
                    inferred.append(f"mood:{target_mood}")
            else:
                score = edge.strength * (1.0 - 0.35 * x) + (1.0 - edge.strength) * (0.15 * x)
            ranked.append(RankedBook(book, round(score, 4), tuple(reasons), book.availability, tuple(inferred)))
        ranked.sort(key=lambda item: (-item.score, item.book.title.casefold(), item.book.key))
        return tuple(ranked)

    def _path_relations(self, path: Sequence[str]) -> tuple[Relation, ...] | None:
        rels: list[Relation] = []
        for left, right in zip(path, path[1:]):
            edge = self.edge(left, right)
            if edge is None:
                return None
            rels.append(edge)
        return tuple(rels)

    def _repeat_penalty(self, path: Sequence[str]) -> float:
        authors: list[str] = []
        series: list[str] = []
        penalty = 0.0
        for key in path:
            book = self.books[key]
            for author in (_fold(v) for v in book.authors):
                if author in authors:
                    penalty += 0.12
                authors.append(author)
            for value in book.series:
                if value in series:
                    penalty += 0.15
                series.append(value)
        return penalty

    def _all_paths(self, start: str, end: str, *, min_steps: int = 4, max_steps: int = 8) -> list[tuple[str, ...]]:
        found: list[tuple[str, ...]] = []
        stack: list[tuple[str, tuple[str, ...]]] = [(start, (start,))]
        while stack:
            current, path = stack.pop()
            if len(path) > max_steps:
                continue
            if current == end:
                if len(path) >= min_steps:
                    found.append(path)
                continue
            if len(path) == max_steps:
                continue
            neighbors = [edge.right for edge in self.neighbors(current) if edge.right not in path]
            for next_key in reversed(neighbors):
                stack.append((next_key, path + (next_key,)))
        return found

    def _walk_score(self, path: Sequence[str], variant: str) -> float:
        rels = self._path_relations(path) or ()
        if not rels:
            return -math.inf
        strengths = [edge.strength for edge in rels]
        avg = sum(strengths) / len(strengths)
        variety = len({reason.kind for edge in rels for reason in edge.reasons}) / 8.0
        penalty = self._repeat_penalty(path)
        if variant == "short":
            return 2.0 - len(path) / 8.0 + avg * 0.7 - penalty
        if variant == "surprising":
            return (1.0 - avg) * 0.65 + variety * 0.55 - penalty
        return avg * 0.75 + variety * 0.35 - penalty

    def promenade(self, start: str, end: str, *, variant: str = "balanced") -> Walk | None:
        variant = _fold(variant)
        if variant not in {"short", "balanced", "surprising"}:
            raise ValueError("variant must be short, balanced or surprising")
        paths = self._all_paths(start, end)
        if not paths:
            return None
        paths.sort(key=lambda path: (-self._walk_score(path, variant), len(path), path))
        path = paths[0]
        rels = self._path_relations(path) or ()
        steps = [WalkStep(self.books[path[0]])]
        for key, edge in zip(path[1:], rels):
            steps.append(WalkStep(self.books[key], self._reason_labels(edge)))
        return Walk(variant, tuple(steps), round(self._walk_score(path, variant), 4))

    def replace_step(self, walk: Walk, index: int, replacement_key: str) -> Walk:
        if index <= 0 or index >= len(walk.steps) - 1:
            raise ValueError("only an intermediate step can be replaced")
        if replacement_key not in self.books:
            raise KeyError(replacement_key)
        path = [step.book.key for step in walk.steps]
        if replacement_key in path:
            raise ValueError("replacement already belongs to the walk")
        previous_key = path[index - 1]
        next_key = path[index + 1]
        if self.edge(previous_key, replacement_key) is None or self.edge(replacement_key, next_key) is None:
            raise ValueError("replacement must connect both adjacent steps")
        path[index] = replacement_key
        rels = self._path_relations(path)
        if rels is None:
            raise ValueError("replacement disconnects the walk")
        steps = [WalkStep(self.books[path[0]])]
        for key, edge in zip(path[1:], rels):
            steps.append(WalkStep(self.books[key], self._reason_labels(edge)))
        return Walk(walk.variant, tuple(steps), round(self._walk_score(path, walk.variant), 4))


class ConstellationService:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, ConstellationGraph]] = {}

    def graph_for(self, identity_id: str, books: Iterable[object], *, freshness_token: str = "") -> ConstellationGraph:
        identity = _text(identity_id)
        token = _text(freshness_token)
        current = self._cache.get(identity)
        if current is not None and current[0] == token:
            return current[1]
        graph = ConstellationGraph(books)
        self._cache[identity] = (token, graph)
        return graph

    def invalidate(self, identity_id: str | None = None) -> None:
        if identity_id is None:
            self._cache.clear()
        else:
            self._cache.pop(_text(identity_id), None)


class ConstellationsPlugin:
    def __init__(self) -> None:
        self.service = ConstellationService()
        self.context: object | None = None

    def activate(self, context: object) -> None:
        self.context = context

    def deactivate(self) -> None:
        self.context = None

    def graph(self, identity_id: str, books: Iterable[object], *, freshness_token: str = "") -> ConstellationGraph:
        return self.service.graph_for(identity_id, books, freshness_token=freshness_token)

    def invalidate(self, identity_id: str | None = None) -> None:
        self.service.invalidate(identity_id)


PLUGIN = ConstellationsPlugin()
