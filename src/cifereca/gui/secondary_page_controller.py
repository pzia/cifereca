"""GTK-independent constructors for History and Bookings shell pages (#1051).

Both pages use the existing MasterDetailPage; the window retains service,
callbacks and ownership. Gtk, MasterDetailPage, MultiSelect are injected.
No eager provider connection, scan, or subscription is introduced.
"""
from __future__ import annotations

from ..language import language_label


_HISTORY_LANGUAGES = ("fr", "en", "de", "es", "it", "pt", "nl")


def build_history_page(window, *, Gtk, MasterDetailPage, MultiSelect):
    if getattr(window, "_closed", False):
        return None
    page = MasterDetailPage(
        window, "Historique", "Documents précédemment empruntés",
        "Historique non chargé",
        "Ouvrez cet écran ou actualisez pour récupérer l'historique.",
    )
    # Display the actual backend total, never a progressively loaded page size.
    page.set_total_only_count(True)
    refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
    refresh.set_tooltip_text("Actualiser l'historique")
    refresh.connect("clicked", lambda _b: (
        window.refresh_history(True, refresh)
        if not getattr(window, "_closed", False) else None
    ))
    page.header_actions.append(refresh)
    window.history_languages = MultiSelect(
        "Langues",
        [(code, language_label(code)) for code in _HISTORY_LANGUAGES],
        window._rerender_history,
    )
    page.toolbar.append(window.history_languages)
    page.connect_selected(window._history_selected)
    page.scroll.get_vadjustment().connect(
        "value-changed", window._history_scroll_changed,
    )
    return page


def build_bookings_page(window, *, Gtk, MasterDetailPage):
    if getattr(window, "_closed", False):
        return None
    page = MasterDetailPage(
        window, "Réservations", "Réservations physiques et numériques",
        "Aucune réservation", "Vos réservations apparaîtront ici.",
    )
    refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
    refresh.set_tooltip_text("Actualiser les réservations")
    refresh.connect("clicked", lambda _b: (
        window.refresh_bookings(True, refresh)
        if not getattr(window, "_closed", False) else None
    ))
    page.header_actions.append(refresh)
    page.connect_selected(window._booking_selected)
    return page
