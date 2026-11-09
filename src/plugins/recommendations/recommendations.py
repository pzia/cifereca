from __future__ import annotations

import logging
import re
import time
from ipaddress import ip_address
from itertools import islice
from typing import Any, Mapping
from unicodedata import category
from urllib.parse import unquote, urlsplit

from cifereca.plugin_api import (
    Cancelled,
    DetailField,
    DetailSection,
    UiActionContribution,
    UiBookDetailAction,
    UiBookDetailPresentation,
    UiBookListEntry,
    UiPageContribution,
    normalize_language_tag,
)
from recommendation_llm import explain_reasons

from recommendation_engine import CatalogNoticeVariant, RankedCandidate, candidate_key, get_field, identifier_values, normalize
from recommendation_service import CatalogueSearchObservation, LibrarySnapshotCache, RecommendationPartial, RecommendationResult, compute_recommendations
from reference_recommendation import compute_reference_recommendations


_LOG = logging.getLogger("cifereca.plugin.recommendations")

_AFFINITY_MIN_TOOLTIP = (
    "Seuil minimal d’affinité : une valeur faible affiche plus de résultats, "
    "une valeur forte ne garde que les recommandations les plus proches de votre bibliothèque."
)
_DIVERSITY_TOOLTIP = (
    "Diversité : 0 privilégie les recommandations les plus proches ; "
    "une valeur élevée varie davantage auteurs, sujets et sources."
)

def _public_candidate_text(value: Any, *, limit: int = 180) -> str:
    """Project provider scalars without serializing private cached objects.

    Recommendation candidates can outlive their provider. Object reprs,
    mapping internals and control/bidi characters are not public metadata.
    """
    if type(value) is not str:
        return ""
    cleaned = value.strip()
    if (
        not cleaned or len(cleaned) > limit
        or any(category(char) in {"Cc", "Cf", "Cs"} for char in cleaned)
    ):
        return ""
    return cleaned


def _public_affinity_score(value: Any) -> int | None:
    """A displayed affinity must be the engine's bounded integer, not repr()."""
    return value if type(value) is int and 0 <= value <= 100 else None


def _public_ranked_values(raw: Any, *, limit: int = 32) -> tuple[str, ...]:
    """Bound public rank explanations and provider provenance.

    A restored/partial ranking can contain malformed list members or a
    mapping where a tuple of reasons/sources is expected. Never iterate
    field names as bibliographic facts or coerce objects into GTK labels.
    """
    if type(raw) is str:
        rows = (raw,)
    elif type(raw) in (list, tuple):
        rows = raw[:limit]
    else:
        return ()
    return tuple(
        cleaned for value in rows
        if (cleaned := _public_candidate_text(value, limit=240))
    )


def _catalogue_coverage_projection(
    result: RecommendationResult | None,
) -> tuple[str, str]:
    """Public status of discovered / responding / ranked catalogues.

    The Source filter remains strictly ranked-only. A supplier returning no
    ranked candidate must not be called disabled, nor silently disappear from
    the separate status. Never display raw error messages or user query text.
    """
    if type(result) is not RecommendationResult:
        return "", ""
    raw_providers = result.providers
    if type(raw_providers) not in (tuple, list):
        return "", ""
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in raw_providers[:128]:
        if type(raw) not in (tuple, list) or len(raw) != 2:
            continue
        provider_id = _public_candidate_text(raw[0], limit=120)
        # A source ID is an exact broker key. Trimming controls/whitespace
        # into another provider ID would attribute a foreign status to it.
        if not provider_id or provider_id != raw[0] or provider_id in seen:
            continue
        seen.add(provider_id)
        label = _public_candidate_text(raw[1], limit=240) or provider_id
        entries.append((provider_id, label))
    if not entries:
        return "Aucun catalogue disponible", ""
    count = len(entries)
    if not result.queries:
        return f"{count} catalogue(s) disponibles · aucun signal de recherche", ""

    observations: dict[str, CatalogueSearchObservation] = {}
    raw_observations = result.catalogue_observations
    if type(raw_observations) in (tuple, list):
        for value in raw_observations[:128]:
            if (type(value) is CatalogueSearchObservation
                    and value.provider_id in seen
                    and value.provider_id not in observations
                    and all(type(number) is int and 0 <= number <= 1000000
                            for number in (
                                value.batches, value.results_seen, value.failures,
                            ))):
                observations[value.provider_id] = value
    if not observations:
        return f"{count} catalogue(s) disponibles · réponses individuelles non détaillées", ""

    ranked_sources: set[str] = set()
    if type(result.ranked) in (tuple, list):
        for ranked in result.ranked[:4096]:
            try:
                raw_sources = ranked.sources
            except Exception:
                continue
            ranked_sources.update(_public_ranked_values(raw_sources))

    answered = empty = errors = not_ranked = no_response = 0
    visible = set()
    details: list[str] = []
    for provider_id, label in entries:
        observation = observations.get(provider_id)
        if observation is None or not observation.batches:
            state = "aucun lot reçu"
            no_response += 1
        else:
            answered += 1
            if observation.failures:
                errors += 1
            if observation.results_seen == 0 and not observation.failures:
                empty += 1
            if observation.results_seen and provider_id not in ranked_sources:
                not_ranked += 1
            if provider_id in ranked_sources:
                visible.add(provider_id)
            if observation.failures:
                state = ("réponse partielle en erreur" if observation.results_seen
                         else "erreur fournisseur")
            elif not observation.results_seen:
                state = "réponse sans notice"
            elif provider_id not in ranked_sources:
                state = "notices reçues, non retenues dans le classement"
            else:
                state = "notices présentes dans le classement"
        if len(details) < 12:
            details.append(f"{label} : {state}")

    summary = (
        f"{count} catalogue(s) disponibles · {answered} avec réponse · "
        f"{len(visible)} retenu(s) · {not_ranked} hors classement · "
        f"{empty} sans notice · {errors} en erreur · {no_response} sans lot"
    )
    if len(entries) > len(details):
        details.append(f"{len(entries) - len(details)} autre(s) catalogue(s)")
    return summary, "\n".join(details)


def _candidate_public_field(item: Any, name: str, *, limit: int = 180) -> str:
    try:
        raw = get_field(item, name, None)
    except Exception:
        return ""
    return _public_candidate_text(raw, limit=limit)


def _public_candidate_description(item: Any) -> str:
    # Invalid primary provider description must not hide an independently
    # safe summary, nor stringify malformed metadata into the public detail.
    return (
        _candidate_public_field(item, "description", limit=8000)
        or _candidate_public_field(item, "summary", limit=8000)
    )



def _public_candidate_authors(item: Any) -> tuple[str, ...]:
    """Only attested, bounded author strings reach the public host detail.

    The recommendation ranking engine accepts a broader legacy data model;
    its text coercion must not be repeated at the UI boundary, where a cache
    can contain arbitrary objects, malformed mappings or private values.
    """
    def read(container: Any, key: str) -> Any:
        try:
            return get_field(container, key, None)
        except Exception:
            return None

    metadata = read(item, "metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    epub = read(metadata, "epub_metadata")
    epub = epub if isinstance(epub, Mapping) else {}
    values: list[str] = []
    seen: set[str] = set()
    for field_name in ("author", "authors", "creator", "creators"):
        for source in (item, metadata, epub):
            raw = read(source, field_name)
            if type(raw) is str:
                rows = (raw,)
            elif type(raw) in (list, tuple):
                rows = raw[:32]
            elif isinstance(raw, Mapping):
                rows = (raw,)
            else:
                continue
            for row in rows:
                if isinstance(row, Mapping):
                    # An invalid first mapping value must not mask a valid
                    # independently attested label or name.
                    row = next((
                        safe for key in ("value", "label", "name")
                        if (safe := _public_candidate_text(read(row, key)))
                    ), "")
                author = _public_candidate_text(row)
                if not author:
                    continue
                identity = normalize(author)
                if identity and identity not in seen:
                    seen.add(identity)
                    values.append(author)
                    if len(values) == 32:
                        return tuple(values)
    return tuple(values)


def _local_recommendation_field_values(item: Any, field: str) -> tuple[str, ...]:
    """Read only bounded, already-loaded public facts for Search's field filter.

    This is a *view filter*, not a recommendation signal or a permission.
    Unknown field, malformed metadata, opaque objects, provider exceptions
    and unconsolidated provenance never select a book by accident.
    """
    if field == "title":
        title = _candidate_public_field(item, "title", limit=2048)
        return (title,) if title else ()
    if field == "author":
        return _public_candidate_authors(item)
    if field == "isbn":
        # The engine verifies checksums and namespaces identifiers. Provider
        # record IDs are NOT ISBNs, even when numeric.
        try:
            return tuple(
                value[5:] for value in sorted(identifier_values(item))
                if value.startswith("isbn:")
            )[:32]
        except Exception:
            return ()
    if field == "language":
        return _candidate_languages(item)

    names = {
        "subject": (
            "subjects", "subject", "themes", "keywords", "tags",
            "genres", "genre", "categories",
        ),
        "series": ("series",),
        "collection": ("collection",),
    }.get(field)
    if names is None:
        return ()
    try:
        metadata = get_field(item, "metadata", None)
    except Exception:
        metadata = None
    metadata = metadata if isinstance(metadata, Mapping) else {}
    try:
        epub = get_field(metadata, "epub_metadata", None)
    except Exception:
        epub = None
    epub = epub if isinstance(epub, Mapping) else {}
    scopes = (item, metadata, epub)
    observed: list[str] = []
    for name in names:
        for scope in scopes:
            try:
                raw = get_field(scope, name, None)
            except Exception:
                continue
            if type(raw) is str or isinstance(raw, Mapping):
                rows = (raw,)
            elif type(raw) in (list, tuple):
                rows = raw[:32]
            else:
                continue
            for row in rows:
                if isinstance(row, Mapping):
                    # Named bibliographic subjects, not arbitrary keys or
                    # serialized provider payloads.
                    row = next((
                        value for key in ("value", "label", "name")
                        if (value := _candidate_mapping_text(row, key))
                    ), "")
                text = _public_candidate_text(row, limit=2048)
                if text:
                    observed.append(text)
                    if len(observed) == 128:
                        return tuple(observed)
    return tuple(observed)


def _candidate_mapping_text(row: Mapping[str, Any], key: str) -> str:
    try:
        return _public_candidate_text(row.get(key), limit=2048)
    except Exception:
        return ""


def _reference_isbn(book: Any) -> str:
    """One checksum-validated edition ISBN, not an inferred title match."""
    identifiers = identifier_values(book)
    return next((value[5:] for value in sorted(identifiers) if value.startswith("isbn:")), "")


def _reference_label(book: Any) -> str:
    title = _candidate_public_field(book, "title", limit=2048) or "Sans titre"
    authors = _public_candidate_authors(book)
    author = authors[0] if authors else ""
    label = f"{title} — {author}" if author else title
    isbn = _reference_isbn(book)
    return f"{label} · ISBN {isbn}" if isbn else label


def _reference_selection_key(book: Any) -> str:
    """Select a *specific* reference edition, never a title/author cluster.

    Prefer identity of a local library book (stable over list reload), then
    attested ISBN or provider-scoped identifiers. An unresolved entry has no
    cross-object identity: keeping its Python object avoids substituting a
    homonym when the library list refreshes.
    """
    book_id = get_field(book, "book_id", None)
    if isinstance(book_id, (str, int)) and not isinstance(book_id, bool):
        stable_id = str(book_id).strip()
        if stable_id:
            return "book:" + stable_id
    identifiers = identifier_values(book)
    isbn = next((value for value in sorted(identifiers) if value.startswith("isbn:")), "")
    if isbn:
        return isbn
    source_id = next((value for value in sorted(identifiers) if value.startswith("source:")), "")
    if source_id:
        return source_id
    if identifiers:
        return "id:" + sorted(identifiers)[0]
    return "object:" + str(id(book))


def _external_book_url(value: Any) -> str | None:
    """Only ordinary public book pages may become external detail actions.

    Catalogue values are untrusted. In particular, direct acquisition links,
    signed paths, localhost aliases and non-standard ports must not escape
    through the shared detail panel as if they were public notices.
    """
    if type(value) is not str or not 1 <= len(value) <= 2048:
        return None
    url = value.strip()
    if (
        not url
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url)
        or any(char in url for char in "<>\\")
    ):
        return None
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if parsed.query or parsed.fragment:
        # Never export signed acquisition links or session identifiers.
        return None
    if port is not None and port != (443 if parsed.scheme == "https" else 80):
        return None

    host = parsed.hostname.lower().rstrip(".")
    if (
        not host or "." not in host
        or host == "localhost" or host.endswith((".local", ".localhost", ".internal", ".lan", ".home.arpa"))
        or len(host) > 253
        or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
               for part in host.split("."))
    ):
        return None
    try:
        address = ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if not address.is_global:
            return None
    elif not re.fullmatch(r"[a-z][a-z0-9-]{1,62}", host.rsplit(".", 1)[-1]):
        # Reject noncanonical numeric hosts such as 127.1 and 0177.0.0.1.
        return None

    path = parsed.path
    forbidden_segments = {
        "download", "downloads", "acquisition", "acquire",
        "licence", "license", "licenses", "licences",
        "token", "tokens", "oauth", "authorize", "authorization",
        "login", "signin", "drm", "adept",
    }
    # A path segment or control byte may be encoded several times. Never
    # dereference it; decoding here is used exclusively for rejection.
    for _ in range(4):
        if any(ord(char) < 32 or ord(char) == 127 for char in path):
            return None
        segments = [part.casefold() for part in path.split("/") if part]
        if any(
            part in forbidden_segments
            or part.endswith((".acsm", ".lcpl", ".epub", ".kepub"))
            for part in segments
        ):
            return None
        decoded = unquote(path)
        if decoded == path:
            return url
        path = decoded
    # Do not accept deeply nested encodings with uninspected final content.
    return None


def _public_external_field(item: Any, name: str) -> str | None:
    """Isolate one untrusted provider URL accessor from independent fallbacks.

    A faulty first property/mapping getter must never hide an independently
    attested notice or cover. Neither exceptions nor rejected values escape
    into the public UI model or diagnostics.
    """
    try:
        value = get_field(item, name, None)
    except Exception:
        return None
    return _external_book_url(value)


def _public_recommendation_notice(item: Any) -> str | None:
    """Prefer a validated friendly page, then an independent public notice.

    Never use download or acquisition coordinates as a fallback.
    """
    return (
        _public_external_field(item, "friendly_url")
        or _public_external_field(item, "notice_url")
    )


def _public_cover_url(item: Any) -> str | None:
    """Preserve attested public image URLs for both list and detail.

    Unlike public *document* links, genuine BnF catalogue images may carry
    only the public size/cache-version pair (`size` and `t`). Keep the
    exact source URL; do not erase the query or infer an edition. A signed,
    unknown or partial query is still rejected before it can reach CoverView.
    The existing public origin/path validator remains the authority.
    """
    for field in ("thumbnail_url", "cover_url"):
        ordinary = _public_external_field(item, field)
        if ordinary:
            return ordinary
        try:
            value = get_field(item, field, None)
        except Exception:
            continue
        if type(value) is not str or not 1 <= len(value) <= 2048:
            continue
        original = value.strip()
        try:
            parts = urlsplit(original)
        except ValueError:
            continue
        # A versioned catalogue image is not an acquisition URL or an
        # arbitrary signed URL. Accept precisely the already-attested
        # size+timestamp query used by public cover CDNs; nothing else.
        query = parts.query
        if (parts.scheme != "https" or parts.netloc != "covers.feedbooks.net"
                or not parts.path.startswith("/item/")):
            continue
        if (parts.fragment
                or not re.fullmatch(
                    r"(?:size=(?:small|medium|large)&t=[0-9]{1,12}"
                    r"|t=[0-9]{1,12}&size=(?:small|medium|large))",
                    query,
                )):
            continue
        public_base = parts._replace(query="", fragment="").geturl()
        if _external_book_url(public_base) is not None:
            return original
    return None


def _attested_catalogue_isbns(
    notices: Any, *, selected_sources: tuple[str, ...] = (), limit: int = 4,
) -> tuple[str, ...]:
    """Edition selectors, NOT borrowing permissions or exact item actions.

    An ISBN here was checked by the recommendation engine. Recheck its form
    and require original provider/credential/record coordinates so that a
    merged title or unattributed ISBN never becomes an actionable source.
    """
    selections: list[str] = []
    # A restored/corrupted ranking may contain an arbitrary object with a
    # fabricated has_source_coordinates property. It cannot attest an edition.
    # The engine produces only immutable CatalogNoticeVariant records.
    if (type(notices) not in (tuple, list)
            or type(selected_sources) is not tuple
            or len(selected_sources) > 128
            or any(type(source) is not str or not source for source in selected_sources)
            or type(limit) is not int or not 1 <= limit <= 32):
        return ()
    for notice in notices[:256]:
        if type(notice) is not CatalogNoticeVariant:
            continue
        if selected_sources and notice.source_provider not in selected_sources:
            continue
        # An ISBN selector must be backed by the SAME validated, bounded
        # original provider coordinates as an exact Search detail action.
        # The source dataclass can contain malformed cached field values.
        coordinates = (
            notice.source_provider, notice.source_credential,
            notice.record_id, notice.docbase, notice.source_collection,
        )
        if (any(type(value) is not str or len(value) > 512
                for value in coordinates)
                or not all(coordinates[:3])):
            continue
        isbn = notice.isbn
        if type(isbn) is not str or not re.fullmatch(r"97[89][0-9]{10}", isbn):
            continue
        if sum(
            int(digit) * (1 if index % 2 == 0 else 3)
            for index, digit in enumerate(isbn)
        ) % 10:
            continue
        if isbn not in selections:
            selections.append(isbn)
        if len(selections) == limit:
            break
    return tuple(selections)


def _local_ranked_isbns(
    ranked: RankedCandidate, sources: tuple[str, ...],
) -> tuple[str, ...]:
    """Read ISBNs from the correct catalogue, never from another variant.

    A recommendation may merge multiple exact editions into one ranked book.
    The aggregate candidate's ISBN alone cannot identify the selected source.
    Core's normal public Search ISBN selector still uses its historical
    4-edition bound; this read-only filter may inspect up to 32 verified
    editions among the same first 256 already represented in the detail.
    """
    try:
        item = ranked.item
        notices = getattr(ranked, "notices", ())
    except Exception:
        return ()
    verified = _attested_catalogue_isbns(
        notices, selected_sources=sources, limit=32,
    )
    item_isbns = _local_recommendation_field_values(item, "isbn")
    if sources:
        # In a merged multi-source record, only a typed *exact* notice
        # can attest the ISBN of the selected catalogue. A candidate scalar
        # may represent a different edition (even with the same book title).
        if type(notices) in (tuple, list) and any(
            type(notice) is CatalogNoticeVariant
            for notice in notices[:256]
        ):
            return verified
        source = _candidate_public_field(item, "source_provider", limit=120)
        return item_isbns if source in sources else ()
    return tuple(dict.fromkeys((*item_isbns, *verified)))[:32]


_LANGUAGE_LABELS = {
    "fr": "Français",
    "en": "Anglais",
    "de": "Allemand",
    "es": "Espagnol",
    "it": "Italien",
    "pt": "Portugais",
    "nl": "Néerlandais",
}


def _candidate_languages(item: Any) -> tuple[str, ...]:
    """Project only safely normalized language tags from explicit evidence.

    Provider metadata and legacy language normalizers are independent trust
    boundaries: one poisoned accessor/tag must not hide later valid languages,
    break the language filter or expose private exception messages.
    """
    def field(container: Any, key: str) -> Any:
        try:
            return get_field(container, key, None)
        except Exception:
            return None

    metadata = field(item, "metadata")
    values: list[str] = []
    for raw in (
        field(item, "language"),
        field(item, "languages"),
        field(metadata, "language") if isinstance(metadata, Mapping) else None,
        field(metadata, "languages") if isinstance(metadata, Mapping) else None,
    ):
        if type(raw) in (list, tuple, set):
            # Bound untrusted provider arrays; never stringify nested values
            # or accept a str subclass with overridable text methods.
            for tag in islice(raw, 128):
                if type(tag) is str and tag.strip():
                    values.append(tag)
        elif type(raw) is str and raw.strip():
            values.append(raw)

    normalized: list[str] = []
    for raw in values[:256]:
        try:
            canonical = normalize_language_tag(raw)
        except Exception:
            continue
        if type(canonical) is str and canonical and canonical not in normalized:
            normalized.append(canonical)
    return tuple(normalized)


def _language_label(language: str) -> str:
    language = str(language or "")
    return _LANGUAGE_LABELS.get(language, language)


def _freeze_overview_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(
            sorted(
                (str(key), _freeze_overview_value(item))
                for key, item in value.items()
            )
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_overview_value(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze_overview_value(item) for item in value), key=repr))
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _library_overview_signature(row: Mapping[str, Any]) -> Any | None:
    # selected/source connectivity/capabilities can change without changing
    # metadata.library_entries(). Only persisted library/sync projections
    # participate in snapshot freshness.
    if not any(key in row for key in ("statistics", "sync", "deep_last_at")):
        return None
    return (
        _freeze_overview_value(row.get("statistics")),
        _freeze_overview_value(row.get("sync")),
        _freeze_overview_value(row.get("deep_last_at")),
    )


class RecommendationsPlugin:
    def __init__(self) -> None:
        self.context = None
        self._view = None
        self._pending_reference = None

    def activate(self, context):
        self.context = context
        return self

    def pages(self):
        return [
            UiPageContribution(
                route="recommendations",
                title="Recommandations",
                icon_name="starred-symbolic",
                order=35,
                factory=self.build,
                group="usage",
                actions=(
                    UiActionContribution("recommendations.refresh", "Recalculer", self._refresh_action, "view-refresh-symbolic"),
                ),
            )
        ]

    def _refresh_action(self, host):
        if self._view is not None:
            self._view.refresh()
        else:
            host.toast("Ouvrez la page Recommandations pour lancer le calcul.")

    def recommend_from_book(self, book) -> str:
        # Validate synchronously so Core can surface a useful error before
        # navigating. The concrete BookRef protocol remains structural.
        from reference_recommendation import prepare_reference

        prepare_reference(book, max_queries=self._config_max_queries())
        self._pending_reference = book
        if self._view is not None:
            self._view.set_reference_book(book)
        return "recommendations"

    def _config_max_queries(self) -> int:
        if self.context is None or not isinstance(self.context.config, Mapping):
            return 8
        try:
            return int(self.context.config.get("max_queries", 8))
        except (TypeError, ValueError):
            return 8

    def deactivate(self):
        try:
            if self._view is not None:
                self._view.dispose()
        finally:
            self._view = None
            self._pending_reference = None
            self.context = None

    def build(self, host):
        from gi.repository import GLib, Gtk

        view = UxRecommendationsView(self, host, Gtk, GLib)
        self._view = view
        if self._pending_reference is not None:
            view.set_reference_book(self._pending_reference, refresh=False)
        _refresh_on_first_map(view, view.widget, GLib)
        return view.widget

class UxRecommendationsView:
    """Recommendations UI using only public Core brokers and master/detail contracts."""

    PARTIAL_COALESCE_MS = 180
    OVERVIEW_REFRESH_COALESCE_MS = 350

    def __init__(self, plugin, host, Gtk, GLib):
        # A queued GTK first-map callback must not resurrect a disposed view.
        self._disposed = False
        # These fields are deliberately ready before the parent reloads identities:
        # Gtk.DropDown::notify::selected may call refresh() during construction.
        self._provider_labels: dict[str, str] = {"": "Tous les catalogues"}
        self._language_ids: list[str] = [""]
        self._reloading_languages = False
        self._provisional_order: dict[str, int] = {}
        self._provisional_active = False
        self._profile_books_count = 0
        self._reference_books: list[Any] = []
        self._reference_book: Any = None
        self._reloading_references = False
        self._library_snapshot_cache = LibrarySnapshotCache()
        self._identity_library_signatures: dict[str, Any | None] = {}
        self._active_identity_id: str | None = None
        self._reloading_identities = False
        self._profile_selection_required = False
        self._overview_refresh_source_id = 0
        self._overview_refresh_pending = False
        self._diagnostic_cache = 0
        self._diagnostic_network = 0
        self._refresh_started_at: float | None = None
        self._init_master_detail(plugin, host, Gtk, GLib)

        self._refresh_button.set_tooltip_text(
            "Relancer le calcul en conservant les filtres de vue"
        )
        self._provisional_badge = Gtk.Label(label="Provisoire")
        self._provisional_badge.add_css_class("badge")
        self._provisional_badge.set_tooltip_text(
            "Les résultats sont déjà utilisables, mais leur ordre peut encore évoluer."
        )
        self._provisional_badge.set_visible(False)
        parent = self._summary.get_parent()
        if parent is not None and hasattr(parent, "insert_child_after"):
            parent.insert_child_after(self._provisional_badge, self._spinner)

    def _init_master_detail(self, plugin, host, Gtk, GLib):
        # Deliberately do not call the legacy ListBox constructor. The public
        # host owns the standard MasterDetailPage / BookListCell / DetailPanel.
        self.plugin = plugin
        self.host = host
        self.Gtk = Gtk
        self.GLib = GLib
        self._ranked = []
        self._identity_ids = []
        self._provider_ids = [""]
        self._unsubscribe = None
        self._generation = 0
        self._busy = False
        self._partial_warning_count = 0
        self._task_handle = None
        self._pending_partial = None
        self._partial_source_id = 0
        self._render_generation = 0
        self._ranked_by_key: dict[str, Any] = {}
        # Optional explanation is never used by the ranking engine.
        self._llm_enabled = False
        self._llm_selection_key = None
        self._llm_selected_row = None
        self._llm_explanation = None
        self._llm_handle = None
        self._llm_generation = 0
        self._llm_button = None
        self._llm_hint = None

        builder = getattr(host, "build_book_master_detail", None)
        if not callable(builder):
            raise RuntimeError("Core incompatible : présentation maître/détail standard indisponible")
        self._master = builder(
            title="Recommandations pour votre bibliothèque",
            subtitle="Résultats issus des catalogues connectés, classés par affinité puis diversifiés.",
            empty_title="Aucune recommandation",
            empty_desc="Lancez le calcul pour rechercher des livres dans les catalogues connectés.",
        )
        self.widget = self._master.widget
        self._master.connect_selected(self._selected_row)

        toolbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        toolbox.set_hexpand(True)
        build_filters = getattr(host, "build_local_query_controls", None)
        self._query_controls = (
            build_filters(changed=self._render, placeholder="Filtrer les résultats calculés")
            if callable(build_filters) else None
        )
        # Search's same field selector, now explicitly enabled for local
        # recommendation filtering. No query or recalculation is started.
        enable_field = getattr(self._query_controls, "enable_field_filter", None)
        if callable(enable_field):
            enable_field()

        # The same one-row + horizontal-scroll layout as Search's shared
        # filters: this strip contains ONLY recommendation calculation
        # parameters; the local Search widgets remain a separate row below.
        # A wrapping FlowBox previously scattered the controls over two rows
        # and changed their keyboard position as the window was resized.
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        controls.set_halign(Gtk.Align.START)
        self._calculation_parameters = controls
        controls_scroll = Gtk.ScrolledWindow()
        controls_scroll.set_name("recommendations-parameters-scroll")
        controls_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        controls_scroll.set_propagate_natural_width(False)
        controls_scroll.set_propagate_natural_height(True)
        controls_scroll.set_hexpand(True)
        controls_scroll.set_child(controls)
        self._calculation_parameters_scroll = controls_scroll

        self._identity_model = Gtk.StringList.new([])
        self._identity = Gtk.DropDown(model=self._identity_model)
        # The Core global profile is authoritative. Keep this dropdown only
        # as an internal model for existing generation-safe adapters: it is
        # deliberately NOT parented to GTK, visible, or user-selectable.
        # A second profile selector here could compute for A while the
        # Core's global context (including cover resolution) remains B.

        self._reference_model = Gtk.StringList.new(["Bibliothèque entière"])
        self._reference = Gtk.DropDown(model=self._reference_model)
        self._reference.set_sensitive(False)
        self._reference.set_tooltip_text(
            "Point de départ : bibliothèque entière ou livre précis de ce profil"
        )
        self._reference.connect("notify::selected", self._reference_changed)
        controls.append(self._labeled("Référence", self._reference))

        if self._query_controls is None:
            self._append_legacy_filters(controls, Gtk)

        self._exclude_owned = Gtk.CheckButton(label="Exclure les possédés")
        self._exclude_owned.set_active(self._config_bool("exclude_owned", True))
        self._exclude_owned.set_tooltip_text(
            "Masque par défaut les livres déjà présents dans la bibliothèque de ce profil. "
            "Décochez pour les réafficher volontairement."
        )
        self._exclude_owned.connect("toggled", lambda *_: self.refresh())
        controls.append(self._exclude_owned)

        # The configured threshold is the baseline. A later user change
        # is an active *view* filter, not a new recommendation ranking.
        self._affinity_initial_threshold = self._config_int("minimum_score", 15)
        score_adjustment = Gtk.Adjustment(
            lower=0, upper=100, step_increment=5, page_increment=10,
            value=self._affinity_initial_threshold,
        )
        self._score = Gtk.SpinButton(adjustment=score_adjustment, climb_rate=1, digits=0)
        self._score.set_tooltip_text(_AFFINITY_MIN_TOOLTIP)
        self._score.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Seuil minimal d’affinité des recommandations"],
        )
        self._score.connect("value-changed", lambda *_: self._render())
        controls.append(self._labeled("Affinité ≥", self._score))

        exploration_adjustment = Gtk.Adjustment(
            lower=0, upper=100, step_increment=5, page_increment=10,
            value=self._config_int("exploration", 25),
        )
        self._exploration = Gtk.SpinButton(adjustment=exploration_adjustment, climb_rate=1, digits=0)
        self._exploration.set_tooltip_text(_DIVERSITY_TOOLTIP)
        self._exploration.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Diversité des recommandations"],
        )
        controls.append(self._labeled("Diversité", self._exploration))

        self._refresh_button = Gtk.Button(label="Recalculer", icon_name="view-refresh-symbolic")
        self._refresh_button.set_tooltip_text("Relancer le calcul en conservant les filtres de vue")
        self._refresh_button.connect("clicked", lambda *_: self.refresh())
        controls.append(self._refresh_button)

        # Double opt-in: explicit plugin configuration AND an explicit click.
        # Opening the page/choosing a book never sends data to an LLM.
        self._llm_enabled = self._config_bool("llm_explanations", False)
        if self._llm_enabled:
            self._llm_button = Gtk.Button(label="Reformuler les raisons (IA)")
            self._llm_button.set_sensitive(False)
            self._llm_button.set_tooltip_text(
                "Envoi explicite des trois raisons affichées au service IA configuré "
                "(qui peut être distant) ; aucun titre, profil ou livre entier."
            )
            self._llm_button.connect("clicked", lambda *_: self._explain_selected())
            controls.append(self._llm_button)
        toolbox.append(controls_scroll)
        if self._query_controls is not None:
            toolbox.append(self._query_controls.widget)
        if self._llm_enabled:
            self._llm_hint = Gtk.Label(
                label="IA facultative : choisissez une recommandation puis confirmez l'envoi "
                      "des raisons. Aucun changement du classement.",
                xalign=0, wrap=True,
            )
            toolbox.append(self._llm_hint)

        self._progress = Gtk.ProgressBar()
        self._progress.set_show_text(True)
        self._progress.set_fraction(0.0)
        self._progress.set_text("0 % · Préparation")
        self._progress.set_tooltip_text(
            "Progression du calcul : requêtes × catalogues, puis classement et diversification"
        )
        toolbox.append(self._progress)

        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._spinner = Gtk.Spinner()
        status_row.append(self._spinner)
        self._summary = Gtk.Label(label="Préparation…", xalign=0)
        self._summary.set_hexpand(True)
        self._summary.set_wrap(True)
        status_row.append(self._summary)
        toolbox.append(status_row)

        # This is a catalogue *inventory/response* status, not the ranked
        # results Source facet. No rights, accounts or provider error payload.
        self._catalogue_overview = Gtk.Label(label="", xalign=0, wrap=True)
        self._catalogue_overview.add_css_class("dim-label")
        self._catalogue_overview.set_visible(False)
        toolbox.append(self._catalogue_overview)

        self._master.append_toolbar(toolbox)
        self._master.empty_detail(
            "Aucune recommandation sélectionnée",
            "Sélectionnez un livre dans la liste pour afficher la notice et les raisons de la recommandation.",
        )

        self._reload_identities()
        subscribe = getattr(host, "subscribe_identity_overview", None)
        if callable(subscribe):
            self._unsubscribe = subscribe(self._identity_overview_changed)

    def _append_legacy_filters(self, controls, Gtk):
        """Keep the installed plugin usable on Core versions before #3687."""
        self._provider_model = Gtk.StringList.new(["Tous les catalogues"])
        self._provider = Gtk.DropDown(model=self._provider_model)
        self._provider.set_tooltip_text("Filtre les recommandations déjà calculées")
        self._provider.connect("notify::selected", lambda *_: self._render())
        controls.append(self._labeled("Catalogue", self._provider))

        self._language_model = Gtk.StringList.new(["Toutes"])
        self._language = Gtk.DropDown(model=self._language_model)
        self._language.set_tooltip_text(
            "Filtre sur les langues explicitement fournies par les catalogues ; "
            "« Toutes » conserve aussi les ouvrages sans langue connue"
        )
        self._language.connect("notify::selected", self._language_changed)
        controls.append(self._labeled("Langue", self._language))

        self._text = Gtk.SearchEntry(placeholder_text="Titre ou auteur")
        self._text.connect("search-changed", lambda *_: self._render())
        controls.append(self._labeled("Filtrer", self._text))

    def _labeled(self, label: str, widget: Any):
        box = self.Gtk.Box(orientation=self.Gtk.Orientation.HORIZONTAL, spacing=5)
        box.append(self.Gtk.Label(label=label))
        box.append(widget)
        return box

    def _config(self) -> Mapping[str, Any]:
        ctx = self.plugin.context
        return ctx.config if ctx is not None and isinstance(ctx.config, Mapping) else {}

    def _config_int(self, key: str, fallback: int) -> int:
        try:
            return int(self._config().get(key, fallback))
        except (TypeError, ValueError):
            return fallback

    def _config_bool(self, key: str, fallback: bool) -> bool:
        value = self._config().get(key, fallback)
        return value if isinstance(value, bool) else bool(fallback)

    def _config_str(self, key: str, fallback: str) -> str:
        value = self._config().get(key, fallback)
        return str(value or fallback)

    def _identity_overview_changed(self) -> None:
        # Overview notifications also cover selection, connection and capability
        # changes. Refresh recommendations for those events, but invalidate a
        # library snapshot only when the persisted library/sync projection for
        # that identity changed.
        identity_widget = getattr(self, "_identity", None)
        previous_identity = (
            self.selected_identity_id()
            if identity_widget is not None
            else getattr(self, "_active_identity_id", None)
        )
        changed_identities = self._reload_identities()
        current_identity = (
            self.selected_identity_id()
            if identity_widget is not None
            else previous_identity
        )
        if previous_identity is not None and previous_identity != current_identity:
            self._reference_book = None
            self.plugin._pending_reference = None
            self._reference_books = []
            self._reload_reference_books(())
        snapshot_cache = getattr(self, "_library_snapshot_cache", None)
        if snapshot_cache is not None:
            signatures = getattr(self, "_identity_library_signatures", {})
            active = self._active_identity_id or self.selected_identity_id()
            if active and signatures.get(active) is None:
                # Older/fallback identity hosts do not expose a freshness
                # projection. Preserve the conservative historical behaviour.
                snapshot_cache.invalidate(active)
            else:
                for identity_id in changed_identities:
                    snapshot_cache.invalidate(identity_id)
        if self._active_identity_id is None:
            return
        if (
            (previous_identity is not None and previous_identity != current_identity)
            or self._active_identity_id != current_identity
        ):
            # An in-flight task belongs to the previous profile. Do not wait
            # for its completion to schedule the new refresh: a queued result
            # could repaint the old account's recommendations under the new
            # GTK selection. refresh() immediately revokes its generation,
            # cancels its handle and clears the old visible list/detail.
            self.refresh()
            return
        if not self._identity_ids:
            return
        self._overview_refresh_pending = True
        if not self._busy:
            self._schedule_overview_refresh()

    def _schedule_overview_refresh(self) -> None:
        if not self._overview_refresh_pending or self._active_identity_id is None:
            return
        if self._overview_refresh_source_id:
            try:
                self.GLib.source_remove(self._overview_refresh_source_id)
            except Exception:
                pass
        self._overview_refresh_source_id = self.GLib.timeout_add(
            self.OVERVIEW_REFRESH_COALESCE_MS,
            self._flush_overview_refresh,
        )

    def _flush_overview_refresh(self) -> bool:
        self._overview_refresh_source_id = 0
        if not self._overview_refresh_pending:
            return False
        if self._busy:
            return False
        self._overview_refresh_pending = False
        self.refresh()
        return False

    def _clear_overview_refresh(self) -> None:
        if self._overview_refresh_source_id:
            try:
                self.GLib.source_remove(self._overview_refresh_source_id)
            except Exception:
                pass
            self._overview_refresh_source_id = 0
        self._overview_refresh_pending = False

    def _reload_identities(self) -> set[str]:
        current = self.selected_identity_id()
        previous_signatures = dict(
            getattr(self, "_identity_library_signatures", {})
        )
        rows = []
        getter = getattr(self.host, "identity_overview", None)
        if callable(getter):
            try:
                rows = list(getter() or [])
            except Exception:
                rows = []
        if not rows and self.plugin.context is not None:
            broker = self.plugin.context.broker("identities")
            if broker is not None:
                try:
                    rows = list(broker.identities() or [])
                except Exception:
                    rows = []

        current_signatures: dict[str, Any | None] = {}
        self._reloading_identities = True
        try:
            self._identity_ids = []
            while self._identity_model.get_n_items():
                self._identity_model.remove(0)
            selected = 0
            current_index = None
            selected_index = None
            selected_count = 0
            for row in rows:
                identity_id = str(row.get("identity_id") or "")
                if not identity_id:
                    continue
                self._identity_ids.append(identity_id)
                current_signatures[identity_id] = _library_overview_signature(row)
                self._identity_model.append(str(row.get("label") or row.get("name") or identity_id))
                index = len(self._identity_ids) - 1
                if identity_id == current:
                    current_index = index
                if row.get("selected") is True:
                    selected_index = index
                    selected_count += 1
            # Trust only the *explicitly selected Core identity*. An absent
            # selection must never silently fall back to the first identity;
            # a global A -> B switch may legitimately replace a removed A.
            # Do not keep a sticky plugin-only selection state: there is no
            # longer any separate user choice to clear it.
            self._profile_selection_required = selected_count != 1
            self._identity.set_sensitive(False)
            self._identity.set_selected(
                selected_index if selected_count == 1 else 0xFFFFFFFF
            )
        finally:
            self._reloading_identities = False
        self._identity_library_signatures = current_signatures
        all_ids = set(previous_signatures) | set(current_signatures)
        return {
            identity_id
            for identity_id in all_ids
            if previous_signatures.get(identity_id)
            != current_signatures.get(identity_id)
        }

    def selected_identity_id(self) -> str | None:
        if not self._identity_ids:
            return None
        index = self._identity.get_selected()
        # Invalid GTK positions, booleans and partial test hosts must never
        # select an unrelated profile by implicit int coercion.
        return (
            self._identity_ids[index]
            if type(index) is int and 0 <= index < len(self._identity_ids)
            else None
        )

    def _identity_changed(self, *_args) -> None:
        if self._reloading_identities:
            return
        if self.selected_identity_id() is not None:
            # A real GTK selection is the only way to leave a missing-profile
            # state. Overview notifications are not explicit user consent.
            self._profile_selection_required = False
        if self._identity_ids:
            self._reference_book = None
            self.plugin._pending_reference = None
            self._reference_books = []
            self._reload_reference_books(())
            self.refresh()

    def _reference_changed(self, *_args) -> None:
        if self._reloading_references:
            return
        index = int(self._reference.get_selected())
        reference = (
            self._reference_books[index - 1]
            if 0 < index <= len(self._reference_books)
            else None
        )
        if reference is self._reference_book:
            return
        self._reference_book = reference
        self.plugin._pending_reference = reference
        self.refresh()

    def set_reference_book(self, book, *, refresh: bool = True) -> None:
        from reference_recommendation import prepare_reference

        prepare_reference(book, max_queries=self._config_int("max_queries", 8))
        self._reference_book = book
        self.plugin._pending_reference = book
        self._reload_reference_books(self._reference_books)
        if refresh:
            self.refresh()

    def _reload_reference_books(self, books) -> None:
        current_key = _reference_selection_key(self._reference_book) if self._reference_book is not None else ""
        ordered = list(books or ())
        if self._reference_book is not None and current_key:
            if not any(_reference_selection_key(book) == current_key for book in ordered):
                ordered.append(self._reference_book)
        ordered = sorted(
            ordered,
            key=lambda book: (_reference_label(book).casefold(), candidate_key(book)),
        )
        self._reloading_references = True
        try:
            self._reference_books = ordered
            while self._reference_model.get_n_items() > 1:
                self._reference_model.remove(1)
            selected = 0
            for index, book in enumerate(ordered, 1):
                self._reference_model.append(_reference_label(book))
                if current_key and _reference_selection_key(book) == current_key:
                    selected = index
                    self._reference_book = book
            self._reference.set_selected(selected)
            self._reference.set_sensitive(bool(ordered))
        finally:
            self._reloading_references = False

    def selected_provider_id(self) -> str:
        index = int(self._provider.get_selected())
        return self._provider_ids[index] if 0 <= index < len(self._provider_ids) else ""

    def selected_language_id(self) -> str:
        index = int(self._language.get_selected())
        return self._language_ids[index] if 0 <= index < len(self._language_ids) else ""

    def _view_filters(self) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        controls = getattr(self, "_query_controls", None)
        if controls is not None:
            return (
                controls.filter_text(), controls.selected_sources(),
                controls.selected_languages(),
            )
        provider = self.selected_provider_id()
        language = self.selected_language_id()
        return (
            self._text.get_text().strip(),
            (provider,) if provider else (), (language,) if language else (),
        )

    def _local_filter_state(
        self,
    ) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
        """Bound user-facing local filter state before matching or summarizing.

        A stale/untrusted host adapter must never stringify private values,
        break list refresh, or turn an invalid active filter into 'Tous'.
        This projection neither queries providers nor changes exact rights.
        """
        try:
            text, sources, languages = self._view_filters()
        except Exception:
            return None
        if type(text) is not str or len(text) > 2048 or not text.isprintable():
            return None
        for values in (sources, languages):
            if (type(values) not in (tuple, list) or len(values) > 128
                    or any(type(value) is not str or not value
                           or len(value) > 120 or value != value.strip()
                           or not value.isprintable() for value in values)):
                return None
        return text, tuple(dict.fromkeys(sources)), tuple(dict.fromkeys(languages))

    def _language_changed(self, *_args) -> None:
        if not self._reloading_languages:
            self._render()

    def _selected_catalogue_sources(self) -> tuple[str, ...]:
        """Current view-only sources, never an account or rights decision.

        Missing filters on a legacy/headless host mean "Tous". A malformed
        active filter must NOT silently show all catalogues' action routes.
        """
        controls = getattr(self, "_query_controls", None)
        try:
            if controls is not None:
                values = controls.selected_sources()
            elif getattr(self, "_provider", None) is not None:
                selected = self.selected_provider_id()
                values = (selected,) if selected else ()
            else:
                values = ()
        except Exception:
            return ("\x00",)
        if (type(values) not in (list, tuple) or len(values) > 128
                or any(type(x) is not str or not x or len(x) > 120
                       or x != x.strip() or not x.isprintable() for x in values)):
            return ("\x00",)
        return tuple(dict.fromkeys(values))


    def _matches(self, ranked: RankedCandidate) -> bool:
        # A corrupted score from a stale cache is not a legitimate ranking.
        # It must not execute an arbitrary comparison or become a selected
        # catalogue record with an invented affinity.
        score = _public_affinity_score(ranked.score)
        if score is None or score < int(self._score.get_value()):
            return False
        state = self._local_filter_state()
        if state is None:
            return False
        text, sources, languages = state
        if sources and set(sources).isdisjoint(_public_ranked_values(ranked.sources)):
            return False
        if languages and set(languages).isdisjoint(_candidate_languages(ranked.item)):
            return False
        controls = getattr(self, "_query_controls", None)
        matches_document = getattr(controls, "matches_document", None)
        if callable(matches_document):
            try:
                if not matches_document(ranked.item):
                    return False
            except Exception:
                return False
        needle = normalize(text)
        if needle:
            item = ranked.item
            selected_field = getattr(controls, "filter_field", None)
            try:
                field = selected_field() if callable(selected_field) else "all"
            except Exception:
                return False
            if type(field) is not str or field not in (
                "all", "title", "author", "isbn", "subject",
                "series", "collection", "language",
            ):
                return False
            if field == "all":
                # Preserve historical title + attested authors by default.
                # Selecting a field is an explicit, local-only restriction.
                haystack = normalize(" ".join((
                    _candidate_public_field(item, "title", limit=2048),
                    " ".join(_public_candidate_authors(item)),
                )))
                if needle not in haystack:
                    return False
            else:
                values = (
                    _local_ranked_isbns(ranked, sources)
                    if field == "isbn" else
                    _local_recommendation_field_values(item, field)
                )
                if not values:
                    return False
                haystack = normalize(" ".join(values))
                tokens = needle.split()
                if field == "subject":
                    # Same one-character whole-word guard as Core Search.
                    if not all(
                        bool(re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", haystack))
                        if len(token) == 1 else token in haystack
                        for token in tokens
                    ):
                        return False
                elif not all(token in haystack for token in tokens):
                    return False
        return True

    def _base_reload_providers(self, providers: tuple[tuple[str, str], ...]) -> None:
        selected_id = self.selected_provider_id()
        self._provider_ids = [""]
        while self._provider_model.get_n_items() > 1:
            self._provider_model.remove(1)
        selected = 0
        for provider_id, label in providers:
            pid = str(provider_id or "")
            if not pid:
                continue
            self._provider_ids.append(pid)
            self._provider_model.append(str(label or pid))
            if pid == selected_id:
                selected = len(self._provider_ids) - 1
        self._provider.set_selected(selected)

    def _reload_languages(self, ranked_rows) -> None:
        available = {
            language
            for ranked in ranked_rows
            for language in _candidate_languages(ranked.item)
        }
        controls = getattr(self, "_query_controls", None)
        selected_id = self.selected_language_id() if controls is None else ""
        if selected_id:
            available.add(selected_id)
        if controls is not None:
            # A partial top-N is not an exhaustive language inventory. Keep
            # only the user's currently selected languages that were already
            # attested in an earlier loaded result of this view. Otherwise
            # MultiSelect drops the choice and silently broadens to 'Toutes'.
            # Final results (_busy=False) strictly prune absent languages.
            previously_attested = getattr(self, "_attested_language_choices", frozenset())
            reader = getattr(controls, "selected_languages", None)
            if getattr(self, "_busy", False) and callable(reader):
                try:
                    selection = reader()
                except Exception:
                    selection = ()
                if type(selection) in (tuple, list) and len(selection) <= 128:
                    for language in selection:
                        if type(language) is str and language in previously_attested:
                            available.add(language)
        self._attested_language_choices = frozenset(available)
        ordered = sorted(available, key=lambda language: (_language_label(language).casefold(), language))
        if controls is not None:
            controls.set_choices(languages=tuple((lang, _language_label(lang)) for lang in ordered))
            return

        self._reloading_languages = True
        try:
            self._language_ids = [""]
            while self._language_model.get_n_items() > 1:
                self._language_model.remove(1)
            selected = 0
            for language in ordered:
                self._language_ids.append(language)
                self._language_model.append(_language_label(language))
                if language == selected_id:
                    selected = len(self._language_ids) - 1
            self._language.set_selected(selected)
        finally:
            self._reloading_languages = False

    def _reload_document_choices(self) -> None:
        controls = getattr(self, "_query_controls", None)
        set_documents = getattr(controls, "set_document_choices", None)
        if callable(set_documents):
            # Use the already published provisional badge state: preserving
            # the historical no-arg hook also keeps headless/host adapters.
            set_documents(
                (ranked.item for ranked in self._ranked),
                provisional=bool(getattr(self, "_provisional_active", False)),
            )

    @staticmethod
    def _candidate_key(ranked: Any) -> str:
        item = ranked.item
        key = candidate_key(item)
        if key:
            return str(key)
        return "|".join(
            (
                str(get_field(item, "title", "") or ""),
                str(get_field(item, "author", "") or ""),
                str(get_field(item, "source_provider", "") or ""),
            )
        )

    def _stable_partial(self, ranked_rows) -> list[Any]:
        for ranked in ranked_rows:
            key = self._candidate_key(ranked)
            if key not in self._provisional_order:
                self._provisional_order[key] = len(self._provisional_order)
        return sorted(
            ranked_rows,
            key=lambda ranked: self._provisional_order[self._candidate_key(ranked)],
        )

    def _display_source(self, provider_id: str) -> str:
        # The source name is copied into both the list and the public
        # detail; never stringify provider objects or expose bad cache labels.
        source = _public_candidate_text(provider_id, limit=120)
        if not source:
            return ""
        return _public_candidate_text(
            self._provider_labels.get(source, source), limit=240,
        )

    def _set_catalogue_coverage(
        self, result: RecommendationResult | None = None,
    ) -> None:
        label = getattr(self, "_catalogue_overview", None)
        if label is None:
            return
        summary, tooltip = _catalogue_coverage_projection(result)
        label.set_label(summary)
        label.set_tooltip_text(tooltip or None)
        label.set_visible(bool(summary))

    def _diagnostic_tooltip(self) -> str:
        return (
            "Progression du calcul : requêtes × catalogues, puis classement et diversification.\n"
            f"Diagnostic : {self._diagnostic_cache} lot(s) depuis le cache Core · "
            f"{self._diagnostic_network} lot(s) depuis le réseau."
        )

    def _set_provisional(self, active: bool) -> None:
        self._provisional_active = bool(active)
        badge = getattr(self, "_provisional_badge", None)
        if badge is not None:
            badge.set_visible(self._provisional_active)

    def _reload_providers(
        self, providers: tuple[tuple[str, str], ...], ranked_rows=None,
    ) -> None:
        # The connected-provider inventory is NOT the result-set Source
        # facet: a connected catalogue may have returned no recommendation.
        # Explicit rows (or the current saved projection) constrain the facet.
        # Older headless hosts without a result projection keep the historical
        # inventory contract. Never synthesize missing result sources.
        if ranked_rows is None:
            ranked_rows = getattr(self, "_ranked", None)
        available: set[str] | None = None
        if ranked_rows is not None:
            available = set()
            rows = ranked_rows[:4096] if type(ranked_rows) in (list, tuple) else ()
            for ranked in rows:
                try:
                    sources = getattr(ranked, "sources", ())
                except Exception:
                    continue
                for source in _public_ranked_values(sources):
                    if source_id := _public_candidate_text(source, limit=120):
                        available.add(source_id)
        # Partial ranking is not an exhaustive catalogue inventory. A user
        # may have selected Source B in the previous complete result while
        # the current provisional top-N only contains Source A. Dropping B
        # here silently resets the filter to 'Tous' and exposes A's rows and
        # exact actions before the user has changed their choice. Retain
        # *already selected* sources while a calculation is active, but only
        # if the current provider inventory still attests the source. The
        # final complete result (_busy=False) drops truly absent choices.
        retained: set[str] = set()
        if available is not None and getattr(self, "_busy", False):
            selected = self._selected_catalogue_sources()
            if selected != ("\x00",):
                retained.update(selected)
        # Catalogue labels populate the GTK filter *before* a detail is
        # selected. Apply the same public text boundary already used by
        # _display_source; never stringify a provider object or iterate
        # mapping keys as (provider, label) pairs after a cache restore.
        safe: list[tuple[str, str]] = []
        seen: set[str] = set()
        entries = providers[:128] if type(providers) in (list, tuple) else ()
        for entry in entries:
            if type(entry) not in (list, tuple) or len(entry) != 2:
                continue
            provider_id = _public_candidate_text(entry[0], limit=120)
            if (not provider_id or provider_id in seen
                    or (available is not None and provider_id not in available
                        and provider_id not in retained)):
                continue
            label = _public_candidate_text(entry[1], limit=240) or provider_id
            seen.add(provider_id)
            self._provider_labels[provider_id] = label
            safe.append((provider_id, label))
        controls = getattr(self, "_query_controls", None)
        if controls is not None:
            controls.set_choices(sources=tuple(safe))
        else:
            self._base_reload_providers(tuple(safe))

    def _start_refresh(self):
        self._reset_llm_selection(None)
        # Advance the generation before any early return. Otherwise a prior
        # worker's already queued GTK callback can repaint the wrong profile.
        self._generation += 1
        if self._task_handle is not None:
            self._task_handle.cancel()
        self._task_handle = None
        # The previous task may have been busy even when the newly selected
        # profile is no longer valid or the Core brokers disappear.
        self._busy = False
        stop_spinner = getattr(self._spinner, "stop", None)
        if callable(stop_spinner):
            stop_spinner()
        self._refresh_button.set_sensitive(True)

        identity_id = self.selected_identity_id()
        if not identity_id:
            self._show_calculation_unavailable("Aucun profil disponible.")
            return False
        context = self.plugin.context
        if context is None:
            self._show_calculation_unavailable("Plugin non activé.")
            return False
        metadata_broker = context.broker("metadata")
        catalog_broker = context.broker("catalog")
        task_broker = context.broker("tasks")
        if metadata_broker is None or catalog_broker is None or task_broker is None:
            self._show_calculation_unavailable(
                "Core incompatible : brokers metadata/catalog/tasks indisponibles."
            )
            return False

        generation = self._generation
        self._refresh_started_at = time.perf_counter()
        self._busy = True
        self._refresh_button.set_sensitive(False)
        self._spinner.start()
        self._progress.set_fraction(0.0)
        reference = self._reference_book
        if reference is None:
            self._progress.set_text("0 % · Lecture de la bibliothèque")
            self._summary.set_label("Lecture de la projection locale de la bibliothèque…")
        else:
            self._progress.set_text("0 % · Préparation du livre de référence")
            self._summary.set_label(
                "Recommandations centrées sur « " + _reference_label(reference) + " »…"
            )
        exploration = int(self._exploration.get_value())
        exclude_owned = bool(self._exclude_owned.get_active())

        def operation(control):
            def progress(done: int, total: int, detail: str) -> None:
                if generation != self._generation or control.cancelled():
                    return
                label = (
                    f"Recherche {done + 1}/{total} · {detail}"
                    if detail and done < total
                    else "Classement des résultats…"
                )
                self.GLib.idle_add(self._set_progress, generation, label)

            def partial(snapshot: RecommendationPartial) -> None:
                if generation != self._generation or control.cancelled():
                    return
                self.GLib.idle_add(self._accept_partial, generation, snapshot)

            if reference is not None:
                owned_snapshot = self._library_snapshot_cache.load(
                    metadata_broker,
                    identity_id,
                )
                return compute_reference_recommendations(
                    catalog_broker,
                    reference,
                    scope=self._config_str("scope", "digital"),
                    max_queries=self._config_int("max_queries", 8),
                    per_query=self._config_int("per_query", 50),
                    max_results=self._config_int("max_results", 120),
                    exploration=exploration,
                    owned_books=owned_snapshot.books,
                    exclude_owned=exclude_owned,
                    progress=progress,
                    partial=partial,
                    control=control,
                )
            return compute_recommendations(
                metadata_broker,
                catalog_broker,
                identity_id,
                scope=self._config_str("scope", "digital"),
                max_queries=self._config_int("max_queries", 8),
                per_query=self._config_int("per_query", 50),
                max_results=self._config_int("max_results", 120),
                exploration=exploration,
                exclude_owned=exclude_owned,
                snapshot_cache=self._library_snapshot_cache,
                progress=progress,
                partial=partial,
                control=control,
            )

        handle = task_broker.submit(
            operation,
            label="Calcul des recommandations",
            kind="recommendations",
        )
        self._task_handle = handle

        def finished(done_handle) -> None:
            if done_handle.control.cancelled():
                return
            try:
                result = done_handle.result()
            except Cancelled:
                return
            except Exception:
                # Provider and storage exceptions can include account IDs,
                # signed URLs and access tokens. The public UI only needs to
                # know that the calculation failed, never the raw exception.
                self.GLib.idle_add(self._complete, generation, None, "calculation_failed")
            else:
                self.GLib.idle_add(self._complete, generation, result, None)

        handle.add_done_callback(finished)
        return False

    def _show_calculation_unavailable(self, message: str) -> None:
        self._summary.set_label(message)
        master = getattr(self, "_master", None)
        if master is not None and not getattr(self, "_ranked", ()):
            master.set_list_state("Recommandations indisponibles", message, "dialog-warning-symbolic")
            master.empty_detail(
                "Aucune recommandation sélectionnée",
                "Les détails apparaîtront après un calcul réussi.",
            )

    def refresh(self):
        # Detached pages can still receive late identity/toolbar signals
        # after deactivate(). No task, list or action may be resurrected.
        if getattr(self, "_disposed", False):
            return False
        # A manual/profile refresh consumes any queued overview refresh because
        # this computation will already read the latest local projection.
        self._clear_overview_refresh()
        identity_id = self.selected_identity_id()
        profile_changed = (
            self._active_identity_id is not None
            and identity_id != self._active_identity_id
        )
        self._active_identity_id = identity_id
        self._provisional_order.clear()
        self._set_provisional(False)
        self._diagnostic_cache = 0
        self._diagnostic_network = 0
        self._set_catalogue_coverage()
        if hasattr(self, "_progress"):
            self._progress.set_tooltip_text(self._diagnostic_tooltip())

        if hasattr(self, "_master"):
            if profile_changed:
                self._ranked = []
                self._ranked_by_key = {}
                controls = getattr(self, "_query_controls", None)
                if controls is not None:
                    controls.set_choices(sources=(), languages=())
                    self._provider_labels.clear()
                self._reload_document_choices()
                self._master.show_rows((), total_count=0)
            if not self._ranked:
                self._master.set_list_state(
                    "Analyse de la bibliothèque…",
                    "Lecture du profil local, sans rescan des fichiers. Les premières propositions apparaîtront dès le premier catalogue.",
                    "system-search-symbolic",
                )
                self._master.empty_detail(
                    "Calcul en cours",
                    "Les détails seront disponibles dès la première recommandation.",
                )
            else:
                # Keep the local filtered count and attested facet labels
                # visible while a new calculation is pending. Replacing the
                # entire summary with a progress sentence obscured an exact
                # 1/N counter even though the old GTK rows stayed visible.
                # Preserve the richer unfiltered summary where applicable.
                self._update_view_summary(
                    provisional=False, preserve_unfiltered=True,
                )
                progress = (
                    "Recalcul en cours · résultats précédents conservés "
                    "jusqu’aux nouvelles propositions"
                )
                current = self._summary.get_label()
                if progress not in current:
                    self._summary.set_label(
                        f"{current} · {progress}" if current else progress
                    )

        value = self._start_refresh()
        if self._busy and not self._ranked:
            self._summary.set_label("Analyse de la bibliothèque…")
        return value

    def _set_progress(self, generation: int, label: str) -> bool:
        if getattr(self, "_disposed", False) or generation != self._generation:
            return False

        if label == "Classement des résultats…":
            self._progress.set_fraction(0.96)
            self._progress.set_text("96 % · Classement et diversification")
            return False

        match = re.match(r"Recherche\s+(\d+)/(\d+)\s+·\s*(.*)", label or "")
        if not match:
            return False

        current = max(1, int(match.group(1)))
        total = max(1, int(match.group(2)))
        detail = match.group(3)
        fraction = min(0.94, current / total * 0.94)

        profile = re.search(
            r"Profil prêt\s+·\s+(\d+) livres\s+·\s+(\d+) signaux",
            detail,
        )
        if profile:
            books = int(profile.group(1))
            signals = int(profile.group(2))
            self._profile_books_count = books
            self._progress.set_fraction(max(0.02, fraction))
            self._progress.set_text(
                f"{int(round(max(0.02, fraction) * 100))} % · "
                f"{books} livres analysés · {signals} signaux"
            )
            if not self._ranked:
                self._summary.set_label(
                    f"{books} livres analysés · recherche des recommandations…"
                )
                self._master.set_list_state(
                    f"{books} livres analysés",
                    "Recherche dans les catalogues connectés…",
                    "system-search-symbolic",
                )
            return False

        search = re.search(
            r"requête\s+(\d+)/(\d+).*catalogues\s+(\d+)/(\d+).*?(\d+) résultats",
            detail,
        )
        self._progress.set_fraction(fraction)
        if search:
            query_index, query_total, provider_done, provider_total, results_seen = (
                int(search.group(1)),
                int(search.group(2)),
                int(search.group(3)),
                int(search.group(4)),
                int(search.group(5)),
            )
            self._progress.set_text(
                f"{int(round(fraction * 100))} % · Recherche {query_index}/{query_total} · "
                f"{provider_done}/{provider_total} catalogues · {results_seen} résultats"
            )
        else:
            self._progress.set_text(
                f"{int(round(fraction * 100))} % · Recherche catalogues"
            )
        return False

    def _accept_partial(self, generation: int, snapshot: RecommendationPartial) -> bool:
        if generation != self._generation or not snapshot.ranked:
            return False
        self._pending_partial = snapshot
        if not self._partial_source_id:
            self._partial_source_id = self.GLib.timeout_add(
                self.PARTIAL_COALESCE_MS,
                self._flush_partial,
                generation,
            )
        return False

    def _flush_partial(self, generation: int) -> bool:
        self._partial_source_id = 0
        if generation != self._generation or self._pending_partial is None:
            return False
        snapshot: RecommendationPartial = self._pending_partial
        self._pending_partial = None

        self._diagnostic_cache = snapshot.cache_batches
        self._diagnostic_network = snapshot.network_batches
        self._progress.set_tooltip_text(self._diagnostic_tooltip())

        self._ranked = self._stable_partial(list(snapshot.ranked))
        # Rebuild the source facet *after* replacing the partial projection.
        # Prior connected catalogues with zero visible candidates disappear.
        self._reload_providers(snapshot.providers)
        self._reload_languages(self._ranked)
        self._set_provisional(True)
        self._reload_document_choices()

        units = (
            max(0, snapshot.query_index - 1) * max(1, snapshot.provider_total)
            + max(0, snapshot.provider_done)
        )
        total_units = max(
            1, snapshot.query_total * max(1, snapshot.provider_total)
        )
        fraction = min(0.94, units / total_units * 0.94)
        self._progress.set_fraction(fraction)
        self._progress.set_text(
            f"{int(round(fraction * 100))} % · Recherche "
            f"{snapshot.query_index}/{snapshot.query_total} · "
            f"{snapshot.provider_done}/{snapshot.provider_total} catalogues · "
            f"{snapshot.results_seen} résultats"
        )

        self._render(preserve_summary=True)
        self._update_view_summary(provisional=True)
        return False

    def _update_view_summary(
        self, *, provisional: bool, preserve_unfiltered: bool = False,
    ) -> None:
        if getattr(self, "_disposed", False):
            return
        total = len(self._ranked)
        visible = sum(1 for row in self._ranked if self._matches(row))
        if not total:
            return

        state = self._local_filter_state()
        if state is None:
            # _matches has hidden every row. Keep the count and a public,
            # closed diagnostic rather than serialize arbitrary filter values.
            text_filter, sources, languages = "", (), ()
            filters = ["Filtres locaux indisponibles"]
        else:
            text_filter, sources, languages = state
            filters = []
            if text_filter:
                filters.append(f"« {text_filter} »")
            filters.extend(self._display_source(source) for source in sources)
            filters.extend(_language_label(language) for language in languages)
        # The default threshold already informs initial ranking visibility.
        # Once edited it becomes a *local, reversible* facet and belongs in
        # the same visible count as Source/Format/Champ; no new provider I/O.
        default_threshold = getattr(self, "_affinity_initial_threshold", None)
        score_reader = getattr(getattr(self, "_score", None), "get_value", None)
        if type(default_threshold) is int and callable(score_reader):
            try:
                selected_threshold = score_reader()
                if (type(selected_threshold) in (int, float)
                        and 0 <= selected_threshold <= 100
                        and selected_threshold == int(selected_threshold)
                        and selected_threshold != default_threshold):
                    filters.append(f"Affinité ≥ {int(selected_threshold)}")
            except (TypeError, ValueError, OverflowError):
                pass
        # Same Core LocalQueryControls as Search: expose current format and
        # support choices in the visible counter. Absent/legacy hosts simply
        # omit the optional labels; this is presentation, not a new filter.
        controls = getattr(self, "_query_controls", None)
        describe_documents = getattr(controls, "active_document_labels", None)
        if callable(describe_documents):
            try:
                labels = describe_documents()
                if (type(labels) is tuple and len(labels) <= 32
                        and all(
                            type(value) is str and 0 < len(value) <= 120
                            and value.isprintable() for value in labels
                        )):
                    filters.extend(labels)
            except Exception:
                pass

        # A completed calculation already has a richer unfiltered summary
        # (profile books, signals and partial provider failures). Preserve it
        # unless the actual local view differs or a facet is actively set.
        # Otherwise a recalculation with retained filters misleadingly
        # announces the *total* as if every recommendation were visible.
        if preserve_unfiltered and not provisional and not filters and visible == total:
            return

        if filters or visible != total:
            parts = [f"{visible} sur {total}"]
        else:
            parts = [f"{total} recommandation(s)"]
        parts.extend(filters)
        if provisional:
            parts.append("Provisoire · ordre susceptible d’évoluer")
        else:
            parts.append("Ordre diversifié")
            warnings = getattr(self, "_partial_warning_count", 0)
            if warnings:
                parts.append(f"{warnings} recherche(s) partiellement en erreur")
        self._summary.set_label(" · ".join(parts))

    def _render(self, preserve_summary: bool = False) -> None:
        if getattr(self, "_disposed", False):
            return
        visible = [row for row in self._ranked if self._matches(row)]
        # Synchronize the view counter before materializing GTK rows: real
        # HiDPI reallocation and detail-selection callbacks can interrupt
        # presentation after show_rows has changed the list model. The
        # filtered count must not depend on successful detail rendering.
        # Preserve the richer calculation summary for provisional updates.
        if not preserve_summary and self._ranked:
            self._update_view_summary(provisional=self._provisional_active)
        catalogue_sources = self._selected_catalogue_sources()

        rows = []
        mapping = {}
        # Keep the original identities reserved, including those on *later*
        # rows: a generated suffix must never shadow a real edition key.
        # This matters when two providers legitimately return the same
        # candidate key or a key already ending in ":<index>".
        original_keys = tuple(
            self._candidate_key(ranked) or f"recommendation-{index}"
            for index, ranked in enumerate(visible)
        )
        reserved_keys = set(original_keys)
        for index, (ranked, original_key) in enumerate(zip(visible, original_keys)):
            item = ranked.item
            key = original_key
            if key in mapping:
                suffix = index
                key = f"{original_key}:{suffix}"
                while key in mapping or key in reserved_keys:
                    suffix += 1
                    key = f"{original_key}:{suffix}"
            mapping[key] = ranked

            author = ", ".join(_public_candidate_authors(item))
            source_labels = [
                label for source in _public_ranked_values(ranked.sources)
                if (not catalogue_sources or source in catalogue_sources)
                and (label := self._display_source(source))
            ]
            sources = " · ".join(dict.fromkeys(source_labels))
            reasons = " · ".join(_public_ranked_values(ranked.reasons))
            meta_parts = []
            if sources:
                meta_parts.append(sources)
            if reasons:
                meta_parts.append("Pourquoi : " + reasons)

            rows.append(
                UiBookListEntry(
                    key=key,
                    title=_candidate_public_field(item, "title", limit=2048) or "Sans titre",
                    subtitle=author,
                    meta=" · ".join(meta_parts),
                    badge=(f"Affinité {score}/100"
                           if (score := _public_affinity_score(ranked.score)) is not None
                           else ""),
                    thumbnail_url=_public_cover_url(item),
                )
            )

        selection = getattr(self, "_exact_notice_state", None)
        self._ranked_by_key = mapping
        self._master.show_rows(tuple(rows), total_count=len(self._ranked))

        # A local filter may hide the selected recommendation while other
        # rows remain. GTK does not always emit selection-changed on a model
        # replacement, so explicitly revoke the old detail and its callbacks.
        # Do not clear a NEW selection installed synchronously by show_rows.
        if (rows and selection is not None
                and self._exact_notice_state is selection
                and selection["key"] not in mapping):
            self._exact_notice_state = None
            self._detail_selection_generation = (
                getattr(self, "_detail_selection_generation", 0) + 1
            )
            self._reset_llm_selection(None)
            self._master.empty_detail(
                "Aucune recommandation sélectionnée",
                "La recommandation précédente ne correspond plus aux filtres.",
            )

        # The shared list can keep its selected GTK item without emitting a
        # signal when a progressive batch retains the same row keys. Refresh
        # the detail only if that selection still belongs to this snapshot.
        if (selection is not None and self._exact_notice_state is selection
                and selection["identity"] == self.selected_identity_id()):
            key = selection["key"]
            if key in mapping and (
                selection["ranked"] is not mapping[key]
                or selection.get("catalogue_sources") != catalogue_sources
                or (not self._busy and (
                    selection["generation"] != self._generation
                    or selection["provisional"] != self._provisional_active
                ))
            ):
                row = next(row for row in rows if row.key == key)
                self._reset_llm_selection(None)
                self._selected_row(row)

        if not rows:
            self._exact_notice_state = None
            # Empty filters invalidate prior detail and pending LLM callbacks,
            # even if the host emits no selection-changed signal.
            self._detail_selection_generation = (
                getattr(self, "_detail_selection_generation", 0) + 1
            )
            self._reset_llm_selection(None)
            if self._ranked:
                self._master.set_list_state(
                    "Aucun résultat pour ces filtres",
                    "Modifiez les filtres pour afficher les recommandations déjà calculées.",
                )
            self._master.empty_detail(
                "Aucune recommandation sélectionnée",
                "Modifiez les filtres ou relancez le calcul.",
            )

    def _selected_row(self, row: UiBookListEntry | None) -> None:
        if getattr(self, "_disposed", False):
            return
        # Invalidate all callbacks from prior presentations, even on an
        # A→B→A revisit or when two editions reuse the same list key.
        self._detail_selection_generation = (
            getattr(self, "_detail_selection_generation", 0) + 1
        )
        self._reset_llm_selection(row.key if row is not None else None)
        if row is None:
            self._exact_notice_state = None
            self._master.empty_detail(
                "Aucune recommandation sélectionnée",
                "Sélectionnez un livre dans la liste pour afficher ses détails.",
            )
            return
        ranked = self._ranked_by_key.get(row.key)
        if ranked is None:
            self._exact_notice_state = None
            # A fast filter refresh can invalidate the selected row before
            # GTK delivers its selection callback. Never leave the previous
            # book's detail and actions attached to a stale list identity.
            self._reset_llm_selection(None)
            self._master.empty_detail(
                "Notice indisponible",
                "Cette recommandation n’est plus dans les résultats du profil.",
            )
            return
        # Page state is scoped to the current recommendation, identity and
        # ranking generation. Returning A→B→A or switching profiles must
        # never reopen an old page or resurrect a clickable stale action.
        exact_identity = self.selected_identity_id()
        catalogue_sources = self._selected_catalogue_sources()
        state = getattr(self, "_exact_notice_state", None)
        if not (
            type(state) is dict
            and state.get("key") == row.key
            and state.get("ranked") is ranked
            and state.get("identity") == exact_identity
            and state.get("catalogue_sources") == catalogue_sources
            and state.get("generation") == self._generation
        ):
            state = {
                "key": row.key, "ranked": ranked,
                "identity": exact_identity, "generation": self._generation,
                "catalogue_sources": catalogue_sources, "page": 0,
            }
            self._exact_notice_state = state
        state["provisional"] = self._provisional_active
        self._llm_selected_row = row
        if self._llm_button is not None:
            self._llm_button.set_sensitive(not self._busy and self._llm_handle is None)

        item = ranked.item
        title = _candidate_public_field(item, "title", limit=2048) or "Sans titre"
        authors = _public_candidate_authors(item)
        author = ", ".join(authors)
        fmt = _candidate_public_field(item, "format")
        published = _candidate_public_field(item, "published_at")
        reasons = " · ".join(_public_ranked_values(ranked.reasons))
        source_labels = tuple(dict.fromkeys(
            label for source in _public_ranked_values(ranked.sources)
            if (not catalogue_sources or source in catalogue_sources)
            and (label := self._display_source(source))
        ))
        description = _public_candidate_description(item)

        verified_score = _public_affinity_score(ranked.score)
        recommendation_fields = [
            DetailField(
                "affinity", "Affinité",
                f"{verified_score}/100" if verified_score is not None
                else "Non attestée",
            ),
            DetailField(
                "ranking",
                "Classement",
                "Ordre diversifié : l’affinité n’est pas l’unique clé de tri.",
            ),
            DetailField(
                "reasons",
                "Pourquoi",
                reasons or "Aucun motif attesté disponible",
            ),
        ]
        catalogue_fields = [
            DetailField(f"author-{index}", "Auteur", value, kind="author")
            for index, value in enumerate(authors)
        ]
        # Each attested provider remains an independent, copyable value,
        # like provenance in Youth and Constellations. Flattening sources
        # into a single string loses their individual bibliographic meaning.
        for index, source in enumerate(source_labels):
            catalogue_fields.append(DetailField(
                f"source-{index}", "Source", source, kind="copy",
            ))
        # Use the exact same normalized, source-attested values as the
        # existing language filter. A recommendation never invents a
        # language merely from its title or provider.
        for index, language in enumerate(_candidate_languages(item)):
            catalogue_fields.append(DetailField(
                f"language-{index}", "Langue", language, kind="language",
            ))
        if fmt:
            catalogue_fields.append(DetailField("format", "Format", fmt, kind="all"))
        if published:
            catalogue_fields.append(
                DetailField("published", "Publication", published)
            )

        sections = [
            DetailSection(
                "recommendation",
                "Recommandation",
                tuple(recommendation_fields),
                10,
            )
        ]
        if catalogue_fields:
            sections.append(
                DetailSection("catalogue", "Catalogue", tuple(catalogue_fields), 20)
            )
        if description:
            sections.append(
                DetailSection(
                    "notice",
                    "Notice",
                    (DetailField("description", "Description", description),),
                    30,
                )
            )

        # Ranks may aggregate editions and accounts from several providers.
        # Show only accountable, originally observed variants. Never display
        # credential IDs, private record IDs or infer borrowing availability.
        raw_variants = getattr(ranked, "notices", ())
        # This is a bounded LOCAL inspection of public catalogue coordinates,
        # never a network search. The historical first-64 slice could hide
        # every valid edition behind 64 incomplete catalogue variants.
        variants = (
            tuple(raw_variants[:256])
            if type(raw_variants) in (tuple, list) else ()
        )
        # ISBN is not a provider-record identifier: one exact notice may be
        # present with several ISBNs. Deduplicate on the 5 coordinates that
        # Core's real Search detail uses, keeping credentials distinct.
        exact_variants = []
        seen_coordinates = set()
        for variant in variants:
            if type(variant) is not CatalogNoticeVariant:
                continue
            # The source MultiSelect is a view filter. It must also constrain
            # the exact notice actions, not just keep the aggregated row.
            if catalogue_sources and variant.source_provider not in catalogue_sources:
                continue
            coordinates = (
                variant.source_provider, variant.source_credential,
                variant.record_id, variant.docbase, variant.source_collection,
            )
            if (any(type(value) is not str or len(value) > 512
                    for value in coordinates)
                    or not all(coordinates[:3])
                    or coordinates in seen_coordinates):
                continue
            seen_coordinates.add(coordinates)
            # At most 256 original variants were inspected. Retain all
            # validated distinct coordinates for explicit 16-item pages;
            # do not load more data or inspect further variants on click.
            exact_variants.append(variant)
        page_size = 16
        page_count = max(1, (len(exact_variants) + page_size - 1) // page_size)
        page_index = state["page"]
        if type(page_index) is not int or not 0 <= page_index < page_count:
            page_index = 0
            state["page"] = 0
        page_start = page_index * page_size
        visible_exact = exact_variants[page_start:page_start + page_size]
        hidden_variants = (
            type(raw_variants) in (tuple, list)
            and len(raw_variants) > 256
        )
        if exact_variants or hidden_variants:
            edition_fields = []
            for index, variant in enumerate(visible_exact, page_start + 1):
                name = self._display_source(variant.source_provider)
                valid_isbns = _attested_catalogue_isbns((variant,))
                isbn = valid_isbns[0] if valid_isbns else ""
                edition_fields.append(DetailField(
                    f"source-edition-{index}", f"Notice source {index}",
                    f"{name} · ISBN {isbn}" if isbn else
                    f"{name} · édition non attestée",
                ))
            if visible_exact:
                edition_fields.append(DetailField(
                    "source-edition-warning", "Actions disponibles",
                    "Choisir une notice source exacte : consulter ici ses "
                    "actions lorsqu’elles sont qualifiées, ou utiliser Recherche.",
                ))
            if len(exact_variants) > page_size:
                edition_fields.append(DetailField(
                    "source-edition-page", "Notices affichées",
                    f"{page_start + 1}–{page_start + len(visible_exact)} "
                    f"sur {len(exact_variants)} notices distinctes, "
                    f"page {page_index + 1}/{page_count}.",
                ))
            if hidden_variants:
                edition_fields.append(DetailField(
                    "source-edition-limit", "Portée non exhaustive",
                    "Seules les 256 premières variantes ont été examinées. "
                    "La liste n'est pas exhaustive : d'autres éditions "
                    "peuvent exister.",
                ))
            sections.append(DetailSection(
                "source-editions", "Éditions et sources distinctes",
                tuple(edition_fields), 35,
            ))
        if self._llm_enabled and self._llm_explanation is not None and self._llm_selection_key == row.key:
            explanation = self._llm_explanation
            sections.append(DetailSection(
                "llm-explanation", "Reformulation générée — à vérifier",
                (
                    DetailField("llm-wording", "Texte généré (non attesté)", explanation.text),
                    DetailField(
                        "llm-provenance", "Provenance du traitement",
                        f"{explanation.provider} / {explanation.model} · "
                        + ("service distant" if explanation.remote else "service local")
                        + f" · {explanation.elapsed_ms} ms",
                    ),
                ),
                25,
            ))

        actions = []
        # This is an explicit local ISBN *search*, not a silent remote fetch
        # or an exact catalogue action. Search owns the real availability and
        # acquisition decision; Recommandations never duplicates that policy.
        identity_at_selection = self.selected_identity_id()
        generation_at_selection = self._generation
        detail_generation_at_selection = self._detail_selection_generation
        key_at_selection = row.key
        ranked_at_selection = ranked
        if identity_at_selection:
            # Do not fabricate borrowing/downloading actions in this plugin.
            # Only an EXACT notice already loaded by Core Search may be
            # selected, on an explicit click, through the SAME DetailPanel
            # that resolves rights and actions for a normal Search selection.
            can_open = getattr(self.host, "can_open_loaded_search_notice", None)
            open_loaded = getattr(self.host, "open_loaded_search_notice", None)
            can_cached = getattr(self.host, "can_open_known_search_notice", None)
            open_cached = getattr(self.host, "open_known_search_notice", None)
            in_place = getattr(
                self.host, "present_loaded_search_detail_in_place", None,
            )
            known_in_place = getattr(
                self.host, "present_known_search_detail_in_place", None,
            )
            if ((callable(can_open) and callable(open_loaded))
                    or (callable(can_cached) and callable(open_cached))):
                for index, variant in enumerate(visible_exact, page_start + 1):
                    coordinates = {
                        "provider": variant.source_provider,
                        "credential": variant.source_credential,
                        "record_id": variant.record_id,
                        "docbase": variant.docbase,
                        "source_collection": variant.source_collection,
                        "identity_id": identity_at_selection,
                    }
                    # Prefer the exact already-loaded Search row. Only if
                    # absent, Core may identify ONE cache-qualified v2 notice;
                    # the recommendation contributes coordinates, not rights.
                    destination = None
                    for probe, opener, mode in (
                        (can_open, open_loaded, "loaded"),
                        (can_cached, open_cached, "known"),
                    ):
                        if not (callable(probe) and callable(opener)):
                            continue
                        try:
                            if probe(**coordinates) is True:
                                destination = (opener, mode)
                                break
                        except Exception:
                            continue
                    if destination is None:
                        continue
                    dispatch, mode = destination

                    def open_exact(coordinates=coordinates, dispatch=dispatch):
                        if (
                            getattr(self, "_disposed", False)
                            or self._generation != generation_at_selection
                            or self._detail_selection_generation != detail_generation_at_selection
                            or self._selected_catalogue_sources() != catalogue_sources
                            or self._llm_selection_key != key_at_selection
                            or self._ranked_by_key.get(key_at_selection) is not ranked_at_selection
                            or self.selected_identity_id() != identity_at_selection
                        ):
                            return False
                        try:
                            return dispatch(**coordinates) is True
                        except Exception:
                            return False

                    actions.append(UiBookDetailAction(
                        f"recommendation.{mode}-notice.{index}",
                        (f"Voir les actions de la notice {index} dans Recherche"
                         if mode == "loaded" else
                         f"Consulter la notice {index} dans Recherche")
                        + f" · {self._display_source(variant.source_provider)}",
                        "document-open-symbolic", callback=open_exact,
                    ))
                    # #4664: exact loaded Search actions may now be displayed
                    # in this very panel on an explicit click. Core retrieves
                    # the SearchResult and ItemDetail and builds all provider
                    # actions; recommendation scores cannot grant rights.
                    presenter = in_place if mode == "loaded" else known_in_place
                    if callable(presenter):
                        def in_place_current():
                            return (
                                not getattr(self, "_disposed", False)
                                and self._generation == generation_at_selection
                                and self._detail_selection_generation
                                    == detail_generation_at_selection
                                and self._selected_catalogue_sources() == catalogue_sources
                                and self._llm_selection_key == key_at_selection
                                and self._ranked_by_key.get(key_at_selection)
                                    is ranked_at_selection
                                and self.selected_identity_id()
                                    == identity_at_selection
                                and self._exact_notice_state is state
                                and state["page"] == page_index
                            )

                        def show_in_place(
                            coordinates=coordinates, presenter=presenter,
                            context_sections=tuple(sections),
                            still_selected=in_place_current,
                        ):
                            if not still_selected():
                                return False
                            try:
                                return presenter(
                                    view=self._master,
                                    context_sections=context_sections,
                                    is_current=still_selected,
                                    **coordinates,
                                ) is True
                            except Exception:
                                return False

                        actions.append(UiBookDetailAction(
                            f"recommendation.{mode}-in-place.{index}",
                            (f"Consulter disponibilités et actions de la notice "
                             f"{index} ici · "
                             f"{self._display_source(variant.source_provider)}"),
                            "document-open-symbolic",
                            callback=show_in_place,
                        ))
            # Pure-local pagination of exact source choices. A callback from
            # the previous page, a replaced ranking or another profile cannot
            # change the active detail. Core remains the sole action resolver.
            if page_count > 1:
                def change_notice_page(next_page):
                    if (
                        getattr(self, "_disposed", False)
                        or self._generation != generation_at_selection
                        or self._detail_selection_generation != detail_generation_at_selection
                        or self._selected_catalogue_sources() != catalogue_sources
                        or self._llm_selection_key != key_at_selection
                        or self._ranked_by_key.get(key_at_selection) is not ranked_at_selection
                        or self.selected_identity_id() != identity_at_selection
                        or self._exact_notice_state is not state
                        or state["page"] != page_index
                        or type(next_page) is not int
                        or not 0 <= next_page < page_count
                    ):
                        return False
                    state["page"] = next_page
                    self._selected_row(row)
                    return True

                if page_index > 0:
                    actions.append(UiBookDetailAction(
                        "recommendation.exact-notice.previous",
                        f"Notices précédentes · page {page_index}/{page_count}",
                        "go-previous-symbolic",
                        callback=lambda: change_notice_page(page_index - 1),
                    ))
                if page_index + 1 < page_count:
                    actions.append(UiBookDetailAction(
                        "recommendation.exact-notice.next",
                        f"Notices suivantes · page {page_index + 2}/{page_count}",
                        "go-next-symbolic",
                        callback=lambda: change_notice_page(page_index + 1),
                    ))
            verified_isbns = _attested_catalogue_isbns(exact_variants)
            for isbn in verified_isbns:
                def open_isbn(isbn=isbn):
                    if (
                        self._generation != generation_at_selection
                        or self._detail_selection_generation != detail_generation_at_selection
                        or self._selected_catalogue_sources() != catalogue_sources
                        or self._llm_selection_key != key_at_selection
                        or self._ranked_by_key.get(key_at_selection) is not ranked_at_selection
                        or self.selected_identity_id() != identity_at_selection
                    ):
                        return False
                    search = getattr(self.host, "search_books", None)
                    if not callable(search):
                        return False
                    return bool(search(
                        isbn, field="isbn", online=False,
                        identity_id=identity_at_selection,
                    ))
                actions.append(UiBookDetailAction(
                    f"recommendation.search.isbn.{isbn}",
                    f"Chercher l’édition ISBN {isbn} dans Recherche",
                    "system-search-symbolic", callback=open_isbn,
                ))
            # When distinct providers contributed attested editions, offer
            # explicit provider-local Search routes, not synthetic borrow /
            # download buttons. Core validates the active provider and owns
            # the standard details/actions after the user selects a result.
            verified_sources = tuple(dict.fromkeys(
                (variant.source_provider, _attested_catalogue_isbns((variant,))[0])
                for variant in visible_exact
                if (_attested_catalogue_isbns((variant,))
                    and type(variant.source_provider) is str
                    and re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}",
                                     variant.source_provider))
            ))
            if len({provider for provider, _isbn in verified_sources}) > 1:
                for provider, isbn in verified_sources[:8]:
                    def open_source(provider=provider, isbn=isbn):
                        if (
                            self._generation != generation_at_selection
                            or self._detail_selection_generation != detail_generation_at_selection
                            or self._selected_catalogue_sources() != catalogue_sources
                            or self._llm_selection_key != key_at_selection
                            or self._ranked_by_key.get(key_at_selection) is not ranked_at_selection
                            or self.selected_identity_id() != identity_at_selection
                        ):
                            return False
                        search = getattr(self.host, "search_books", None)
                        if not callable(search):
                            return False
                        return bool(search(
                            isbn, field="isbn", online=False,
                            identity_id=identity_at_selection,
                            providers=(provider,),
                        ))
                    actions.append(UiBookDetailAction(
                        f"recommendation.search.provider.{provider}.{isbn}",
                        f"Chercher cette édition dans {self._display_source(provider)}",
                        "system-search-symbolic", callback=open_source,
                    ))
            # A source may publish no validated ISBN or direct HTML page.
            # Preserve a *local, approximate* exploration action rather
            # than inventing a provider-specific detail/loan/download.
            # Never invoke the network or query another identity.
            if (not verified_isbns and visible_exact
                    and 2 <= len(title) <= 160 and title != "Sans titre"):
                title_query = title

                def search_title():
                    if (
                        self._generation != generation_at_selection
                        or self._detail_selection_generation != detail_generation_at_selection
                        or self._selected_catalogue_sources() != catalogue_sources
                        or self._llm_selection_key != key_at_selection
                        or self._ranked_by_key.get(key_at_selection) is not ranked_at_selection
                        or self.selected_identity_id() != identity_at_selection
                    ):
                        return False
                    search = getattr(self.host, "search_books", None)
                    if not callable(search):
                        return False
                    return bool(search(
                        title_query, field="title", online=False,
                        identity_id=identity_at_selection,
                    ))

                actions.append(UiBookDetailAction(
                    "recommendation.search.title.local",
                    "Chercher ce titre dans mes notices (approché)",
                    "system-search-symbolic", callback=search_title,
                ))
        # An unsafe/missing friendly link must not mask an independently
        # attested public notice. Both candidates pass the same strict
        # validation; no download, token or private URL reaches CoreUI.
        url = _public_recommendation_notice(item)
        if url:
            actions.append(
                UiBookDetailAction(
                    "recommendation.online",
                    "Voir en ligne",
                    "external-link-symbolic",
                    uri=url,
                )
            )

        thumbnail = _public_cover_url(item)
        badges = (
            ([f"Affinité {verified_score}/100"] if verified_score is not None else [])
            + list(source_labels)
        )
        if self._provisional_active:
            badges.append("Provisoire")

        self._master.show_detail(
            UiBookDetailPresentation(
                title=title,
                subtitle=author,
                thumbnail_url=thumbnail,
                badges=tuple(badges),
                sections=tuple(sections),
                actions=tuple(actions),
            )
        )

    def _reset_llm_selection(self, key) -> None:
        """Cancel old explanation on new book, profile or ranking generation."""
        if key == self._llm_selection_key:
            return
        self._llm_generation += 1
        handle = self._llm_handle
        if handle is not None:
            handle.cancel()
        self._llm_handle = None
        self._llm_selection_key = key
        self._llm_selected_row = None
        self._llm_explanation = None
        if self._llm_button is not None:
            self._llm_button.set_sensitive(False)
        if self._llm_hint is not None:
            self._llm_hint.set_label(
                "IA facultative : sélectionnez un livre. Aucun envoi sans clic."
                if key is None else
                "Raisons attestées disponibles. Cliquez pour reformuler (service potentiellement distant)."
            )

    def _explain_selected(self) -> None:
        """Explicit one-shot async wording; never changes ranking or source rows."""
        if not self._llm_enabled or self._llm_handle is not None or self._busy:
            return
        key, row = self._llm_selection_key, self._llm_selected_row
        ranked = self._ranked_by_key.get(key) if key is not None else None
        context = self.plugin.context
        if row is None or ranked is None or context is None:
            return
        service = context.service("llm")
        task_broker = context.broker("tasks")
        if service is None or task_broker is None:
            self._llm_hint.set_label("Service IA non configuré. Les raisons originales restent disponibles.")
            return
        # The public detail already rejects poisoned cache reasons. Reuse
        # that exact projection before sending anything to a potentially
        # remote LLM. tuple(mapping) would otherwise turn private field keys
        # into prompt content, even though the detail never displayed them.
        reasons = _public_ranked_values(getattr(ranked, "reasons", ()))[:3]
        if not reasons:
            self._llm_hint.set_label(
                "Aucune raison publique exploitable pour une reformulation."
            )
            return
        self._llm_generation += 1
        token, main_generation = self._llm_generation, self._generation
        self._llm_button.set_sensitive(False)
        self._llm_hint.set_label("Reformulation demandée… Les raisons originales restent inchangées.")
        try:
            handle = task_broker.submit(
                lambda control: explain_reasons(
                    service, reasons, enabled=True, consent=True, control=control,
                ),
                label="Reformulation explicative des recommandations",
                kind="recommendations.llm",
            )
        except Exception:
            self._llm_hint.set_label("Service IA indisponible. Le classement est inchangé.")
            self._llm_button.set_sensitive(True)
            return
        self._llm_handle = handle

        def finished(done_handle):
            if done_handle.control.cancelled():
                return
            try:
                explanation = done_handle.result()
            except Exception:
                explanation = None

            def apply():
                if (self.plugin.context is None or token != self._llm_generation
                        or main_generation != self._generation
                        or key != self._llm_selection_key):
                    return False
                self._llm_handle = None
                self._llm_explanation = explanation
                self._llm_button.set_sensitive(True)
                self._llm_hint.set_label(
                    "Reformulation générée et signalée dans le détail. À vérifier."
                    if explanation is not None else
                    "Aucune reformulation disponible. Les preuves d'origine restent affichées."
                )
                if self._llm_selected_row is not None:
                    self._selected_row(self._llm_selected_row)
                return False

            self.GLib.idle_add(apply)

        handle.add_done_callback(finished)

    def _base_complete(self, generation: int, result: RecommendationResult | None, error: str | None) -> bool:
        if generation != self._generation:
            return False
        self._busy = False
        self._partial_warning_count = len(result.warnings) if result is not None and not error else 0
        self._set_provisional(False)
        self._refresh_button.set_sensitive(True)
        self._spinner.stop()
        self._set_catalogue_coverage(result if error is None else None)
        if error or result is None:
            self._ranked = []
            # The old result's Source/Langue facets are no longer available
            # after a failed calculation, just like its Format/Support facets.
            if (getattr(self, "_query_controls", None) is not None
                    or getattr(self, "_provider", None) is not None):
                self._reload_providers(())
            if (getattr(self, "_query_controls", None) is not None
                    or getattr(self, "_language", None) is not None):
                self._reload_languages(())
            self._reload_document_choices()
            # An error may originate in an untrusted provider or backend.
            # Never reflect raw provider messages into the GTK status/toast.
            message = "Calcul impossible. Réessayez ou vérifiez les catalogues connectés."
            self._render()
            self._show_calculation_unavailable(message)
            try:
                self.host.toast(message)
            except Exception:
                pass
            return False

        self._ranked = list(result.ranked)
        self._reload_providers(result.providers)
        self._reload_languages(self._ranked)
        self._reload_document_choices()
        if self._reference_book is None and result.profile_books:
            self._reload_reference_books(result.profile_books)
        empty_reason = None
        if not result.books_count:
            empty_reason = "Le profil ne contient aucun livre exploitable."
        elif not result.providers:
            empty_reason = "Aucun catalogue connecté ne permet la recherche."
        elif not result.queries:
            empty_reason = "Pas assez de métadonnées pour construire des recommandations."
        elif not result.ranked:
            empty_reason = "Aucune recommandation trouvée avec les catalogues actuellement connectés."
        else:
            suffix = f" · {len(result.warnings)} recherche(s) partiellement en erreur" if result.warnings else ""
            if self._reference_book is not None:
                self._summary.set_label(
                    f"{len(result.ranked)} recommandations proches de « {_reference_label(self._reference_book)} » "
                    f"· {len(result.queries)} signaux{suffix}"
                )
            else:
                self._summary.set_label(f"{len(result.ranked)} recommandations à partir de {result.books_count} livres et {len(result.queries)} signaux{suffix}")
        self._render(preserve_summary=True)
        if self._ranked:
            self._update_view_summary(
                provisional=False, preserve_unfiltered=True,
            )
        if empty_reason is not None:
            self._summary.set_label(empty_reason)
            if not self._ranked:
                self._master.set_list_state("Aucune recommandation", empty_reason)
        return False

    def _progress_complete(self, generation, result, error):
        value = self._base_complete(generation, result, error)
        if generation != self._generation:
            return value
        if error or result is None:
            self._progress.set_text("Interrompu")
            return value
        self._progress.set_fraction(1.0)
        self._progress.set_text("100 % · Terminé")
        if result.ranked:
            suffix = f" · cache {result.cache_batches} lot(s) · réseau {result.network_batches} lot(s)"
            if result.cache_unknown_calls:
                suffix += f" · {result.cache_unknown_calls} cache(s) non observables"
            self._summary.set_label(self._summary.get_label() + suffix)
        return value

    def _streaming_complete(
        self,
        generation: int,
        result: RecommendationResult | None,
        error: str | None,
    ):
        if generation != self._generation:
            return False
        self._task_handle = None
        self._pending_partial = None
        if self._partial_source_id:
            try:
                self.GLib.source_remove(self._partial_source_id)
            except Exception:
                pass
            self._partial_source_id = 0
        return self._progress_complete(generation, result, error)

    def _complete(
        self,
        generation: int,
        result: RecommendationResult | None,
        error: str | None,
    ):
        apply_started = time.perf_counter()
        value = self._streaming_complete(generation, result, error)
        if generation != self._generation:
            return value

        apply_ms = (time.perf_counter() - apply_started) * 1000.0
        if error is None and result is not None:
            total_ui_ms = (
                (time.perf_counter() - self._refresh_started_at) * 1000.0
                if self._refresh_started_at is not None
                else 0.0
            )
            _LOG.info(
                "TRACE recommendations ui books=%s results=%s apply_ms=%.3f total_ui_ms=%.3f",
                result.books_count,
                len(result.ranked),
                apply_ms,
                total_ui_ms,
            )
        self._refresh_started_at = None

        if error or result is None:
            if self._overview_refresh_pending:
                self._schedule_overview_refresh()
            return value

        self._diagnostic_cache = result.cache_batches
        self._diagnostic_network = result.network_batches
        self._progress.set_tooltip_text(self._diagnostic_tooltip())

        if result.ranked:
            # Gtk.ListView/Gio.ListStore is virtualized: once the model is
            # reconciled there is no second "build all row widgets" phase.
            self._progress.set_fraction(1.0)
            self._progress.set_text("100 % · Terminé")
            self._update_view_summary(provisional=False)
        if self._overview_refresh_pending:
            self._schedule_overview_refresh()
        return value

    def _base_dispose(self) -> None:
        self._generation += 1
        if callable(self._unsubscribe):
            try:
                self._unsubscribe()
            except Exception:
                pass
        self._unsubscribe = None

    def dispose(self) -> None:
        # Set before cancelling work: idle callbacks already queued may still
        # execute after deactivation, even when their sources are removed.
        self._disposed = True
        # Do not retain ranking/identity-bound source choices after teardown.
        self._exact_notice_state = None
        self._reset_llm_selection(None)
        self._clear_overview_refresh()
        if self._task_handle is not None:
            self._task_handle.cancel()
        self._task_handle = None
        self._pending_partial = None
        self._render_generation += 1
        if self._partial_source_id:
            try:
                self.GLib.source_remove(self._partial_source_id)
            except Exception:
                pass
            self._partial_source_id = 0
        self._base_dispose()

def _refresh_on_first_map(view, widget, GLib):
    """Schedule exactly one initial refresh when the page becomes visible."""
    scheduled = False

    def refresh_if_active():
        if getattr(view, "_disposed", False):
            return False
        view.refresh()
        return False

    def on_map(_widget):
        nonlocal scheduled
        if scheduled or getattr(view, "_disposed", False):
            return
        scheduled = True
        GLib.idle_add(refresh_if_active)

    widget.connect("map", on_map)
    get_mapped = getattr(widget, "get_mapped", None)
    if callable(get_mapped) and get_mapped():
        on_map(widget)

PLUGIN = RecommendationsPlugin()
