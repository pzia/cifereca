"""Optional GTK technical page: no browser, account or network on activation."""
from __future__ import annotations

from pathlib import Path
from threading import Event, Semaphore

from cifereca.plugin_api import UiPageContribution
from browser_recorder import RecorderError, record, replay, site_origin, FILENAME


_MESSAGES = {
    "playwright_unavailable": "Playwright absent : installer playwright puis Chromium (voir README).",
    "invalid_origin": "Utiliser une URL HTTPS publique valide (sans identifiants).",
    "invalid_filename": "Nom de capture invalide.",
    "read_failed": "Capture introuvable ou illisible.",
    "unsafe_directory": "Dossier de capture non sûr.",
    "invalid_capture": "Capture invalide.",
    "capture_origin_mismatch": "Capture provenant d'un autre site.",
    "capture_limit": "Limite de taille de capture atteinte.",
    "save_failed": "Enregistrement de la capture impossible.",
    "capture_interrupted": "Navigateur fermé avant arrêt de la capture.",
}


class BrowserRecorderPage:
    def __init__(self):
        self.context = None
        self.tasks = None
        self.handle = None
        self.stop_event = None
        self.advance = None
        self.generation = 0
        self.last_filename = ""

    def activate(self, context):
        self.context = context
        self.tasks = context.broker("tasks")
        if self.tasks is None:
            raise RuntimeError("Broker public tasks requis")
        return self

    def pages(self):
        return [UiPageContribution(
            route="browser-recorder",
            title="Browser Recorder",
            icon_name="web-browser-symbolic",
            order=96,
            factory=self.build,
            group="configuration",
        )]

    def build(self, host):
        from gi.repository import Gtk, GLib

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + side)(16)

        description = Gtk.Label(
            label="Capture locale d'un navigateur Playwright visible. "
                  "Connectez-vous vous-même ; aucune session ou requête brute n'est conservée. "
                  "Les URL sont expurgées, les en-têtes, cookies et corps exclus. "
                  "Le rejeu n'effectue que des GET statiques après votre confirmation.",
            xalign=0, wrap=True,
        )
        box.append(description)

        site = Gtk.Entry()
        site.set_placeholder_text("https://site-fournisseur.example/")
        site.set_tooltip_text("Site HTTPS dont le parcours doit être capturé")
        box.append(site)

        consent = Gtk.CheckButton(label="Je consens à ouvrir un navigateur et à capturer ce parcours.")
        box.append(consent)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        start = Gtk.Button(label="Démarrer la capture")
        stop = Gtk.Button(label="Arrêter / enregistrer")
        replay_button = Gtk.Button(label="Rejouer")
        next_button = Gtk.Button(label="Étape GET suivante")
        for button in (start, stop, replay_button, next_button):
            row.append(button)
        box.append(row)

        filename = Gtk.Entry()
        filename.set_placeholder_text("Nom du fichier .har produit (sans chemin)")
        filename.set_text(self.last_filename)
        box.append(filename)

        state = Gtk.Label(label="Inactif — aucune capture ni accès réseau.", xalign=0, wrap=True)
        state.set_selectable(True)
        box.append(state)

        directory = self.context.data_directory / "captures"
        destination = Gtk.Label(label="Exports locaux privés : " + str(directory),
                                xalign=0, wrap=True)
        destination.set_selectable(True)
        box.append(destination)

        def buttons(mode=None):
            active = mode in {"record", "replay"}
            start.set_sensitive(not active)
            replay_button.set_sensitive(not active)
            stop.set_sensitive(active)
            next_button.set_sensitive(mode == "replay")

        buttons()

        def launch(mode):
            if self.context is None or self.tasks is None:
                return
            if self.handle is not None and not self.handle.done():
                return
            if not consent.get_active():
                state.set_label("Consentement explicite requis.")
                return
            try:
                origin = site_origin(site.get_text().strip())
                start_url = site.get_text().strip()
            except RecorderError as exc:
                state.set_label(_MESSAGES.get(str(exc), "Site invalide."))
                return
            capture_name = filename.get_text().strip()
            if mode == "replay" and not FILENAME.fullmatch(capture_name):
                state.set_label("Nom de capture .har invalide.")
                return
            self.generation += 1
            generation = self.generation
            self.stop_event = Event()
            self.advance = Semaphore(0)
            stop_event, advance = self.stop_event, self.advance
            buttons(mode)
            state.set_label("Ouverture du navigateur ; connexion manuelle possible…")

            def signal(value):
                if value == "capture_ready":
                    label = "Capture en cours. Connectez-vous dans le navigateur. Arrêtez ici pour exporter."
                elif value == "replay_ready":
                    label = "Rejeu prêt. Connectez-vous manuellement, puis confirmez chaque GET."
                else:
                    label = "Étape GET validée. Confirmer la suivante, ou arrêter."
                def deliver():
                    if self.context is not None and self.generation == generation:
                        state.set_label(label)
                    return False
                GLib.idle_add(deliver)

            def task(control):
                if mode == "record":
                    return record(start_url, directory, stop_event, status=signal)
                return replay(directory, capture_name, origin, stop_event, advance, status=signal)

            handle = self.tasks.submit(task, kind="browser-recorder",
                                       label="Capture Web volontaire" if mode == "record"
                                       else "Rejeu Web supervisé")
            self.handle = handle

            def finished(done_handle):
                try:
                    result = done_handle.result()
                    message = None
                except RecorderError as exc:
                    result = None
                    message = _MESSAGES.get(str(exc), "Opération refusée (code : " + str(exc) + ").")
                except Exception:
                    result = None
                    message = "Navigateur indisponible ; consulter l'installation Playwright."

                def display():
                    if self.context is None or self.generation != generation:
                        return False
                    if self.handle is done_handle:
                        self.handle = None
                    self.stop_event = None
                    self.advance = None
                    buttons()
                    if message:
                        state.set_label(message)
                    elif mode == "record":
                        self.last_filename = str(result)
                        filename.set_text(self.last_filename)
                        state.set_label("Capture expurgée enregistrée. Nom : " + self.last_filename)
                    else:
                        state.set_label("Rejeu terminé ; " + str(result) + " étapes GET confirmées.")
                    return False

                GLib.idle_add(display)

            handle.add_done_callback(finished)

        start.connect("clicked", lambda *_: launch("record"))
        replay_button.connect("clicked", lambda *_: launch("replay"))
        stop.connect("clicked", lambda *_: self.stop_event.set() if self.stop_event else None)
        next_button.connect("clicked", lambda *_: self.advance.release() if self.advance else None)
        return box

    def deactivate(self):
        self.generation += 1
        if self.stop_event is not None:
            self.stop_event.set()
        self.stop_event = None
        self.advance = None
        self.handle = None
        self.tasks = None
        self.context = None


PLUGIN = BrowserRecorderPage()

