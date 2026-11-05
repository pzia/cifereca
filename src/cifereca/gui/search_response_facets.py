"""Read-only facets reported by the current online catalogue response."""
from __future__ import annotations

from itertools import groupby

from gi.repository import Gtk


def _label(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0, wrap=True)
    label.set_max_width_chars(55)
    return label


def _page_key(facet):
    return facet.scenario, facet.page


class SearchResponseFacets(Gtk.MenuButton):
    def __init__(self) -> None:
        super().__init__(label="Facettes reçues", visible=False)
        self._context = None
        self._sources = {}
        self._shown = ()
        self._popover = Gtk.Popover()
        self.set_popover(self._popover)

    def begin(self, context) -> None:
        self._context = context
        self._sources.clear()
        self._show(())

    def receive(self, batch) -> None:
        facets = getattr(batch, "response_facets", ())
        if self._context is not None and facets:
            self._sources[batch.provider_id] = (batch.provider_label, facets)

    def refresh(self, context, providers) -> None:
        if context != self._context:
            self.begin(None)
        elif self._context is not None:
            self._show(tuple(
                source for provider, source in sorted(self._sources.items())
                if not providers or provider in providers
            ))

    def _show(self, sources) -> None:
        if sources == self._shown:
            return
        self._shown = sources
        self._popover.popdown()
        if not sources:
            self.set_visible(False)
            self._popover.set_child(None)
            return

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for margin in ("top", "bottom", "start", "end"):
            content.set_property(f"margin-{margin}", 12)
        content.append(_label(
            "Décomptes de la réponse en ligne, par scénario et par page. "
            "Filtrage distant non vérifié."
        ))
        for source, facets in sources:
            for (scenario, page), group in groupby(sorted(facets, key=_page_key), key=_page_key):
                heading = _label(f"{source} · {scenario} · page {page + 1}")
                heading.add_css_class("heading")
                content.append(heading)
                for facet in group:
                    if facet.values is None:
                        content.append(_label(f"{facet.label} · valeurs non vérifiées"))
                        continue
                    expander = Gtk.Expander(label=facet.label, use_markup=False)
                    details = "\n".join(
                        f"{value.display_label or value.label} · {value.count}"
                        + (" · sélection signalée" if value.is_selected else "")
                        for value in facet.values
                    )
                    expander.set_child(_label(details))
                    content.append(expander)
        scroll = Gtk.ScrolledWindow(min_content_width=280, max_content_height=400)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_child(content)
        self._popover.set_child(scroll)
        self.set_visible(True)
