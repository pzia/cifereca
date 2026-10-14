from __future__ import annotations

import logging
import re
import time
from ipaddress import ip_address
from typing import Any, Mapping
from urllib.parse import urlsplit

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

from recommendation_engine import RankedCandidate, author_values, candidate_key, get_field, normalize
from recommendation_service import LibrarySnapshotCache, RecommendationPartial, RecommendationResult, compute_recommendations
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

def _reference_label(book: Any) -> str:
    title = str(get_field(book, "title", "") or "").strip() or "Sans titre"
    authors = author_values(book)
    author = authors[0] if authors else ""
    return f"{title} — {author}" if author else title


def _reference_selection_key(book: Any) -> str:
    title = normalize(get_field(book, "title", ""))
    authors = sorted(normalize(author) for author in author_values(book) if normalize(author))
    return f"{title}|{'/'.join(authors)}"


def _external_book_url(value: Any) -> str | None:
    url = str(value or "").strip()
    if not url or any(char.isspace() for char in url):
        return None
    try:
        parsed = urlsplit(url)
        _ = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        # A signed acquisition URL, referral token or fragment is not an
        # attested public book notice. Never reflect it in an external action.
        return None
    host = parsed.hostname.lower().rstrip(".")
    if (
        not host or "." not in host
        or host == "localhost" or host.endswith((".local", ".localhost", ".internal"))
        or any(ord(ch) < 33 or ord(ch) == 127 for ch in host)
    ):
        return None
    try:
        address = ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return None
    if "%" in parsed.path and re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|7f)", parsed.path, re.I):
        # Do not let encoded control characters round-trip through the browser
        # or into a book-action link handed off to a helper.
        return None
    return url


def _attested_catalogue_isbns(notices: Any) -> tuple[str, ...]:
    """Edition selectors, NOT borrowing permissions or exact item actions.

    An ISBN here was checked by the recommendation engine. Recheck its form
    and require original provider/credential/record coordinates so that a
    merged title or unattributed ISBN never becomes an actionable source.
    """
    selections: list[str] = []
    for notice in tuple(notices or ())[:64]:
        if not getattr(notice, "has_source_coordinates", False):
            continue
        isbn = getattr(notice, "isbn", "")
        if type(isbn) is not str or not re.fullmatch(r"97[89][0-9]{10}", isbn):
            continue
        if sum(
            int(digit) * (1 if index % 2 == 0 else 3)
            for index, digit in enumerate(isbn)
        ) % 10:
            continue
        if isbn not in selections:
            selections.append(isbn)
        if len(selections) == 4:
            break
    return tuple(selections)


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
    metadata = get_field(item, "metadata", {}) or {}
    values = []
    for raw in (
        get_field(item, "language", None),
        get_field(item, "languages", None),
        metadata.get("language") if isinstance(metadata, Mapping) else None,
        metadata.get("languages") if isinstance(metadata, Mapping) else None,
    ):
        if isinstance(raw, (list, tuple, set)):
            # Catalogue metadata is untrusted: nested maps, numbers and
            # foreign objects are not language tags. Never stringify them.
            values.extend(item for item in raw if isinstance(item, str))
        elif isinstance(raw, str) and raw.strip():
            values.append(raw)

    normalized = []
    for raw in values:
        canonical = normalize_language_tag(raw)
        if canonical and canonical not in normalized:
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

        controls = Gtk.FlowBox()
        controls.set_selection_mode(Gtk.SelectionMode.NONE)
        controls.set_row_spacing(7)
        controls.set_column_spacing(7)
        controls.set_max_children_per_line(8)

        self._identity_model = Gtk.StringList.new([])
        self._identity = Gtk.DropDown(model=self._identity_model)
        self._identity.set_tooltip_text("Profil utilisé comme point de départ")
        self._identity.connect("notify::selected", self._identity_changed)
        controls.insert(self._labeled("Profil", self._identity), -1)

        self._reference_model = Gtk.StringList.new(["Bibliothèque entière"])
        self._reference = Gtk.DropDown(model=self._reference_model)
        self._reference.set_sensitive(False)
        self._reference.set_tooltip_text(
            "Point de départ : bibliothèque entière ou livre précis de ce profil"
        )
        self._reference.connect("notify::selected", self._reference_changed)
        controls.insert(self._labeled("Référence", self._reference), -1)

        self._provider_model = Gtk.StringList.new(["Tous les catalogues"])
        self._provider = Gtk.DropDown(model=self._provider_model)
        self._provider.set_tooltip_text("Filtre les recommandations déjà calculées")
        self._provider.connect("notify::selected", lambda *_: self._render())
        controls.insert(self._labeled("Catalogue", self._provider), -1)

        self._language_model = Gtk.StringList.new(["Toutes"])
        self._language = Gtk.DropDown(model=self._language_model)
        self._language.set_tooltip_text(
            "Filtre sur les langues explicitement fournies par les catalogues ; "
            "« Toutes » conserve aussi les ouvrages sans langue connue"
        )
        self._language.connect("notify::selected", self._language_changed)
        controls.insert(self._labeled("Langue", self._language), -1)

        self._text = Gtk.SearchEntry(placeholder_text="Titre ou auteur")
        self._text.connect("search-changed", lambda *_: self._render())
        controls.insert(self._labeled("Filtrer", self._text), -1)

        self._exclude_owned = Gtk.CheckButton(label="Exclure les possédés")
        self._exclude_owned.set_active(self._config_bool("exclude_owned", True))
        self._exclude_owned.set_tooltip_text(
            "Masque par défaut les livres déjà présents dans la bibliothèque de ce profil. "
            "Décochez pour les réafficher volontairement."
        )
        self._exclude_owned.connect("toggled", lambda *_: self.refresh())
        controls.insert(self._exclude_owned, -1)

        score_adjustment = Gtk.Adjustment(
            lower=0, upper=100, step_increment=5, page_increment=10,
            value=self._config_int("minimum_score", 15),
        )
        self._score = Gtk.SpinButton(adjustment=score_adjustment, climb_rate=1, digits=0)
        self._score.set_tooltip_text(_AFFINITY_MIN_TOOLTIP)
        self._score.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Seuil minimal d’affinité des recommandations"],
        )
        self._score.connect("value-changed", lambda *_: self._render())
        controls.insert(self._labeled("Affinité ≥", self._score), -1)

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
        controls.insert(self._labeled("Diversité", self._exploration), -1)

        self._refresh_button = Gtk.Button(label="Recalculer", icon_name="view-refresh-symbolic")
        self._refresh_button.set_tooltip_text("Relancer le calcul en conservant les filtres de vue")
        self._refresh_button.connect("clicked", lambda *_: self.refresh())
        controls.insert(self._refresh_button, -1)

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
            controls.insert(self._llm_button, -1)
        toolbox.append(controls)
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

        self._master.append_toolbar(toolbox)
        self._master.empty_detail(
            "Aucune recommandation sélectionnée",
            "Sélectionnez un livre dans la liste pour afficher la notice et les raisons de la recommandation.",
        )

        self._reload_identities()
        subscribe = getattr(host, "subscribe_identity_overview", None)
        if callable(subscribe):
            self._unsubscribe = subscribe(self._identity_overview_changed)

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
        if self._active_identity_id is None or not self._identity_ids:
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
                if row.get("selected"):
                    selected_index = index
            if selected_index is not None:
                selected = selected_index
            elif current_index is not None:
                selected = current_index
            self._identity.set_sensitive(bool(self._identity_ids))
            if self._identity_ids:
                self._identity.set_selected(selected)
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
        index = int(self._identity.get_selected())
        return self._identity_ids[index] if 0 <= index < len(self._identity_ids) else self._identity_ids[0]

    def _identity_changed(self, *_args) -> None:
        if self._reloading_identities:
            return
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

    def _language_changed(self, *_args) -> None:
        if not self._reloading_languages:
            self._render()

    def _matches(self, ranked: RankedCandidate) -> bool:
        if ranked.score < int(self._score.get_value()):
            return False
        provider = self.selected_provider_id()
        if provider and provider not in ranked.sources:
            return False
        language = self.selected_language_id()
        if language and language not in _candidate_languages(ranked.item):
            return False
        needle = normalize(self._text.get_text())
        if needle:
            item = ranked.item
            haystack = normalize(" ".join((str(get_field(item, "title", "") or ""), str(get_field(item, "author", "") or ""))))
            if needle not in haystack:
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
        selected_id = self.selected_language_id()
        available = {
            language
            for ranked in ranked_rows
            for language in _candidate_languages(ranked.item)
        }
        if selected_id:
            available.add(selected_id)
        ordered = sorted(available, key=lambda language: (_language_label(language).casefold(), language))

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
        provider_id = str(provider_id or "")
        return self._provider_labels.get(provider_id, provider_id)

    def _provider_label(self) -> str:
        provider_id = self.selected_provider_id()
        return self._display_source(provider_id) if provider_id else ""

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

    def _reload_providers(self, providers: tuple[tuple[str, str], ...]) -> None:
        for provider_id, label in providers:
            pid = str(provider_id or "")
            if pid:
                self._provider_labels[pid] = str(label or pid)
        self._base_reload_providers(providers)

    def _start_refresh(self):
        self._reset_llm_selection(None)
        if self._task_handle is not None:
            self._task_handle.cancel()

        identity_id = self.selected_identity_id()
        if not identity_id:
            self._summary.set_label("Aucun profil disponible.")
            return False
        context = self.plugin.context
        if context is None:
            self._summary.set_label("Plugin non activé.")
            return False
        metadata_broker = context.broker("metadata")
        catalog_broker = context.broker("catalog")
        task_broker = context.broker("tasks")
        if metadata_broker is None or catalog_broker is None or task_broker is None:
            self._summary.set_label("Core incompatible : brokers metadata/catalog/tasks indisponibles.")
            return False

        self._generation += 1
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
            except Exception as exc:
                self.GLib.idle_add(self._complete, generation, None, str(exc))
            else:
                self.GLib.idle_add(self._complete, generation, result, None)

        handle.add_done_callback(finished)
        return False

    def refresh(self):
        # A manual/profile refresh consumes any queued overview refresh because
        # this computation will already read the latest local projection.
        self._clear_overview_refresh()
        identity_id = self.selected_identity_id()
        profile_changed = (
            self._active_identity_id is not None
            and identity_id is not None
            and identity_id != self._active_identity_id
        )
        self._active_identity_id = identity_id
        self._provisional_order.clear()
        self._set_provisional(False)
        self._diagnostic_cache = 0
        self._diagnostic_network = 0
        if hasattr(self, "_progress"):
            self._progress.set_tooltip_text(self._diagnostic_tooltip())

        if hasattr(self, "_master"):
            if profile_changed:
                self._ranked = []
                self._ranked_by_key = {}
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
                self._summary.set_label(
                    "Recalcul en cours · résultats précédents conservés jusqu’aux nouvelles propositions"
                )

        value = self._start_refresh()
        if self._busy and not self._ranked:
            self._summary.set_label("Analyse de la bibliothèque…")
        return value

    def _set_progress(self, generation: int, label: str) -> bool:
        if generation != self._generation:
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

        if snapshot.providers:
            self._reload_providers(snapshot.providers)

        self._ranked = self._stable_partial(list(snapshot.ranked))
        self._reload_languages(self._ranked)
        self._set_provisional(True)

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

    def _update_view_summary(self, *, provisional: bool) -> None:
        total = len(self._ranked)
        visible = sum(1 for row in self._ranked if self._matches(row))
        if not total:
            return

        provider = self._provider_label()
        language = self.selected_language_id()
        text_filter = str(self._text.get_text() or "").strip()
        filters = []
        if text_filter:
            filters.append(f"« {text_filter} »")
        if provider:
            filters.append(provider)
        if language:
            filters.append(_language_label(language))

        if filters or visible != total:
            parts = [f"{visible} sur {total}"]
        else:
            parts = [f"{total} recommandation(s)"]
        parts.extend(filters)
        if provisional:
            parts.append("Provisoire · ordre susceptible d’évoluer")
        else:
            parts.append("Ordre diversifié")
        self._summary.set_label(" · ".join(parts))

    def _render(self, preserve_summary: bool = False) -> None:
        visible = [row for row in self._ranked if self._matches(row)]

        rows = []
        mapping = {}
        for index, ranked in enumerate(visible):
            item = ranked.item
            key = self._candidate_key(ranked) or f"recommendation-{index}"
            if key in mapping:
                key = f"{key}:{index}"
            mapping[key] = ranked

            author = str(get_field(item, "author", "") or "").strip()
            source_labels = [
                self._display_source(source)
                for source in ranked.sources
                if str(source or "")
            ]
            sources = " · ".join(dict.fromkeys(source_labels))
            reasons = " · ".join(ranked.reasons)
            meta_parts = []
            if sources:
                meta_parts.append(sources)
            if reasons:
                meta_parts.append("Pourquoi : " + reasons)

            rows.append(
                UiBookListEntry(
                    key=key,
                    title=str(get_field(item, "title", "") or "Sans titre"),
                    subtitle=author,
                    meta=" · ".join(meta_parts),
                    badge=f"Affinité {ranked.score}/100",
                    thumbnail_url=str(
                        get_field(item, "thumbnail_url", "")
                        or get_field(item, "cover_url", "")
                        or ""
                    ) or None,
                )
            )

        self._ranked_by_key = mapping
        self._master.show_rows(tuple(rows), total_count=len(self._ranked))

        if not rows:
            self._master.empty_detail(
                "Aucune recommandation sélectionnée",
                "Modifiez les filtres ou relancez le calcul.",
            )
        if not preserve_summary and self._ranked:
            self._update_view_summary(provisional=self._provisional_active)

    def _selected_row(self, row: UiBookListEntry | None) -> None:
        self._reset_llm_selection(row.key if row is not None else None)
        if row is None:
            self._master.empty_detail(
                "Aucune recommandation sélectionnée",
                "Sélectionnez un livre dans la liste pour afficher ses détails.",
            )
            return
        ranked = self._ranked_by_key.get(row.key)
        if ranked is None:
            return
        self._llm_selected_row = row
        if self._llm_button is not None:
            self._llm_button.set_sensitive(not self._busy and self._llm_handle is None)

        item = ranked.item
        title = str(get_field(item, "title", "") or "Sans titre")
        author = str(get_field(item, "author", "") or "").strip()
        fmt = str(get_field(item, "format", "") or "").strip()
        published = str(get_field(item, "published_at", "") or "").strip()
        reasons = " · ".join(ranked.reasons)
        source_labels = tuple(
            dict.fromkeys(
                self._display_source(source)
                for source in ranked.sources
                if str(source or "")
            )
        )
        description = str(
            get_field(item, "description", "")
            or get_field(item, "summary", "")
            or ""
        ).strip()

        recommendation_fields = [
            DetailField("affinity", "Affinité", f"{ranked.score}/100"),
            DetailField(
                "ranking",
                "Classement",
                "Ordre diversifié : l’affinité n’est pas l’unique clé de tri.",
            ),
            DetailField(
                "reasons",
                "Pourquoi",
                reasons or "Plusieurs signaux concordants",
            ),
        ]
        catalogue_fields = []
        if source_labels:
            catalogue_fields.append(
                DetailField("source", "Source", " · ".join(source_labels))
            )
        if fmt:
            catalogue_fields.append(DetailField("format", "Format", fmt))
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
        variants = tuple(getattr(ranked, "notices", ()) or ())
        exact_variants = [
            variant for variant in variants[:16]
            if getattr(variant, "has_source_coordinates", False)
        ]
        if exact_variants:
            edition_fields = []
            for index, variant in enumerate(exact_variants, 1):
                name = self._display_source(variant.source_provider)
                valid_isbns = _attested_catalogue_isbns((variant,))
                isbn = valid_isbns[0] if valid_isbns else ""
                edition_fields.append(DetailField(
                    f"source-edition-{index}", f"Notice source {index}",
                    f"{name} · ISBN {isbn}" if isbn else
                    f"{name} · édition non attestée",
                ))
            edition_fields.append(DetailField(
                "source-edition-warning", "Actions disponibles",
                "Choisir une notice dans Recherche pour consulter les "
                "disponibilités et les actions réellement autorisées.",
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
        key_at_selection = row.key
        if identity_at_selection:
            for isbn in _attested_catalogue_isbns(variants):
                def open_isbn(isbn=isbn):
                    if (
                        self._generation != generation_at_selection
                        or self._llm_selection_key != key_at_selection
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
        url = _external_book_url(get_field(item, "friendly_url", ""))
        if url:
            actions.append(
                UiBookDetailAction(
                    "recommendation.online",
                    "Voir en ligne",
                    "external-link-symbolic",
                    uri=url,
                )
            )

        thumbnail = str(
            get_field(item, "thumbnail_url", "")
            or get_field(item, "cover_url", "")
            or ""
        ) or None
        badges = [f"Affinité {ranked.score}/100", *source_labels]
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
        reasons = tuple(ranked.reasons)
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
        self._refresh_button.set_sensitive(True)
        self._spinner.stop()
        if error or result is None:
            self._ranked = []
            message = "Calcul impossible" + (f" : {error}" if error else "")
            self._summary.set_label(message)
            try:
                self.host.toast(message)
            except Exception:
                pass
            self._render()
            return False

        self._ranked = list(result.ranked)
        self._reload_providers(result.providers)
        self._reload_languages(self._ranked)
        if self._reference_book is None and result.profile_books:
            self._reload_reference_books(result.profile_books)
        if not result.books_count:
            self._summary.set_label("Le profil ne contient aucun livre exploitable.")
        elif not result.providers:
            self._summary.set_label("Aucun catalogue connecté ne permet la recherche.")
        elif not result.queries:
            self._summary.set_label("Pas assez de métadonnées pour construire des recommandations.")
        elif not result.ranked:
            self._summary.set_label("Aucune recommandation trouvée avec les catalogues actuellement connectés.")
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

        self._set_provisional(False)
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

    def on_map(_widget):
        nonlocal scheduled
        if scheduled:
            return
        scheduled = True
        GLib.idle_add(view.refresh)

    widget.connect("map", on_map)
    get_mapped = getattr(widget, "get_mapped", None)
    if callable(get_mapped) and get_mapped():
        on_map(widget)

PLUGIN = RecommendationsPlugin()
