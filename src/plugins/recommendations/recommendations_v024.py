from __future__ import annotations

import re
from typing import Any

from cifereca.plugin_api import (
    DetailField,
    DetailSection,
    UiBookDetailAction,
    UiBookDetailPresentation,
    UiBookListEntry,
)

from recommendation_engine import candidate_key, get_field
from recommendation_service_v022 import RecommendationPartial, RecommendationResult
from recommendations import RecommendationsPlugin
from recommendations_v023 import MasterDetailRecommendationsView


class UxRecommendationsView(MasterDetailRecommendationsView):
    """0.2.4: E2E UX recommendations on top of the standard master/detail view."""

    def __init__(self, plugin, host, Gtk, GLib):
        # These fields are deliberately ready before the parent reloads identities:
        # Gtk.DropDown::notify::selected may call refresh() during construction.
        self._provider_labels: dict[str, str] = {"": "Tous les catalogues"}
        self._provisional_order: dict[str, int] = {}
        self._provisional_active = False
        self._profile_books_count = 0
        self._active_identity_id: str | None = None
        self._diagnostic_cache = 0
        self._diagnostic_network = 0
        super().__init__(plugin, host, Gtk, GLib)

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
        super()._reload_providers(providers)

    def refresh(self):
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

        value = super().refresh()
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
        text_filter = str(self._text.get_text() or "").strip()
        filters = []
        if text_filter:
            filters.append(f"« {text_filter} »")
        if provider:
            filters.append(provider)

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

    def _complete(
        self,
        generation: int,
        result: RecommendationResult | None,
        error: str | None,
    ):
        value = super()._complete(generation, result, error)
        if generation != self._generation:
            return value

        self._set_provisional(False)
        if error or result is None:
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
        return value


class RecommendationsPluginV024(RecommendationsPlugin):
    def build(self, host):
        from gi.repository import GLib, Gtk

        view = UxRecommendationsView(self, host, Gtk, GLib)
        self._view = view
        GLib.idle_add(view.refresh)
        return view.widget


PLUGIN = RecommendationsPluginV024()
