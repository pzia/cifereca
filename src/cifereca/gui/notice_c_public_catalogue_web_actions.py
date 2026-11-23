"""#2385: explicit, source-fenced public web actions from the read-only C catalogue.

The GTK page never holds a trusted URL. Core resolves each opaque choice
again at click time in a worker, including the canonical C and source epochs.
"""
from __future__ import annotations

from threading import Thread

from ..core.notice_c_cover_index import is_canonical_cover_document
from ..core.notice_c_web_choices import (
    PublicWebChoices, read_public_web_choices, resolve_public_web_choice,
)


def _open_public_url(url):
    from gi.repository import Gio
    return Gio.AppInfo.launch_default_for_uri(url, None)


def _web_controls(window, document, title, *, Gtk, GLib, current):
    group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    load = Gtk.Button(label=f"Fiches web publiques · {title}")
    group.append(load)
    status = Gtk.Label(label="")
    status.set_xalign(0)
    group.append(status)
    choices_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    group.append(choices_box)
    state = {"generation": 0}

    def valid(generation):
        return (not getattr(window, "_closed", False)
                and current() and generation == state["generation"])

    def clear_choices():
        child = choices_box.get_first_child()
        while child is not None:
            choices_box.remove(child)
            child = choices_box.get_first_child()

    def deliver_url(generation, url):
        if not valid(generation):
            return False
        if type(url) is not str or not url:
            status.set_text("Fiche indisponible · actualisez les choix")
            return False
        try:
            if _open_public_url(url) is False:
                status.set_text("Impossible d’ouvrir la fiche")
        except (OSError, RuntimeError, ValueError):
            status.set_text("Impossible d’ouvrir la fiche")
        return False

    def choose(menu, choice, generation):
        if not valid(generation):
            return
        status.set_text("Vérification de la fiche publique…")

        def work():
            try:
                url = resolve_public_web_choice(
                    window.service.storage, document,
                    expected_epoch=menu.epoch, choice_id=choice.choice_id,
                )
            except (OSError, RuntimeError, ValueError):
                url = None
            GLib.idle_add(deliver_url, generation, url)

        Thread(target=work, daemon=True, name="public-c-web-resolve").start()

    def deliver_choices(generation, menu):
        if not valid(generation):
            return False
        clear_choices()
        if (type(menu) is not PublicWebChoices or menu.document != document
                or not menu.choices):
            status.set_text("Aucune fiche web publique qualifiée dans les sources connues")
            return False
        status.set_text(f"{len(menu.choices)} fiche(s) publique(s) connue(s)")
        for choice in menu.choices:
            button = Gtk.Button(label=choice.label)
            button.connect(
                "clicked", lambda _, item=choice: choose(menu, item, generation),
            )
            choices_box.append(button)
        return False

    def read(_):
        if not current() or getattr(window, "_closed", False):
            return
        state["generation"] += 1
        generation = state["generation"]
        clear_choices()
        status.set_text("Recherche des fiches web publiques…")

        def work():
            try:
                menu = read_public_web_choices(window.service.storage, document)
            except (OSError, RuntimeError, ValueError):
                menu = None
            GLib.idle_add(deliver_choices, generation, menu)

        Thread(target=work, daemon=True, name="public-c-web-choices").start()

    load.connect("clicked", read)
    return group


def append_public_catalogue_web_actions(window, content, rows, *, Gtk, GLib, current):
    """One choice menu per canonical C, even in the source-by-source display."""
    seen = set()
    for row in rows:
        document = row.document
        if not is_canonical_cover_document(document):
            continue
        if document.source_key in seen:
            continue
        seen.add(document.source_key)
        content.append(_web_controls(
            window, document, row.title, Gtk=Gtk, GLib=GLib, current=current,
        ))
