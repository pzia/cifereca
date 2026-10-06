from __future__ import annotations

import threading

from recommendation_service_v022 import RecommendationPartial, RecommendationResult, compute_recommendations
from recommendations_v021 import ProgressRecommendationsView, RecommendationsPluginV021


class RecommendationTaskControl:
    """Small public TaskControl implementation used to stop stale catalog work."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check_cancelled(self) -> None:
        if self.cancelled():
            raise RuntimeError("recommendation task cancelled")

    def report(self, *, current=None, total=None, message: str = "") -> None:
        # Progress is already supplied by CatalogSearchBatch and is deliberately
        # not sent to GTK from a provider/worker callback.
        return None


class StreamingRecommendationsView(ProgressRecommendationsView):
    """0.2.2: cancellable worker, progressive results and cooperative GTK render."""

    RENDER_CHUNK = 8
    CLEAR_CHUNK = 20
    PARTIAL_COALESCE_MS = 180

    def __init__(self, plugin, host, Gtk, GLib):
        super().__init__(plugin, host, Gtk, GLib)
        self._task_control: RecommendationTaskControl | None = None
        self._pending_partial: RecommendationPartial | None = None
        self._partial_source_id = 0
        self._render_generation = 0

    def refresh(self):
        # Cancel the previous Core progressive search before starting another one.
        if self._task_control is not None:
            self._task_control.cancel()

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
        self._task_control = RecommendationTaskControl()
        control = self._task_control
        self._busy = True
        self._refresh_button.set_sensitive(False)
        self._spinner.start()
        self._progress.set_fraction(0.0)
        self._progress.set_text("0 % · Lecture de la bibliothèque")
        self._summary.set_label("Lecture de la projection locale de la bibliothèque…")
        exploration = int(self._exploration.get_value())

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
            # The Core callback stays GTK-free. Crossing to GTK is always done
            # with idle_add and partial snapshots are coalesced afterwards.
            self.GLib.idle_add(self._accept_partial, generation, snapshot)

        def worker() -> None:
            try:
                result = compute_recommendations(
                    metadata_broker,
                    catalog_broker,
                    identity_id,
                    scope=self._config_str("scope", "digital"),
                    max_queries=self._config_int("max_queries", 8),
                    per_query=self._config_int("per_query", 50),
                    max_results=self._config_int("max_results", 120),
                    exploration=exploration,
                    progress=progress,
                    partial=partial,
                    control=control,
                )
                self.GLib.idle_add(self._complete, generation, result, None)
            except Exception as exc:
                if not control.cancelled():
                    self.GLib.idle_add(self._complete, generation, None, str(exc))

        threading.Thread(
            target=worker,
            name="cifereca-recommendations",
            daemon=True,
        ).start()
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
        snapshot = self._pending_partial
        self._pending_partial = None
        self._ranked = list(snapshot.ranked)
        self._summary.set_label(
            f"Résultats provisoires · {len(snapshot.ranked)} affichables · "
            f"requête {snapshot.query_index}/{snapshot.query_total} · "
            f"catalogues {snapshot.provider_done}/{snapshot.provider_total} · "
            f"{snapshot.results_seen} résultats reçus · "
            f"cache {snapshot.cache_batches} / réseau {snapshot.network_batches}"
        )
        self._render(preserve_summary=True)
        return False

    def _render(self, preserve_summary: bool = False) -> None:
        """Render cooperatively so GTK never builds the whole result set at once."""
        self._render_generation += 1
        render_generation = self._render_generation
        visible = [row for row in self._ranked if self._matches(row)]
        if self._ranked and not preserve_summary:
            self._summary.set_label(
                f"{len(visible)} affichée(s) sur {len(self._ranked)} recommandation(s)"
            )

        state = {"phase": "clear", "index": 0}

        def step() -> bool:
            if render_generation != self._render_generation:
                return False
            if state["phase"] == "clear":
                for _ in range(self.CLEAR_CHUNK):
                    child = self._list.get_first_child()
                    if child is None:
                        state["phase"] = "append"
                        break
                    self._list.remove(child)
                if state["phase"] == "clear":
                    return True

            start = state["index"]
            stop = min(len(visible), start + self.RENDER_CHUNK)
            for ranked in visible[start:stop]:
                self._list.append(self._build_row(ranked))
            state["index"] = stop
            return stop < len(visible)

        self.GLib.idle_add(step)

    def _complete(
        self,
        generation: int,
        result: RecommendationResult | None,
        error: str | None,
    ):
        if generation != self._generation:
            return False
        self._pending_partial = None
        if self._partial_source_id:
            try:
                self.GLib.source_remove(self._partial_source_id)
            except Exception:
                pass
            self._partial_source_id = 0
        return super()._complete(generation, result, error)

    def dispose(self) -> None:
        if self._task_control is not None:
            self._task_control.cancel()
        self._task_control = None
        self._pending_partial = None
        self._render_generation += 1
        if self._partial_source_id:
            try:
                self.GLib.source_remove(self._partial_source_id)
            except Exception:
                pass
            self._partial_source_id = 0
        super().dispose()


class RecommendationsPluginV022(RecommendationsPluginV021):
    def build(self, host):
        from gi.repository import GLib, Gtk

        view = StreamingRecommendationsView(self, host, Gtk, GLib)
        self._view = view
        GLib.idle_add(view.refresh)
        return view.widget


PLUGIN = RecommendationsPluginV022()
