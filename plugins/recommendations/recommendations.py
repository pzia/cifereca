from __future__ import annotations

from typing import Any, Mapping

from cifereca.plugin_api import UiActionContribution, UiPageContribution

from recommendation_engine import RankedCandidate, get_field, normalize
from recommendation_service_v022 import RecommendationResult, compute_recommendations


class RecommendationsPlugin:
    def __init__(self) -> None:
        self.context = None
        self._view = None

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

    def build(self, host):
        from gi.repository import GLib, Gtk

        view = RecommendationsView(self, host, Gtk, GLib)
        self._view = view
        GLib.idle_add(view.refresh)
        return view.widget

    def deactivate(self):
        if self._view is not None:
            self._view.dispose()
        self._view = None
        self.context = None


class RecommendationsView:
    def __init__(self, plugin: RecommendationsPlugin, host: Any, Gtk: Any, GLib: Any) -> None:
        self.plugin = plugin
        self.host = host
        self.Gtk = Gtk
        self.GLib = GLib
        self._ranked: list[RankedCandidate] = []
        self._identity_ids: list[str] = []
        self._provider_ids: list[str] = [""]
        self._unsubscribe = None
        self._generation = 0
        self._busy = False

        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for setter in (self.widget.set_margin_top, self.widget.set_margin_bottom, self.widget.set_margin_start, self.widget.set_margin_end):
            setter(12)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title_box.set_hexpand(True)
        title = Gtk.Label(label="Recommandations pour votre bibliothèque", xalign=0)
        title.add_css_class("title-2")
        title_box.append(title)
        subtitle = Gtk.Label(label="Résultats issus des catalogues connectés, classés par affinité puis diversifiés.", xalign=0)
        subtitle.add_css_class("dim-label")
        subtitle.set_wrap(True)
        title_box.append(subtitle)
        header.append(title_box)
        self._refresh_button = Gtk.Button(label="Recalculer", icon_name="view-refresh-symbolic")
        self._refresh_button.connect("clicked", lambda *_: self.refresh())
        header.append(self._refresh_button)
        self.widget.append(header)

        controls = Gtk.FlowBox()
        controls.set_selection_mode(Gtk.SelectionMode.NONE)
        controls.set_row_spacing(7)
        controls.set_column_spacing(7)
        controls.set_max_children_per_line(6)

        self._identity_model = Gtk.StringList.new([])
        self._identity = Gtk.DropDown(model=self._identity_model)
        self._identity.set_tooltip_text("Profil utilisé comme point de départ")
        self._identity.connect("notify::selected", self._identity_changed)
        controls.insert(self._labeled("Profil", self._identity), -1)

        self._provider_model = Gtk.StringList.new(["Tous les catalogues"])
        self._provider = Gtk.DropDown(model=self._provider_model)
        self._provider.set_tooltip_text("Filtre les recommandations déjà calculées")
        self._provider.connect("notify::selected", lambda *_: self._render())
        controls.insert(self._labeled("Catalogue", self._provider), -1)

        self._text = Gtk.SearchEntry(placeholder_text="Titre ou auteur")
        self._text.connect("search-changed", lambda *_: self._render())
        controls.insert(self._labeled("Filtrer", self._text), -1)

        adjustment = Gtk.Adjustment(lower=0, upper=100, step_increment=5, page_increment=10, value=self._config_int("minimum_score", 15))
        self._score = Gtk.SpinButton(adjustment=adjustment, climb_rate=1, digits=0)
        self._score.connect("value-changed", lambda *_: self._render())
        controls.insert(self._labeled("Score ≥", self._score), -1)

        exploration = Gtk.Adjustment(lower=0, upper=100, step_increment=5, page_increment=10, value=self._config_int("exploration", 25))
        self._exploration = Gtk.SpinButton(adjustment=exploration, climb_rate=1, digits=0)
        self._exploration.set_tooltip_text("0 = pertinence pure ; 100 = davantage de diversité")
        controls.insert(self._labeled("Diversité", self._exploration), -1)

        self.widget.append(controls)

        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._spinner = Gtk.Spinner()
        status_row.append(self._spinner)
        self._summary = Gtk.Label(label="Préparation…", xalign=0)
        self._summary.set_hexpand(True)
        self._summary.set_wrap(True)
        status_row.append(self._summary)
        self.widget.append(status_row)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list.add_css_class("boxed-list")
        scroller.set_child(self._list)
        self.widget.append(scroller)

        self._reload_identities()
        subscribe = getattr(host, "subscribe_identity_overview", None)
        if callable(subscribe):
            self._unsubscribe = subscribe(self._reload_identities)

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

    def _config_str(self, key: str, fallback: str) -> str:
        value = self._config().get(key, fallback)
        return str(value or fallback)

    def _reload_identities(self) -> None:
        current = self.selected_identity_id()
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

        self._identity_ids = []
        while self._identity_model.get_n_items():
            self._identity_model.remove(0)
        selected = 0
        for row in rows:
            identity_id = str(row.get("identity_id") or "")
            if not identity_id:
                continue
            self._identity_ids.append(identity_id)
            self._identity_model.append(str(row.get("label") or row.get("name") or identity_id))
            if row.get("selected") or identity_id == current:
                selected = len(self._identity_ids) - 1
        self._identity.set_sensitive(bool(self._identity_ids))
        if self._identity_ids:
            self._identity.set_selected(selected)

    def selected_identity_id(self) -> str | None:
        if not self._identity_ids:
            return None
        index = int(self._identity.get_selected())
        return self._identity_ids[index] if 0 <= index < len(self._identity_ids) else self._identity_ids[0]

    def _identity_changed(self, *_args) -> None:
        if self._identity_ids:
            self.refresh()

    def refresh(self):
        if self._busy:
            self._generation += 1  # stale worker results will be ignored
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
        if metadata_broker is None or catalog_broker is None:
            self._summary.set_label("Core incompatible : brokers metadata/catalog indisponibles.")
            return False

        self._generation += 1
        generation = self._generation
        self._busy = True
        self._refresh_button.set_sensitive(False)
        self._spinner.start()
        self._summary.set_label("Analyse du profil et recherche dans les catalogues…")
        exploration = int(self._exploration.get_value())

        def progress(done: int, total: int, query: str) -> None:
            if generation != self._generation:
                return
            label = f"Recherche {done + 1}/{total} · {query}" if query and done < total else "Classement des résultats…"
            self.GLib.idle_add(self._set_progress, generation, label)

        task_broker = context.broker("tasks")
        if task_broker is None:
            self._busy = False
            self._refresh_button.set_sensitive(True)
            self._spinner.stop()
            self._summary.set_label("Core incompatible : broker tasks indisponible.")
            return False

        def operation(control):
            return compute_recommendations(
                metadata_broker,
                catalog_broker,
                identity_id,
                scope=self._config_str("scope", "digital"),
                max_queries=self._config_int("max_queries", 8),
                per_query=self._config_int("per_query", 50),
                max_results=self._config_int("max_results", 120),
                exploration=exploration,
                progress=progress,
                control=control,
            )

        handle = task_broker.submit(operation, label="Calcul des recommandations", kind="recommendations")

        def finished(done_handle) -> None:
            try:
                result = done_handle.result()
            except Exception as exc:
                self.GLib.idle_add(self._complete, generation, None, str(exc))
            else:
                self.GLib.idle_add(self._complete, generation, result, None)

        handle.add_done_callback(finished)
        return False

    def _set_progress(self, generation: int, label: str) -> bool:
        if generation == self._generation:
            self._summary.set_label(label)
        return False

    def _complete(self, generation: int, result: RecommendationResult | None, error: str | None) -> bool:
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
            self._summary.set_label(f"{len(result.ranked)} recommandations à partir de {result.books_count} livres et {len(result.queries)} signaux{suffix}")
        self._render(preserve_summary=True)
        return False

    def _reload_providers(self, providers: tuple[tuple[str, str], ...]) -> None:
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

    def selected_provider_id(self) -> str:
        index = int(self._provider.get_selected())
        return self._provider_ids[index] if 0 <= index < len(self._provider_ids) else ""

    def _matches(self, ranked: RankedCandidate) -> bool:
        if ranked.score < int(self._score.get_value()):
            return False
        provider = self.selected_provider_id()
        if provider and provider not in ranked.sources:
            return False
        needle = normalize(self._text.get_text())
        if needle:
            item = ranked.item
            haystack = normalize(" ".join((str(get_field(item, "title", "") or ""), str(get_field(item, "author", "") or ""))))
            if needle not in haystack:
                return False
        return True

    def _clear_list(self) -> None:
        child = self._list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._list.remove(child)
            child = nxt

    def _render(self, preserve_summary: bool = False) -> None:
        self._clear_list()
        visible = [row for row in self._ranked if self._matches(row)]
        if self._ranked and not preserve_summary:
            self._summary.set_label(f"{len(visible)} affichée(s) sur {len(self._ranked)} recommandation(s)")
        for ranked in visible:
            self._list.append(self._build_row(ranked))

    def _build_row(self, ranked: RankedCandidate):
        Gtk = self.Gtk
        item = ranked.item
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        for setter in (row.set_margin_top, row.set_margin_bottom, row.set_margin_start, row.set_margin_end):
            setter(8)

        score = Gtk.Label(label=f"{ranked.score}")
        score.add_css_class("title-3")
        score.set_tooltip_text("Score de pertinence sur 100")
        score.set_width_chars(3)
        row.append(score)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        body.set_hexpand(True)
        title = Gtk.Label(label=str(get_field(item, "title", "") or "Sans titre"), xalign=0)
        title.add_css_class("heading")
        title.set_wrap(True)
        body.append(title)

        author = str(get_field(item, "author", "") or "").strip()
        fmt = str(get_field(item, "format", "") or "").strip()
        date = str(get_field(item, "published_at", "") or "").strip()
        source = ", ".join(ranked.sources)
        details = " · ".join(x for x in (author, source, fmt, date) if x)
        if details:
            subtitle = Gtk.Label(label=details, xalign=0)
            subtitle.add_css_class("dim-label")
            subtitle.set_wrap(True)
            body.append(subtitle)

        if ranked.reasons:
            reason = Gtk.Label(label="Pourquoi : " + " · ".join(ranked.reasons), xalign=0)
            reason.set_wrap(True)
            body.append(reason)
        row.append(body)

        url = str(get_field(item, "friendly_url", "") or "").strip()
        if url.startswith(("http://", "https://")):
            button = Gtk.LinkButton(uri=url, label="Notice")
            button.set_tooltip_text("Ouvrir la notice du catalogue")
            row.append(button)
        return row

    def dispose(self) -> None:
        self._generation += 1
        if callable(self._unsubscribe):
            try:
                self._unsubscribe()
            except Exception:
                pass
        self._unsubscribe = None


PLUGIN = RecommendationsPlugin()
