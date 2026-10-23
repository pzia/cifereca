"""Explicit BnF-Prêt reader page, opt-in and read-only (#2055).

No provider.library registration, implicit SSO, rights ledger, ACSM or
Browser Recorder. Only fixed outcome codes/counts cross into the UI status.
"""
from __future__ import annotations

from cifereca.plugin_api import UiPageContribution
from cifereca_bnf_pret_worker import ReaderWorker, BrowserSessionError

_MESSAGES = {
    "playwright_unavailable": "Installer Playwright et son Chromium pour utiliser ce lecteur.",
    "browser_unavailable": "Impossible d'ouvrir Chromium sur ce poste.",
    "login_not_verified": "Compte ou page d'activité non reconnus : session fermée.",
    "auth_required": "Connexion BnF requise.",
    "access_refused": "Accès refusé par le site.",
    "rate_limited": "Le site limite les requêtes. Réessayez manuellement plus tard.",
    "unrecognized_dom": "Structure de page inconnue : aucune synchronisation effectuée.",
    "page_changed": "La page a changé pendant la lecture.",
    "session_closed": "Session terminée.",
    "scope_mismatch": "Identité changée : session interrompue.",
    "reader_busy": "Lecture déjà en cours.",
    "html_limit": "Page trop volumineuse : lecture partielle non publiée.",
    "dom_limit": "Page trop complexe : lecture refusée.",
}
_DEFAULT_ERROR = "Lecture BnF indisponible. Aucun droit modifié."


def selected_identity(rows):
    """A single Core-*selected* identity; never default to the first profile."""
    if type(rows) not in (list, tuple) or len(rows) > 128:
        return None
    selected = [r for r in rows if type(r) is dict and r.get("selected") is True]
    if len(selected) != 1:
        return None
    row = selected[0]
    identity = row.get("identity_id")
    if type(identity) is not str or not identity.strip() or len(identity) > 256:
        return None
    label = row.get("label")
    if type(label) is not str or not 1 <= len(label) <= 128:
        label = "Profil actif"
    return identity, label


def safe_error(exc):
    """Translate only an allowlisted adapter code, never print exception text."""
    if not isinstance(exc, BrowserSessionError):
        return _DEFAULT_ERROR
    code = exc.args[0] if len(exc.args) == 1 and type(exc.args[0]) is str else None
    return _MESSAGES.get(code, _DEFAULT_ERROR)


class BnfReaderPage:
    def __init__(self):
        self.context = None
        self.tasks = None
        self.worker = None
        self.handle = None
        self._generation = 0
        self._ui = None
        self._unsubscribe = None

    def activate(self, context):
        self.context = context
        self.tasks = context.broker("tasks")
        if self.tasks is None or context.broker("identities") is None:
            raise RuntimeError("bnf_reader_brokers_unavailable")
        return self

    def pages(self):
        return [UiPageContribution(
            route="bnf-pret-reader", title="BnF Prêt (lecture expérimentale)",
            icon_name="web-browser-symbolic", group="configuration",
            order=95, factory=self.build,
        )]

    def _stop(self):
        self._generation += 1
        worker, handle = self.worker, self.handle
        self.worker = self.handle = None
        if worker is not None:
            worker.stop()
        if handle is not None:
            try:
                handle.cancel()
            except Exception:
                pass

    def build(self, host):
        from gi.repository import Gtk, GLib

        self._stop()
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for edge in ("top", "bottom", "start", "end"):
            getattr(root, "set_margin_" + edge)(16)
        self._ui = root
        root.set_hexpand(True)

        root.append(Gtk.Label(
            label="Portail Web BnF Prêt : authentifiez-vous vous-même dans Chromium. "
                  "Lecture locale des pages uniquement : aucune création de prêt, "
                  "aucun téléchargement, aucun droit synchronisé avec Cifereca.",
            xalign=0, wrap=True,
        ))
        profile = Gtk.Label(label="", xalign=0, wrap=True)
        root.append(profile)
        consent = Gtk.CheckButton(
            label="J'autorise l'ouverture d'une session Chromium éphémère pour le profil actif."
        )
        root.append(consent)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        root.append(row)
        open_button = Gtk.Button(label="Ouvrir le portail BnF")
        confirm_button = Gtk.Button(label="J'ai terminé la connexion BnF")
        loans_button = Gtk.Button(label="Relire mes prêts")
        search_button = Gtk.Button(label="Lire les résultats visibles")
        stop_button = Gtk.Button(label="Fermer la session")
        for button in (open_button, confirm_button, loans_button, search_button, stop_button):
            row.append(button)
        status = Gtk.Label(label="Aucune connexion ni lecture lancée.", xalign=0, wrap=True)
        status.set_selectable(True)
        root.append(status)

        def current():
            try:
                return selected_identity(host.identity_overview())
            except Exception:
                return None

        current_profile = current()
        active_identity = current_profile[0] if current_profile else None
        busy = False

        def valid():
            return self._ui is root and self.context is not None

        def reset_buttons():
            worker = self.worker
            mode = worker.state if worker is not None else "closed"
            observed = current()
            same = observed is not None and observed[0] == active_identity
            open_button.set_sensitive(worker is None and same)
            consent.set_sensitive(worker is None)
            confirm_button.set_sensitive(same and mode == "login" and not busy)
            loans_button.set_sensitive(same and mode == "readonly" and not busy)
            search_button.set_sensitive(same and mode == "readonly" and not busy)
            stop_button.set_sensitive(worker is not None)

        def scope_ok(worker, scope, generation):
            now = current()
            return (valid() and worker is not None and self._generation == generation
                    and self.worker is worker and worker.scope == scope
                    and scope is not None and now is not None
                    and now[0] == scope.identity_id == active_identity)

        def sync_profile():
            nonlocal current_profile, active_identity, busy
            if not valid():
                return False
            updated = current()
            new_id = updated[0] if updated else None
            if new_id != active_identity:
                self._stop()
                busy = False
                active_identity = new_id
                status.set_label("Le profil actif a changé : ancienne session révoquée.")
            current_profile = updated
            profile.set_label("Profil Cifereca : " +
                              (updated[1] if updated else "aucun profil actif attesté"))
            reset_buttons()
            return False

        def command(name):
            nonlocal busy
            worker = self.worker
            scope = worker.scope if worker is not None else None
            generation = self._generation
            if busy or not scope_ok(worker, scope, generation):
                sync_profile()
                return
            busy = True
            reset_buttons()
            status.set_label("Lecture BnF en cours…")
            try:
                future = worker.submit(
                    name, expected=scope,
                    account_confirmed=(name == "confirm_account"),
                )
            except Exception as exc:
                busy = False
                status.set_label(safe_error(exc))
                reset_buttons()
                return

            def completed(fut):
                def show():
                    nonlocal busy
                    if not scope_ok(worker, scope, generation):
                        return False
                    busy = False
                    try:
                        result = fut.result()
                    except Exception as exc:
                        status.set_label(safe_error(exc))
                    else:
                        if name in ("confirm_account", "loans"):
                            count = len(result.loans)
                            status.set_label(
                                f"{count} prêt(s) visibles dans la page BnF ; "
                                "inventaire non exhaustif, aucun droit publié."
                            )
                        else:
                            status.set_label(
                                f"{len(result.records)} suggestion(s) visibles ; "
                                "ceci n'est pas une recherche exhaustive."
                            )
                    reset_buttons()
                    return False
                GLib.idle_add(show)
            future.add_done_callback(completed)

        def start(_button):
            nonlocal busy
            if self.worker is not None or busy:
                return
            profile_now = current()
            if profile_now is None or profile_now[0] != active_identity:
                sync_profile()
                return
            if consent.get_active() is not True:
                status.set_label("Consentement explicite nécessaire avant l'ouverture.")
                return
            consent.set_active(False)
            generation = self._generation + 1
            self._generation = generation
            try:
                worker = ReaderWorker(profile_now[0], "reader-web-confirmed-manually")
                handle = self.tasks.submit(
                    lambda control: worker.run(control, consent=True),
                    kind="bnf-pret-reader", label="Session BnF Prêt volontaire",
                )
            except Exception:
                status.set_label(_DEFAULT_ERROR)
                reset_buttons()
                return
            self.worker, self.handle = worker, handle
            status.set_label("Chromium s'ouvre. Connectez-vous manuellement sur le site BnF.")
            reset_buttons()

            def closed(done_handle):
                def show():
                    nonlocal busy
                    if not valid() or self._generation != generation or self.worker is not worker:
                        return False
                    try:
                        done_handle.result()
                        message = "Session BnF fermée."
                    except Exception as exc:
                        message = safe_error(exc)
                    self._stop()
                    busy = False
                    status.set_label(message)
                    reset_buttons()
                    return False
                GLib.idle_add(show)
            handle.add_done_callback(closed)

        def stop(_button=None):
            nonlocal busy
            self._stop()
            busy = False
            status.set_label("Session fermée et observations privées invalidées.")
            reset_buttons()

        def tick():
            nonlocal busy
            if not valid():
                return False
            identity = current()
            if identity is None or identity[0] != active_identity:
                sync_profile()
            # The completed TaskHandle owns final error reporting and teardown.
            # Do not mask its closed error by stopping on an intermediate state.
            reset_buttons()
            return True

        open_button.connect("clicked", start)
        confirm_button.connect("clicked", lambda _b: command("confirm_account"))
        loans_button.connect("clicked", lambda _b: command("loans"))
        search_button.connect("clicked", lambda _b: command("suggestions"))
        stop_button.connect("clicked", stop)
        root.connect("unmap", lambda *_args: stop() if self.worker is not None else None)

        subscribe = getattr(host, "subscribe_identity_overview", None)
        if callable(subscribe):
            try:
                self._unsubscribe = subscribe(lambda: GLib.idle_add(sync_profile))
            except Exception:
                self._unsubscribe = None
        GLib.timeout_add(300, tick)
        sync_profile()
        return root

    def deactivate(self):
        self._stop()
        self._ui = None
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            except Exception:
                pass
        self._unsubscribe = None
        self.tasks = None
        self.context = None


PLUGIN = BnfReaderPage()
