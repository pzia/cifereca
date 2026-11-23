"""#2385: built-in public C catalogue page with worker-backed SQLite search.

Never calls a provider, never uses a profile-specific catalogue mirror, and
never queries SQLite on the GTK thread. Each request has a generation fence.
"""
from __future__ import annotations

from threading import Thread

from ..core.notice_c_catalogue_query import search_public_catalogue_view
from .notice_c_public_catalogue_widget import render_public_catalogue_view
from .notice_c_public_catalogue_web_actions import append_public_catalogue_web_actions

_MODES = (
    ("list", "Liste"), ("grid", "Grille"), ("compact", "Compact"),
    ("detail", "Détail"), ("sources", "Sources"), ("timeline", "Chronologie"),
)


def build_public_catalogue_page(window, *, Gtk, GLib):
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    query = Gtk.Entry()
    query.set_placeholder_text("Rechercher dans les notices publiques")
    root.append(query)
    modes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    root.append(modes)
    status = Gtk.Label(label="Saisissez au moins deux caractères")
    status.set_xalign(0)
    root.append(status)
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    scroller = Gtk.ScrolledWindow()
    scroller.set_vexpand(True)
    scroller.set_child(content)
    root.append(scroller)
    state = {"generation": 0, "mode": "list", "page": 0,
             "field": "all", "scope": "all", "age_kind": None}

    age_filters = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    age_entry = Gtk.Entry()
    age_entry.set_placeholder_text("Âge observé 0–17 (facultatif)")
    age_filters.append(age_entry)
    root.append(age_filters)

    def identity_epoch():
        return (getattr(window.service, "_account_context_epoch", None),
                getattr(window.service, "_source_context_epoch", None))

    def deliver(generation, mode, page, epoch, view):
        if (getattr(window, "_closed", False)
                or state["generation"] != generation
                or epoch != identity_epoch()):
            return False
        child = content.get_first_child()
        while child is not None:
            content.remove(child)
            child = content.get_first_child()
        if view is None:
            status.set_text("Catalogue indisponible · réessayez")
            return False
        count = view.known_documents if view.known_documents is not None else len(view.rows)
        status.set_text(f"{count} notice(s) C · {view.known_matches} source(s) · {view.status}")
        if not view.rows:
            empty = Gtk.Label(label="Aucune notice publique correspondante dans l’index local")
            empty.set_xalign(0)
            content.append(empty)
        else:
            content.append(render_public_catalogue_view(view, Gtk=Gtk))
            if mode in ("detail", "sources"):
                append_public_catalogue_web_actions(
                    window, content, view.rows, Gtk=Gtk, GLib=GLib,
                    current=lambda: (not getattr(window, "_closed", False)
                                     and state["generation"] == generation
                                     and epoch == identity_epoch()),
                )
        if page:
            previous = Gtk.Button(label="Page précédente")
            previous.connect("clicked", lambda _: set_page(page - 1))
            content.append(previous)
        if view.has_more:
            following = Gtk.Button(label="Page suivante")
            following.connect("clicked", lambda _: set_page(page + 1))
            content.append(following)
        return False

    def clear_content():
        child = content.get_first_child()
        while child is not None:
            content.remove(child)
            child = content.get_first_child()

    def refresh():
        state["generation"] += 1
        generation = state["generation"]
        mode, page = state["mode"], state["page"]
        field, scope = state["field"], state["scope"]
        epoch = identity_epoch()
        text = query.get_text().strip()
        age_text = age_entry.get_text().strip()
        if len(text) < 2:
            clear_content()
            status.set_text("Saisissez au moins deux caractères")
            return
        if age_text and (not age_text.isascii() or not age_text.isdecimal()
                         or len(age_text) > 2 or int(age_text) > 17):
            clear_content()
            status.set_text("Âge invalide · utilisez un nombre de 0 à 17")
            return
        age = int(age_text) if age_text else None
        age_kind = state["age_kind"] if age is not None else None
        status.set_text("Recherche dans le catalogue public…")

        def work():
            try:
                view = search_public_catalogue_view(
                    window.service.storage, text, mode=mode, page=page,
                    field=field, scope=scope, age=age, age_kind=age_kind,
                )
            except (ValueError, OSError, RuntimeError):
                view = None
            GLib.idle_add(deliver, generation, mode, page, epoch, view)

        Thread(target=work, daemon=True, name="public-c-catalogue").start()

    def set_page(page):
        state["page"] = max(0, page)
        refresh()

    def set_mode(mode):
        state["mode"], state["page"] = mode, 0
        refresh()

    def set_filter(kind, value):
        state[kind], state["page"] = value, 0
        refresh()

    for kind, choices in (
        ("field", (("all", "Tous champs"), ("title", "Titre"),
                   ("author", "Auteur"), ("publisher", "Éditeur"),
                   ("subject", "Sujet"), ("series", "Série"),
                   ("language", "Langue"))),
        ("scope", (("all", "Tous supports"), ("digital", "Numérique"),
                   ("physical", "Papier"), ("unknown", "Indéterminé"))),
    ):
        filters = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        root.append(filters)
        for value, label in choices:
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _, k=kind, v=value: set_filter(k, v))
            filters.append(button)

    for kind, label in ((None, "Tout type d’âge"),
                        ("reading", "Lecture"), ("interest", "Intérêt"),
                        ("publisher", "Éditeur"), ("recommended", "Recommandé")):
        button = Gtk.Button(label=label)
        button.connect("clicked", lambda _, k=kind: set_filter("age_kind", k))
        age_filters.append(button)

    search = Gtk.Button(label="Rechercher")
    search.connect("clicked", lambda _: set_page(0))
    root.append(search)
    query.connect("activate", lambda _: set_page(0))
    age_entry.connect("activate", lambda _: set_page(0))
    for mode, label in _MODES:
        button = Gtk.Button(label=label)
        button.connect("clicked", lambda _, selected=mode: set_mode(selected))
        modes.append(button)
    return root
