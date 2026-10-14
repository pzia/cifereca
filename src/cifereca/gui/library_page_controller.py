"""Library and Recent GTK page assembly with explicit widget dependencies (#1129).

The MainWindow facade retains all state and callbacks.  This module does not
import the GTK runtime or MainWindow and starts no scans or downloads.
"""
from __future__ import annotations

from typing import Any


def build_library_page(window, *, Gtk, MasterDetailPage):
    page = MasterDetailPage(window, "Bibliothèque", "", "Bibliothèque vide", "Fichiers locaux, livres possédés et empruntés.")
    window.library_filter = "all"
    controls = window._query_controls(lambda: window._render_library(), lambda: window._render_library(), library=True)
    window.library_controls = controls; window.library_filter_entry = controls.entry
    page.toolbar.append(controls)
    search = Gtk.Button(label="Recherche"); search.connect("clicked", window._library_search_online); controls.primary.append(search)
    actions = page.header_actions
    refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
    refresh.set_tooltip_text("Rescanner la bibliothèque")
    refresh.connect("clicked", lambda _b: window.refresh_library(True, refresh))
    actions.append(refresh)

    folder = Gtk.Button.new_from_icon_name("folder-open-symbolic")
    folder.set_tooltip_text("Ouvrir le dossier de la bibliothèque")
    folder.connect("clicked", lambda _b: window.open_library_folder())
    actions.append(folder)

    advanced = Gtk.MenuButton(label="Opérations…")
    advanced.set_tooltip_text("Opérations avancées et maintenance de la bibliothèque")
    advanced_popover = Gtk.Popover()
    advanced_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    advanced_box.set_margin_top(8); advanced_box.set_margin_bottom(8)
    advanced_box.set_margin_start(8); advanced_box.set_margin_end(8)

    covers = Gtk.Button(label="Réinspecter les couvertures")
    covers.set_tooltip_text("Réinspecter toutes les couvertures EPUB locales, sans téléchargement")
    covers.connect("clicked", lambda _b: window.refresh_library(True, covers, refresh_covers=True))
    advanced_box.append(covers)

    acsm = Gtk.Button(label="Importer un ACSM…")
    acsm.set_tooltip_text("Importer manuellement un fichier ACSM dans la bibliothèque")
    acsm.connect("clicked", lambda _b: window.choose_acsm())
    advanced_box.append(acsm)

    # Library maintenance must never launch a global Kobo acquisition.
    # The Synchronisation page exposes the source/identity-scoped actions.
    acquire = Gtk.Button(label="Gérer la synchronisation Kobo…")
    acquire.set_tooltip_text(
        "Ouvrir Synchronisation pour actualiser les notices et droits Kobo ; "
        "aucun téléchargement ne démarre depuis Bibliothèque."
    )
    acquire.connect("clicked", lambda _b: window.navigate("sync"))
    advanced_box.append(acquire)

    advanced_popover.set_child(advanced_box)
    advanced.set_popover(advanced_popover)
    actions.append(advanced)
    page.connect_selected(window._library_selected)
    window._library_objects = {}
    return page


def build_recent_page(window, *, Gtk, MasterDetailPage):
    page = MasterDetailPage(
        window, "Récupérations récentes",
        "Dernières matérialisations locales de l’identité courante",
        "Aucune récupération récente",
        "Les acquisitions locales apparaîtront ici.",
    )
    refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
    refresh.set_tooltip_text("Actualiser les récupérations récentes")
    refresh.connect("clicked", lambda _b: window.refresh_recent(refresh)); page.header_actions.append(refresh)
    page.connect_selected(window._recent_selected)
    window._recent_objects: dict[str, dict[str, Any]] = {}
    return page