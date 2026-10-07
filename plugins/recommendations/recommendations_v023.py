from __future__ import annotations

from typing import Any

from cifereca.plugin_api import (
    DetailField,
    DetailSection,
    UiBookDetailAction,
    UiBookDetailPresentation,
    UiBookListEntry,
)

from recommendation_engine import candidate_key, get_field
from recommendations import RecommendationsPlugin
from recommendations_v022 import RecommendationTaskControl, StreamingRecommendationsView


class MasterDetailRecommendationsView(StreamingRecommendationsView):
    """Recommendations rendered through Cifereca's public standard master/detail host."""

    def __init__(self, plugin, host, Gtk, GLib):
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
        self._task_control: RecommendationTaskControl | None = None
        self._pending_partial = None
        self._partial_source_id = 0
        self._render_generation = 0
        self._ranked_by_key: dict[str, Any] = {}

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
        controls.set_max_children_per_line(7)

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

        score_adjustment = Gtk.Adjustment(
            lower=0, upper=100, step_increment=5, page_increment=10,
            value=self._config_int("minimum_score", 15),
        )
        self._score = Gtk.SpinButton(adjustment=score_adjustment, climb_rate=1, digits=0)
        self._score.connect("value-changed", lambda *_: self._render())
        controls.insert(self._labeled("Affinité ≥", self._score), -1)

        exploration_adjustment = Gtk.Adjustment(
            lower=0, upper=100, step_increment=5, page_increment=10,
            value=self._config_int("exploration", 25),
        )
        self._exploration = Gtk.SpinButton(adjustment=exploration_adjustment, climb_rate=1, digits=0)
        self._exploration.set_tooltip_text("0 = pertinence pure ; 100 = davantage de diversité")
        controls.insert(self._labeled("Diversité", self._exploration), -1)

        self._refresh_button = Gtk.Button(label="Recalculer", icon_name="view-refresh-symbolic")
        self._refresh_button.set_tooltip_text("Relancer le calcul en conservant les filtres de vue")
        self._refresh_button.connect("clicked", lambda *_: self.refresh())
        controls.insert(self._refresh_button, -1)
        toolbox.append(controls)

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
            self._unsubscribe = subscribe(self._reload_identities)

    def _labeled(self, label: str, widget: Any):
        box = self.Gtk.Box(orientation=self.Gtk.Orientation.HORIZONTAL, spacing=5)
        box.append(self.Gtk.Label(label=label))
        box.append(widget)
        return box

    def _render(self, preserve_summary: bool = False) -> None:
        visible = [row for row in self._ranked if self._matches(row)]
        if self._ranked and not preserve_summary:
            self._summary.set_label(
                f"{len(visible)} affichée(s) sur {len(self._ranked)} recommandation(s)"
            )

        rows = []
        mapping = {}
        for index, ranked in enumerate(visible):
            item = ranked.item
            key = candidate_key(item) or f"recommendation-{index}"
            # A duplicated catalogue identity should map to the same selected
            # recommendation; add an index only if the current view still collides.
            if key in mapping:
                key = f"{key}:{index}"
            mapping[key] = ranked
            author = str(get_field(item, "author", "") or "").strip()
            reasons = " · ".join(ranked.reasons)
            source = ", ".join(ranked.sources)
            rows.append(
                UiBookListEntry(
                    key=key,
                    title=str(get_field(item, "title", "") or "Sans titre"),
                    subtitle=author,
                    meta=("Pourquoi : " + reasons) if reasons else "",
                    badge=f"{ranked.score}/100" + (f" · {source}" if source else ""),
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

    def _selected_row(self, row: UiBookListEntry | None) -> None:
        if row is None:
            self._master.empty_detail(
                "Aucune recommandation sélectionnée",
                "Sélectionnez un livre dans la liste pour afficher ses détails.",
            )
            return
        ranked = self._ranked_by_key.get(row.key)
        if ranked is None:
            return
        item = ranked.item
        title = str(get_field(item, "title", "") or "Sans titre")
        author = str(get_field(item, "author", "") or "").strip()
        fmt = str(get_field(item, "format", "") or "").strip()
        published = str(get_field(item, "published_at", "") or "").strip()
        sources = ", ".join(ranked.sources)
        reasons = " · ".join(ranked.reasons)
        description = str(
            get_field(item, "description", "")
            or get_field(item, "summary", "")
            or ""
        ).strip()

        recommendation_fields = [
            DetailField("score", "Score", f"{ranked.score}/100"),
            DetailField("reasons", "Pourquoi", reasons or "Plusieurs signaux concordants"),
        ]
        catalogue_fields = []
        if sources:
            catalogue_fields.append(DetailField("source", "Source", sources))
        if fmt:
            catalogue_fields.append(DetailField("format", "Format", fmt))
        if published:
            catalogue_fields.append(DetailField("published", "Publication", published))
        sections = [
            DetailSection("recommendation", "Recommandation", tuple(recommendation_fields), 10),
        ]
        if catalogue_fields:
            sections.append(DetailSection("catalogue", "Catalogue", tuple(catalogue_fields), 20))
        if description:
            sections.append(
                DetailSection(
                    "notice",
                    "Notice",
                    (DetailField("description", "Description", description),),
                    30,
                )
            )

        actions = []
        url = str(get_field(item, "friendly_url", "") or "").strip()
        if url.startswith(("http://", "https://")):
            actions.append(
                UiBookDetailAction(
                    "recommendation.notice",
                    "Ouvrir la notice",
                    "external-link-symbolic",
                    uri=url,
                )
            )

        thumbnail = str(
            get_field(item, "thumbnail_url", "")
            or get_field(item, "cover_url", "")
            or ""
        ) or None
        badges = [f"Score {ranked.score}/100"]
        if sources:
            badges.append(sources)
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


class RecommendationsPluginV023(RecommendationsPlugin):
    def build(self, host):
        from gi.repository import GLib, Gtk

        view = MasterDetailRecommendationsView(self, host, Gtk, GLib)
        self._view = view
        GLib.idle_add(view.refresh)
        return view.widget


PLUGIN = RecommendationsPluginV023()
