"""Specialized Youth search page. Rendering reads the local youth cache only."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from urllib.parse import urlsplit

from cifereca.plugin_api import (
    BookRef,
    DetailField,
    DetailSection,
    DocumentLink,
    UiActionContribution,
    UiBookDetailPresentation,
    UiBookListEntry,
    UiPageContribution,
)
from youth_catalog import normalize_isbn


KIND_LABELS = {
    "": "Tous les types d’âge",
    "recommended": "Âge recommandé",
    "interest": "Âge d’intérêt",
    "reading": "Âge de lecture",
    "publisher": "Âge éditeur",
    "inferred": "Âge inféré",
}
KIND_VALUES = tuple(KIND_LABELS)
_SPLIT = re.compile(r"[,;]")


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _fold(value: object) -> str:
    return _text(value).casefold()


def public_url(value: object) -> str:
    raw = _text(value)
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return ""
    return raw


def age_text(age_range: object) -> str:
    lo = getattr(age_range, "age_min", None)
    hi = getattr(age_range, "age_max", None)
    if lo == 0 and hi is not None:
        bounds = f"jusqu’à {hi} ans"
    elif lo is not None and hi is None:
        bounds = f"à partir de {lo} ans"
    elif lo is not None and hi == lo:
        bounds = f"{lo} ans"
    elif lo is not None and hi is not None:
        bounds = f"{lo}–{hi} ans"
    elif hi is not None:
        bounds = f"jusqu’à {hi} ans"
    else:
        bounds = "âge inconnu"
    kind = KIND_LABELS.get(_fold(getattr(age_range, "age_kind", "")), _text(getattr(age_range, "age_kind", "")) or "Âge")
    return f"{kind} · {bounds}"


def age_detail(age_range: object) -> str:
    parts = [age_text(age_range)]
    source_id = _text(getattr(age_range, "source_id", ""))
    label = _text(getattr(age_range, "source_label", ""))
    confidence = getattr(age_range, "confidence", None)
    if source_id:
        parts.append(f"source {source_id}")
    if label:
        parts.append(f"« {label} »")
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        parts.append(f"confiance {round(float(confidence) * 100)} %")
    return " · ".join(parts)


def book_key(book: object) -> str:
    return f"{_text(getattr(book, 'source_id', 'source'))}:{_text(getattr(book, 'record_id', 'record'))}"


def book_ref(book: object) -> BookRef:
    return BookRef(
        book_id=_text(getattr(book, "record_id", "")),
        title=_text(getattr(book, "title", "")),
        authors=tuple(getattr(book, "authors", ()) or ()),
        identifiers=dict(getattr(book, "identifiers", {}) or {}),
        metadata={
            "youth_age_ranges": tuple(
                {
                    "age_min": getattr(row, "age_min", None),
                    "age_max": getattr(row, "age_max", None),
                    "age_kind": getattr(row, "age_kind", ""),
                    "source_label": getattr(row, "source_label", ""),
                    "source_id": getattr(row, "source_id", ""),
                    "confidence": getattr(row, "confidence", None),
                }
                for row in tuple(getattr(book, "age_ranges", ()) or ())
            )
        },
    )


def age_badge(age_range: object) -> str:
    """Short age chip: preserve the precise kind and provenance in the detail."""
    lo = getattr(age_range, "age_min", None)
    hi = getattr(age_range, "age_max", None)
    if lo is None and hi is None:
        return "Âge inconnu"
    if lo is None:
        bounds = f"≤{hi}"
    elif hi is None:
        bounds = f"{lo}+"
    elif lo == hi:
        bounds = str(lo)
    else:
        bounds = f"{lo}–{hi}"
    kind = _fold(getattr(age_range, "age_kind", ""))
    prefix = {
        "interest": "Intérêt ",
        "reading": "Lecture ",
        "publisher": "Éditeur ",
        "inferred": "Inféré ",
    }.get(kind, "")
    return f"{prefix}{bounds} ans"


def list_entry(book: object) -> UiBookListEntry:
    ranges = tuple(getattr(book, "age_ranges", ()) or ())
    age_summary = " · ".join(age_text(row) for row in ranges[:2])
    language = _text(getattr(book, "language", ""))
    formats = tuple(getattr(book, "formats", ()) or ())
    source = ", ".join(tuple(getattr(book, "provenance", ()) or ())[:3])
    meta = " · ".join(filter(None, (age_summary, language, ", ".join(formats), source)))
    badge = age_badge(ranges[0]) if ranges else "Âge inconnu"
    return UiBookListEntry(
        key=book_key(book),
        title=_text(getattr(book, "title", "")),
        subtitle=", ".join(tuple(getattr(book, "authors", ()) or ())),
        meta=meta,
        badge=badge,
        thumbnail_url=public_url(getattr(book, "cover_url", "")) or None,
    )


def detail_presentation(book: object) -> UiBookDetailPresentation:
    ranges = tuple(getattr(book, "age_ranges", ()) or ())
    age_fields = tuple(
        DetailField(f"age-{index}", KIND_LABELS.get(_fold(getattr(row, "age_kind", "")), "Âge"), age_detail(row))
        for index, row in enumerate(ranges[:8])
    )
    if not age_fields:
        age_fields = (DetailField("age-empty", "Âge", "Aucune tranche d’âge attestée en cache."),)
    metadata_fields = []
    source = ", ".join(tuple(getattr(book, "provenance", ()) or ()))
    if source:
        metadata_fields.append(DetailField("sources", "Sources", source))
    language = _text(getattr(book, "language", ""))
    if language:
        metadata_fields.append(DetailField("language", "Langue", language))
    formats = tuple(getattr(book, "formats", ()) or ())
    if formats:
        metadata_fields.append(DetailField("formats", "Formats", ", ".join(formats)))
    book_type = _text(getattr(book, "book_type", ""))
    if book_type:
        metadata_fields.append(DetailField("type", "Type", book_type))
    themes = tuple(getattr(book, "themes", ()) or ())
    if themes:
        metadata_fields.append(DetailField("themes", "Thèmes", ", ".join(themes[:12])))
    publishers = tuple(getattr(book, "publishers", ()) or ())
    if publishers:
        metadata_fields.append(DetailField("publishers", "Éditeurs", ", ".join(publishers[:6])))
    published_year = getattr(book, "published_year", None)
    if published_year:
        metadata_fields.append(DetailField("published-year", "Première publication", str(published_year)))
    bibliography_sources = tuple(getattr(book, "bibliography_sources", ()) or ())
    if bibliography_sources:
        metadata_fields.append(DetailField(
            "bibliography-sources", "Enrichissement bibliographique", ", ".join(bibliography_sources)
        ))
    link = public_url(getattr(book, "friendly_url", "") or getattr(book, "notice_url", ""))
    links = (DocumentLink("notice", "Voir la notice publique", link),) if link else ()
    sections = [DetailSection("ages", "Âges attestés", age_fields)]
    if metadata_fields:
        sections.append(DetailSection("metadata", "Métadonnées", tuple(metadata_fields)))
    return UiBookDetailPresentation(
        title=_text(getattr(book, "title", "")),
        subtitle=", ".join(tuple(getattr(book, "authors", ()) or ())),
        thumbnail_url=public_url(getattr(book, "cover_url", "")) or None,
        badges=tuple(age_text(row) for row in ranges[:3]),
        sections=tuple(sections),
        links=links,
    )


def page_parts(value: object) -> tuple[tuple[object, ...], str, int]:
    if isinstance(value, Mapping):
        rows = value.get("items") or value.get("results") or ()
        if isinstance(rows, (str, bytes, Mapping)):
            rows = ()
        try:
            rows = tuple(rows)
        except TypeError:
            rows = ()
        cursor = _text(value.get("next_cursor"))
        try:
            freshness = max(0, int(value.get("fresh_for_seconds") or 0))
        except (TypeError, ValueError):
            freshness = 0
        return rows, cursor, freshness
    if value is None or isinstance(value, (str, bytes, Mapping)):
        return (), "", 0
    try:
        return tuple(value), "", 0
    except TypeError:
        return (), "", 0


def source_info(row: object) -> tuple[str, str]:
    service = getattr(row, "service", row)
    method = getattr(service, "source_info", None)
    try:
        raw = method() if callable(method) else {}
    except Exception:
        raw = {}
    if not isinstance(raw, Mapping):
        raw = {}
    source_id = _fold(raw.get("source_id") or raw.get("id") or getattr(row, "plugin_id", ""))
    label = _text(raw.get("display_name") or raw.get("name") or source_id)
    return source_id, label


def fetch_source_page(
    service: object,
    query: str,
    *,
    age: int | None,
    themes: tuple[str, ...],
    cursor: str | None,
    limit: int = 100,
) -> object:
    """Explicit remote call. Prefer an optional age-aware browse extension when present."""
    browse = getattr(service, "browse", None)
    if callable(browse):
        return browse(
            age=age,
            themes=themes,
            text_filter=_text(query),
            cursor=cursor,
            limit=max(1, min(int(limit), 200)),
        )
    search = getattr(service, "search", None)
    if not callable(search):
        raise TypeError("source jeunesse incompatible : search() absent")
    return search(_text(query), cursor=cursor, limit=max(1, min(int(limit), 200)))


def fetch_bibliography(service: object, isbn: object) -> tuple[object, ...]:
    normalized = normalize_isbn(isbn)
    if not normalized:
        raise ValueError("ISBN valide requis")
    lookup = getattr(service, "lookup_isbn", None)
    if not callable(lookup):
        raise TypeError("service bibliographique incompatible : lookup_isbn() absent")
    rows = lookup(normalized)
    if rows is None or isinstance(rows, (str, bytes, Mapping)):
        return ()
    try:
        return tuple(rows)
    except TypeError:
        return ()


def parse_themes(value: object) -> tuple[str, ...]:
    out = []
    for part in _SPLIT.split(_text(value)):
        item = _fold(part)
        if item and item not in out:
            out.append(item)
    return tuple(out)


@dataclass(frozen=True, slots=True)
class FilterState:
    query: str = ""
    age: int | None = None
    age_kind: str = ""
    source_id: str = ""
    language: str = ""
    format: str = ""
    book_type: str = ""
    themes: tuple[str, ...] = ()
    include_inferred: bool = False


class YouthPagePlugin:
    def activate(self, context):
        self.context, self.view, self.pending = context, None, None
        return self

    def pages(self):
        return [UiPageContribution(
            "youth",
            "Jeunesse",
            "system-search-symbolic",
            35,
            self.build,
            actions=(UiActionContribution(
                "youth.from-book",
                "Rechercher en Jeunesse",
                self.from_book,
                target="book.detail",
                visible_when=lambda context: context.book is not None,
            ),),
        )]

    def from_book(self, host):
        self.pending = host.action_context().book
        if self.view:
            self.view.seed(self.pending)
        host.navigate("youth")

    def build(self, host):
        from gi.repository import Gtk, GLib
        self.view = YouthView(self, host, Gtk, GLib)
        return self.view.widget

    def deactivate(self):
        if self.view:
            self.view.dispose()
        self.view = self.pending = None


class YouthView:
    def __init__(self, plugin, host, Gtk, GLib):
        self.plugin, self.host, self.Gtk, self.GLib = plugin, host, Gtk, GLib
        self.catalog = plugin.context.service("catalog.youth")
        self.rows, self.selected_book = {}, None
        self.generation, self.task, self.disposed = 0, None, False
        self.next_cursor, self.next_source_plugin = "", ""
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.status = Gtk.Label(
            label="Cache local uniquement. Une collecte distante nécessite le bouton « Collecter cette source ».",
            wrap=True, xalign=0,
        )
        self.widget.append(self.status)

        self.identities = self._identity_rows()
        self.identity = Gtk.DropDown.new_from_strings([
            str(row.get("label") or row.get("name") or row.get("identity_id") or "Profil")
            for row in self.identities
        ])
        self.identity.set_tooltip_text("Profil de bibliothèque")
        self.identity.connect("notify::selected", lambda *_: self.filters_changed())
        self.widget.append(self.identity)

        filters = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=5)
        self.widget.append(filters)
        self.query = Gtk.Entry(placeholder_text="Titre, auteur ou thème — cache local")
        self.age = Gtk.DropDown.new_from_strings(["Tous les âges"] + [f"{age} ans" for age in range(18)])
        self.age_kind = Gtk.DropDown.new_from_strings([KIND_LABELS[value] for value in KIND_VALUES])
        self.source = Gtk.DropDown.new_from_strings(["Toutes les sources"])
        self.language = Gtk.Entry(placeholder_text="Langue (fr, en…)")
        self.format = Gtk.Entry(placeholder_text="Format (epub, pdf…)")
        self.book_type = Gtk.Entry(placeholder_text="Type (fiction, documentaire…)")
        self.themes = Gtk.Entry(placeholder_text="Thèmes, séparés par des virgules")
        self.inferred = Gtk.CheckButton(label="Inclure les âges inférés", active=False)
        for widget in (self.query, self.age, self.age_kind, self.source, self.language, self.format, self.book_type, self.themes, self.inferred):
            filters.append(widget)
        for entry in (self.query, self.language, self.format, self.book_type, self.themes):
            entry.connect("changed", lambda *_: self.filters_changed())
        for dropdown in (self.age, self.age_kind, self.source):
            dropdown.connect("notify::selected", lambda *_: self.filters_changed())
        self.inferred.connect("toggled", lambda *_: self.filters_changed())

        actions = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE)
        self.widget.append(actions)
        actions.append(self.button("Collecter cette source", self.collect))
        actions.append(self.button("Plus de résultats", self.more))
        actions.append(self.button("Enrichir la bibliographie", self.enrich_bibliography))
        actions.append(self.button("Ouvrir la fiche Bibliothèque", self.open_selected))

        self.master = host.build_book_master_detail(
            title="Catalogue Jeunesse",
            subtitle="Âges et provenance conservés source par source",
            empty_title="Aucun titre Jeunesse",
            empty_desc="Le cache est vide pour ces filtres. Choisissez une source puis collectez-la explicitement.",
        )
        self.master.connect_selected(self.selected)
        self.widget.append(self.master.widget)

        self.sources = ()
        self.unsubscribe_sources = plugin.context.subscribe_services("youth.source", lambda: GLib.idle_add(self.reload_sources))
        self.unsubscribe_identity = host.subscribe_identity_overview(lambda: GLib.idle_add(self.reload_identities)) if hasattr(host, "subscribe_identity_overview") else lambda: None
        self.reload_sources()
        if plugin.pending:
            self.seed(plugin.pending)
            plugin.pending = None
        else:
            self.render_cached()

    def button(self, label, callback):
        button = self.Gtk.Button(label=label)
        button.connect("clicked", lambda *_: callback())
        return button

    def _identity_rows(self):
        broker = self.plugin.context.broker("identities")
        return list(broker.identities()) if broker is not None and hasattr(broker, "identities") else []

    def identity_id(self):
        index = self.identity.get_selected()
        return str(self.identities[index].get("identity_id") or "") if index < len(self.identities) else ""

    def reload_identities(self):
        if self.disposed:
            return False
        current = self.identity_id()
        rows = self._identity_rows()
        self.identities = rows
        self.identity.set_model(self.Gtk.StringList.new([
            str(row.get("label") or row.get("name") or row.get("identity_id") or "Profil")
            for row in rows
        ]))
        selected = next((index for index, row in enumerate(rows) if str(row.get("identity_id")) == current), 0)
        self.identity.set_selected(selected)
        self.filters_changed()
        return False

    def reload_sources(self):
        if self.disposed:
            return False
        current = self.selected_source_plugin()
        self.sources = tuple(self.plugin.context.services(
            "youth.source", contract="cifereca.youth-source", version=">=1,<2"
        ))
        labels = ["Toutes les sources"] + [source_info(row)[1] or row.plugin_id for row in self.sources]
        self.source.set_model(self.Gtk.StringList.new(labels))
        selected = next((index + 1 for index, row in enumerate(self.sources) if row.plugin_id == current), 0)
        self.source.set_selected(selected)
        self.filters_changed()
        return False

    def selected_source(self):
        index = self.source.get_selected()
        return self.sources[index - 1] if 0 < index <= len(self.sources) else None

    def selected_source_plugin(self):
        row = self.selected_source()
        return str(row.plugin_id) if row else ""

    def age_value(self):
        index = self.age.get_selected()
        return index - 1 if 1 <= index <= 18 else None

    def filter_state(self):
        source = self.selected_source()
        source_id = source_info(source)[0] if source else ""
        kind_index = self.age_kind.get_selected()
        kind = KIND_VALUES[kind_index] if kind_index < len(KIND_VALUES) else ""
        return FilterState(
            query=self.query.get_text().strip(),
            age=self.age_value(),
            age_kind=kind,
            source_id=source_id,
            language=self.language.get_text().strip(),
            format=self.format.get_text().strip(),
            book_type=self.book_type.get_text().strip(),
            themes=parse_themes(self.themes.get_text()),
            include_inferred=self.inferred.get_active(),
        )

    def filters_changed(self):
        self.generation += 1
        if self.task:
            self.task.cancel()
        self.next_cursor = self.next_source_plugin = ""
        self.render_cached()

    def seed(self, book):
        if book is None:
            return
        title = _text(getattr(book, "title", ""))
        if title:
            self.query.set_text(title)
        self.render_cached()

    def render_cached(self):
        if not self.catalog:
            self.rows = {}
            self.master.set_list_state(
                "Plugin catalogue Jeunesse indisponible",
                "Activez le moteur Jeunesse en même temps que cette page.",
                "dialog-warning-symbolic",
            )
            self.master.empty_detail()
            return
        identity = self.identity_id()
        if not identity:
            self.rows = {}
            self.master.set_list_state("Aucun profil disponible", "Créez ou activez un profil Cifereca.")
            self.master.empty_detail()
            return
        state = self.filter_state()
        books = self.catalog.search_cached(
            identity,
            state.query,
            age=state.age,
            age_kind=state.age_kind,
            source_id=state.source_id,
            language=state.language,
            format=state.format,
            book_type=state.book_type,
            themes=state.themes,
            include_inferred=state.include_inferred,
        )
        self.rows = {book_key(book): book for book in books}
        entries = tuple(list_entry(book) for book in books)
        self.master.show_rows(entries, total_count=len(entries))
        if not entries:
            self.master.set_list_state(
                "Aucun résultat en cache",
                "Les filtres restent locaux. Une collecte distante n’a lieu que sur demande explicite.",
            )
            self.master.empty_detail()

    def selected(self, row):
        if row is None:
            self.selected_book = None
            self.master.empty_detail()
            return
        self.selected_book = self.rows.get(row.key)
        if self.selected_book is None:
            self.master.empty_detail()
            return
        self.master.show_detail(detail_presentation(self.selected_book))

    def open_selected(self):
        if not self.selected_book:
            self.host.toast("Sélectionnez un livre Jeunesse.")
            return
        method = getattr(self.host, "open_book_detail", None)
        if callable(method) and method(book_ref(self.selected_book), identity_id=self.identity_id()):
            return
        self.host.toast("Ce livre n’est pas présent de façon non ambiguë dans la bibliothèque de ce profil.")

    def enrich_bibliography(self):
        if not self.selected_book:
            self.status.set_label("Sélectionnez un livre Jeunesse à enrichir.")
            return
        isbn = normalize_isbn(dict(getattr(self.selected_book, "identifiers", {}) or {}).get("isbn", ""))
        if not isbn:
            self.status.set_label("Enrichissement impossible sans ISBN valide : aucun rapprochement titre/auteur automatique.")
            return
        identity = self.identity_id()
        if not identity:
            self.status.set_label("Aucun profil actif.")
            return
        services = tuple(self.plugin.context.services(
            "youth.bibliography",
            contract="cifereca.youth-bibliography",
            version=">=1,<2",
            capabilities=("youth.bibliography.lookup",),
        ))[:4]
        if not services:
            self.status.set_label("Aucun service d’enrichissement bibliographique actif.")
            return
        selected_key = book_key(self.selected_book)

        def operation(control):
            out = []
            for row in services:
                if control.cancelled():
                    break
                source_id, label = source_info(row)
                try:
                    rows = fetch_bibliography(row.service, isbn)
                    out.append((source_id, label, rows, ""))
                except Exception as exc:
                    out.append((source_id, label, (), str(exc)))
            return tuple(out)

        self.run_bibliography(
            operation,
            lambda result: self.bibliography_collected(result, isbn=isbn),
            token=(identity, selected_key),
        )

    def run_bibliography(self, operation, complete, token):
        broker = self.plugin.context.broker("tasks")
        if broker is None:
            self.status.set_label("Broker de tâches indisponible.")
            return
        self.generation += 1
        generation = self.generation
        if self.task:
            self.task.cancel()
        self.status.set_label("Enrichissement bibliographique explicite en cours…")
        self.task = broker.submit(operation, label="Bibliographie Jeunesse", kind="youth")
        def done(handle):
            try:
                result, error = handle.result(), False
            except Exception:
                result, error = None, True
            def apply():
                current_key = book_key(self.selected_book) if self.selected_book is not None else ""
                current_token = (self.identity_id(), current_key)
                if self.disposed or generation != self.generation or current_token != token:
                    return False
                if error or result is None:
                    self.status.set_label("Échec de l’enrichissement. Le cache Jeunesse précédent est conservé.")
                else:
                    complete(result)
                return False
            self.GLib.idle_add(apply)
        self.task.add_done_callback(done)

    def bibliography_collected(self, result, *, isbn):
        accepted = 0
        failures = []
        for source_id, label, rows, error in result:
            if error:
                failures.append(label or source_id)
                continue
            report = self.catalog.ingest_bibliography(
                self.identity_id(), source_id, isbn, rows,
            )
            accepted += report.accepted
            if report.errors:
                failures.append(label or source_id)
        message = f"{accepted} enrichissement(s) bibliographique(s) appliqué(s)"
        if failures:
            message += f" · {len(failures)} source(s) sans résultat exploitable"
        self.status.set_label(message)
        self.render_cached()

    def _remote_request(self, cursor):
        row = self.selected_source()
        if row is None:
            self.status.set_label("Choisissez une source Jeunesse avant de collecter.")
            return
        state = self.filter_state()
        identity = self.identity_id()
        if not identity:
            self.status.set_label("Aucun profil actif.")
            return
        service = row.service
        source_id, label = source_info(row)
        append = bool(cursor)
        token = (identity, row.plugin_id, state.query, state.age, state.themes)
        self.run_remote(
            lambda control: None if control.cancelled() else fetch_source_page(
                service, state.query, age=state.age, themes=state.themes,
                cursor=cursor or None, limit=100,
            ),
            lambda raw: self.collected(raw, source_id=source_id, label=label, append=append, source_plugin=row.plugin_id),
            token,
        )

    def collect(self):
        self._remote_request("")

    def more(self):
        if not self.next_cursor or self.next_source_plugin != self.selected_source_plugin():
            self.status.set_label("Aucune page suivante disponible pour cette source.")
            return
        self._remote_request(self.next_cursor)

    def run_remote(self, operation, complete, token):
        broker = self.plugin.context.broker("tasks")
        if broker is None:
            self.status.set_label("Broker de tâches indisponible.")
            return
        self.generation += 1
        generation = self.generation
        if self.task:
            self.task.cancel()
        self.status.set_label("Collecte explicite en cours…")
        self.task = broker.submit(operation, label="Catalogue Jeunesse", kind="youth")
        def done(handle):
            try:
                result, error = handle.result(), False
            except Exception:
                result, error = None, True
            def apply():
                current = self.filter_state()
                current_token = (self.identity_id(), self.selected_source_plugin(), current.query, current.age, current.themes)
                if self.disposed or generation != self.generation or current_token != token:
                    return False
                if error or result is None:
                    self.status.set_label("Échec de la source. Le cache précédent est conservé.")
                else:
                    complete(result)
                return False
            self.GLib.idle_add(apply)
        self.task.add_done_callback(done)

    def collected(self, raw, *, source_id, label, append, source_plugin):
        rows, cursor, fresh = page_parts(raw)
        report = self.catalog.ingest(
            self.identity_id(),
            source_id,
            rows,
            replace_source=not append,
            fresh_for_seconds=fresh,
        )
        self.next_cursor = cursor
        self.next_source_plugin = str(source_plugin) if cursor else ""
        message = f"{report.accepted} notice(s) reçue(s) de {label or source_id}"
        if report.rejected:
            message += f" · {report.rejected} rejet(s)"
        if cursor:
            message += " · page suivante disponible"
        self.status.set_label(message)
        self.render_cached()

    def dispose(self):
        self.disposed = True
        self.generation += 1
        if self.task:
            self.task.cancel()
        self.unsubscribe_sources()
        self.unsubscribe_identity()


PLUGIN = YouthPagePlugin()
