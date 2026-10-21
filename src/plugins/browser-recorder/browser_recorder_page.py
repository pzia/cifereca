"""Optional GTK technical page: no browser, account or network on activation."""
from __future__ import annotations

from pathlib import Path
from threading import Event, Semaphore

from cifereca.plugin_api import UiPageContribution
from browser_recorder import RecorderError, record, replay, site_origin, FILENAME


_MESSAGES = {
    "playwright_unavailable": "Playwright/Chromium indisponibles : ouvrir Plugins → Browser Recorder → Installer les dépendances Python / Installer Chromium.",
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
        self.save_requested = None
        self.mode = None
        self.generation = 0
        self.last_filename = ""
        self.private_panel = None

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

    def _report_legacy_failure(self, mode: str, code: object) -> None:
        """Report only fixed, Core-scoped HAR outcomes after an explicit action.

        Neither exception text nor URL/filename is sent to Diagnostics.
        Plugin activation, page rendering and a declined consent stay silent.
        """
        operation = "har_record" if mode == "record" else "har_replay"
        if type(code) is not str:
            code = None
        if code == "playwright_unavailable":
            safe_code = "playwright_missing"
        elif mode == "record" and code == "capture_interrupted":
            safe_code = "capture_interrupted"
        else:
            safe_code = "har_record_failed" if mode == "record" else "har_replay_failed"
        try:
            report = getattr(self.context, "report_diagnostic", None)
            if callable(report):
                report(
                    "browser recorder user action", "error",
                    detail={"operation": operation, "code": safe_code},
                    level="normal",
                )
        except Exception:
            # An optional diagnostics broker must not break the GTK action.
            pass

    def build(self, host):
        from gi.repository import Gtk, GLib
        from browser_recorder_review_ui import PrivateReviewPanel

        if self.private_panel is not None:
            self.private_panel.destroy()
            self.private_panel = None
        # Plugin pages are inserted directly in Core's ViewStack: there is
        # no guaranteed Core-level scroll container. Keep the entire review,
        # including the exact ZIP and its confirmation, keyboard/mouse reachable.
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_hexpand(True)
        scroller.set_vexpand(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_hexpand(True)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + side)(16)

        description = Gtk.Label(
            label="Capture HTTP privée → revue individuelle → ZIP pour analyse "
                  "technique. Navigation manuelle dans Chromium visible. "
                  "Les données privées restent en mémoire jusqu'à effacement ; "
                  "aucun ZIP ni envoi externe automatique.",
            xalign=0, wrap=True,
        )
        box.append(description)

        site = Gtk.Entry()
        site.set_placeholder_text("https://site-fournisseur.example/")
        site.set_tooltip_text("Site HTTPS dont le parcours doit être capturé")
        box.append(site)

        consent = Gtk.CheckButton(label="Je consens à ouvrir un navigateur et à capturer ce parcours.")
        box.append(consent)

        workflow = Gtk.Label(
            label="1 · Préparer site HTTPS et consentement   →   "
                  "2 · Capturer / pause / arrêt   →   "
                  "3 · Revoir chaque identifiant   →   "
                  "4 · Examiner le ZIP exact   →   5 · Exporter localement",
            xalign=0, wrap=True,
        )
        workflow.add_css_class("heading")
        box.append(workflow)
        self.private_panel = PrivateReviewPanel(
            Gtk, GLib, self.tasks, site, consent,
            legacy_running=lambda: self.handle is not None and not self.handle.done(),
            export_directory=self.context.data_directory / "exports",
            # The Core binds this callback to the installed plugin identity.
            # Never forward a provider value, URL, exception or trace body.
            diagnostic=lambda operation, code: self.context.report_diagnostic(
                "browser recorder user action", "error",
                detail={"operation": operation, "code": code}, level="normal",
            ),
        )
        box.append(self.private_panel.widget)

        advanced = Gtk.Expander(label="Fonctions avancées / héritées — HAR expurgé et rejeu GET")
        advanced.set_expanded(False)
        legacy_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        advanced.set_child(legacy_box)
        legacy_box.append(Gtk.Label(
            label="Mode secondaire : HAR sans corps ni cookies, rejeu limité aux "
                  "GET statiques et confirmé étape par étape. "
                  "Ne produit PAS le ZIP HTTP privé destiné à l'analyse.",
            xalign=0, wrap=True,
        ))

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        start = Gtk.Button(label="Démarrer la capture")
        stop = Gtk.Button(label="Arrêter / enregistrer")
        replay_button = Gtk.Button(label="Rejouer")
        next_button = Gtk.Button(label="Étape GET suivante")
        for button in (start, stop, replay_button, next_button):
            row.append(button)
        legacy_box.append(row)

        filename = Gtk.Entry()
        filename.set_placeholder_text("Nom du fichier .har produit (sans chemin)")
        filename.set_text(self.last_filename)
        legacy_box.append(filename)

        state = Gtk.Label(label="Inactif — aucune capture ni accès réseau.", xalign=0, wrap=True)
        state.set_selectable(True)
        legacy_box.append(state)

        directory = self.context.data_directory / "captures"
        destination = Gtk.Label(label="Exports locaux privés : " + str(directory),
                                xalign=0, wrap=True)
        destination.set_selectable(True)
        legacy_box.append(destination)
        box.append(advanced)

        def buttons(mode=None):
            active = mode in {"record", "replay"}
            private_active = (self.private_panel is not None
                              and self.private_panel.is_recording())
            start.set_sensitive(not active and not private_active)
            replay_button.set_sensitive(not active and not private_active)
            stop.set_sensitive(active)
            next_button.set_sensitive(mode == "replay")

        buttons()

        def launch(mode):
            if self.context is None or self.tasks is None:
                return
            if self.handle is not None and not self.handle.done():
                return
            if self.private_panel is not None:
                if self.private_panel.is_recording():
                    return
                if self.private_panel.has_private_data():
                    state.set_label(
                        "Effacer d'abord la capture HTTP privée avant le mode HAR.",
                    )
                    return
            if not consent.get_active():
                state.set_label("Consentement explicite requis.")
                return
            try:
                origin = site_origin(site.get_text().strip())
                start_url = site.get_text().strip()
            except RecorderError as exc:
                code = exc.args[0] if len(exc.args) == 1 and type(exc.args[0]) is str else None
                state.set_label(_MESSAGES.get(code, "Site invalide."))
                return
            capture_name = filename.get_text().strip()
            if mode == "replay" and not FILENAME.fullmatch(capture_name):
                state.set_label("Nom de capture .har invalide.")
                return
            self.generation += 1
            generation = self.generation
            self.stop_event = Event()
            self.advance = Semaphore(0)
            self.save_requested = Event()
            self.mode = mode
            stop_event, advance, save_requested = self.stop_event, self.advance, self.save_requested
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
                    return record(start_url, directory, stop_event,
                                  save_requested=save_requested, status=signal)
                return replay(directory, capture_name, origin, stop_event, advance, status=signal)

            try:
                handle = self.tasks.submit(
                    task, kind="browser-recorder",
                    label="Capture Web volontaire" if mode == "record"
                    else "Rejeu Web supervisé",
                )
            except Exception:
                # A failed task broker must not strand the HAR controls.
                self.stop_event = None
                self.advance = None
                self.save_requested = None
                self.mode = None
                buttons()
                state.set_label(
                    "Opération indisponible : consultez Diagnostics et "
                    "Plugins → Browser Recorder → Vérifier Chromium…",
                )
                self._report_legacy_failure(mode, None)
                return
            self.handle = handle

            def finished(done_handle):
                try:
                    result = done_handle.result()
                    message = None
                    failure_code = None
                except RecorderError as exc:
                    result = None
                    code = (exc.args[0] if len(exc.args) == 1
                            and type(exc.args[0]) is str else None)
                    message = _MESSAGES.get(
                        code,
                        "Opération refusée : consultez Diagnostics et vérifiez "
                        "Plugins → Browser Recorder → Vérifier Chromium…",
                    )
                    failure_code = code
                except Exception:
                    result = None
                    failure_code = None
                    message = (
                        "Navigateur indisponible : ouvrez Plugins → "
                        "Browser Recorder → Vérifier Chromium… ; "
                        "consultez ensuite Diagnostics."
                    )

                def display():
                    if self.context is None or self.generation != generation:
                        return False
                    if self.handle is done_handle:
                        self.handle = None
                    self.stop_event = None
                    self.advance = None
                    self.save_requested = None
                    self.mode = None
                    buttons()
                    if message:
                        state.set_label(message)
                        self._report_legacy_failure(mode, failure_code)
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
        def stop_clicked(*_):
            if self.stop_event is not None:
                if self.mode == "record" and self.save_requested is not None:
                    self.save_requested.set()
                self.stop_event.set()

        stop.connect("clicked", stop_clicked)
        next_button.connect("clicked", lambda *_: self.advance.release() if self.advance else None)

        scroller.set_child(box)
        return scroller

    def deactivate(self):
        if self.private_panel is not None:
            self.private_panel.destroy()
            self.private_panel = None
        self.generation += 1
        if self.stop_event is not None:
            self.stop_event.set()
        self.stop_event = None
        self.advance = None
        self.save_requested = None
        self.mode = None
        self.handle = None
        self.tasks = None
        self.context = None


PLUGIN = BrowserRecorderPage()

