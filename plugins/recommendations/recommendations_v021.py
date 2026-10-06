from __future__ import annotations

import re

from recommendations import RecommendationsPlugin, RecommendationsView


class ProgressRecommendationsView(RecommendationsView):
    """0.2.1 UI: explicit determinate progress without duplicating the base page."""

    def __init__(self, plugin, host, Gtk, GLib):
        super().__init__(plugin, host, Gtk, GLib)
        self._progress = Gtk.ProgressBar()
        self._progress.set_show_text(True)
        self._progress.set_fraction(0.0)
        self._progress.set_text("0 % · Préparation")
        self._progress.set_tooltip_text(
            "Progression du calcul : requêtes × catalogues, puis classement et diversification"
        )
        # Base page children are header, controls, status, results: insert after controls.
        header = self.widget.get_first_child()
        controls = header.get_next_sibling() if header is not None else None
        self.widget.insert_child_after(self._progress, controls)

    def refresh(self):
        if hasattr(self, "_progress"):
            self._progress.set_fraction(0.0)
            self._progress.set_text("0 % · Préparation")
        return super().refresh()

    def _set_progress(self, generation: int, label: str) -> bool:
        if generation != self._generation:
            return False
        match = re.match(r"Recherche\s+(\d+)/(\d+)\s+·\s*(.*)", label or "")
        if match:
            current = int(match.group(1))
            total = max(1, int(match.group(2)))
            fraction = min(0.94, current / total * 0.94)
            self._progress.set_fraction(fraction)
            self._progress.set_text(f"{int(round(fraction * 100))} % · Recherche catalogues")
            self._summary.set_label(match.group(3) or label)
        elif label == "Classement des résultats…":
            self._progress.set_fraction(0.96)
            self._progress.set_text("96 % · Classement et diversification")
            self._summary.set_label(label)
        else:
            self._summary.set_label(label)
        return False

    def _complete(self, generation, result, error):
        value = super()._complete(generation, result, error)
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


class RecommendationsPluginV021(RecommendationsPlugin):
    def build(self, host):
        from gi.repository import GLib, Gtk
        view = ProgressRecommendationsView(self, host, Gtk, GLib)
        self._view = view
        GLib.idle_add(view.refresh)
        return view.widget


PLUGIN = RecommendationsPluginV021()
