"""Explicit BnF-Prêt reader page, opt-in and read-only (#2055).

No provider.library registration, implicit SSO, rights ledger, ACSM or
Browser Recorder. Only validated on-screen metadata and closed states appear in UI.
"""
from __future__ import annotations

from cifereca.plugin_api import UiPageContribution
from cifereca_bnf_pret_worker import ReaderWorker, BrowserSessionError
from cifereca_bnf_pret_web import CatalogPage, CatalogRecord
from cifereca_bnf_pret_observations import ObservedLoans, ObservedLoanCounts
from cifereca_bnf_pret_selection import VisibleResourceSelection
from cifereca_bnf_pret_loan_selection import VisibleLoanBookSelection
from cifereca_bnf_pret_dom_probe import DomProbe
import re

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
    "invalid_search": "Recherche invalide (2 à 128 caractères, sans caractère de contrôle).",
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


def readable(value, limit=180):
    """Bound text for this local GTK display, never for logs or a broker result."""
    return " ".join(value.split())[:limit] if type(value) is str else ""


def suggestion_lines(result, *, limit=12):
    """Local, bounded GTK text; never transmitted to Diagnostics/TaskBroker."""
    if not isinstance(result, CatalogPage) or result.scope != "suggestions":
        return ()
    if type(result.records) not in (tuple, list) or len(result.records) > 100:
        return ()
    if type(limit) is not int or not 1 <= limit <= 12:
        return ()
    lines = []
    for row in result.records[:limit]:
        if not isinstance(row, CatalogRecord):
            return ()
        rid = row.resource_id
        if type(rid) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", rid) is None:
            return ()
        title = readable(row.title) or "Sans titre"
        authors = (readable(", ".join(a for a in row.authors if type(a) is str), 100)
                   if type(row.authors) in (tuple, list) else "")
        lines.append(f"{len(lines) + 1}. " + title + (" — " + authors if authors else "") + " [BnF " + rid + "]")
    return tuple(lines)


def loan_observation_status(counts):
    """UI-only counts; 'seen' is never a count of current loan rights."""
    if not isinstance(counts, ObservedLoanCounts):
        return _DEFAULT_ERROR
    return (f"{counts.on_last_page} prêt(s) visibles sur cette page ; "
            f"{counts.newly_seen} nouvelle(s) référence(s) ; "
            f"{counts.seen_in_session} référence(s) vues depuis l'ouverture de la session. "
            "Observations partielles, aucun droit actif attesté ou publié.")


def account_confirmation_ready(observed, identity_id, acknowledged):
    """Human declaration bound to the sole currently selected Core identity."""
    return (acknowledged is True
            and type(identity_id) is str and bool(identity_id)
            and type(observed) is tuple and len(observed) == 2
            and observed[0] == identity_id)


def valid_search_text(value):
    """A search is an explicit, bounded user input, never an account URL."""
    return (type(value) is str and 2 <= len(value) <= 128
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
            and 2 <= len(" ".join(value.split())) <= 128)


def dom_probe_message(probe):
    """Show only hard-coded labels and bounded counts, never account/page text."""
    if not isinstance(probe, DomProbe):
        return _DEFAULT_ERROR
    labels = {
        "activity": "activité", "results": "résultats",
        "detail": "fiche", "other": "autre page autorisée",
    }
    fields = (
        ("sections prêts", probe.loan_sections),
        ("blocs prêts", probe.loan_cards),
        ("sections résultats", probe.search_sections),
        ("notices résultats", probe.search_cards),
        ("fiches Book", probe.book_scopes),
        ("liens ressources", probe.resource_links),
        ("liens prêts", probe.loan_links),
    )
    if probe.kind not in labels or any(type(n) is not int or not 0 <= n <= 101
                                       for _, n in fields):
        return _DEFAULT_ERROR
    content = " ; ".join(f"{label} : {count}" for label, count in fields)
    return (f"Structure DOM visible — page {labels[probe.kind]} : " + content
            + ". Valeurs plafonnées à 101. Aucun compte, droit ou inventaire attesté.")


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
        self.observations = None
        self.selection = None
        self.loan_selection = None
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
        observations, selection, loans = self.observations, self.selection, self.loan_selection
        self.observations = self.selection = self.loan_selection = None
        if observations is not None:
            observations.close()
        if selection is not None:
            selection.close()
        if loans is not None:
            loans.close()
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
        account_ack = Gtk.CheckButton(
            label="Je confirme utiliser dans Chromium le compte BnF correspondant "
                  "au profil Cifereca affiché (déclaration personnelle, non vérifiée automatiquement)."
        )
        root.append(account_ack)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        root.append(row)
        open_button = Gtk.Button(label="Ouvrir le portail BnF")
        confirm_button = Gtk.Button(label="J'ai terminé la connexion BnF")
        loans_button = Gtk.Button(label="Relire mes prêts")
        search_button = Gtk.Button(label="Lire les résultats visibles")
        detail_button = Gtk.Button(label="Lire la fiche visible")
        probe_button = Gtk.Button(label="Diagnostiquer les sélecteurs visibles")
        stop_button = Gtk.Button(label="Fermer la session")
        for button in (open_button, confirm_button, loans_button, search_button,
                       detail_button, probe_button, stop_button):
            row.append(button)
        search_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        search_field = Gtk.Entry()
        search_field.set_max_length(128)
        search_field.set_placeholder_text("Titre ou auteur (recherche sur la page Web)")
        search_field.set_hexpand(True)
        search_bar.append(search_field)
        search_action = Gtk.Button(label="Rechercher sur BnF")
        search_bar.append(search_action)
        root.append(search_bar)
        status = Gtk.Label(label="Aucune connexion ni lecture lancée.", xalign=0, wrap=True)
        status.set_selectable(True)
        root.append(status)
        preview_frame = Gtk.ScrolledWindow()
        preview_frame.set_min_content_height(150)
        preview_frame.set_max_content_height(280)
        preview_frame.set_vexpand(True)
        preview = Gtk.Label(label="", xalign=0, wrap=True)
        preview.set_selectable(True)
        preview_frame.set_child(preview)
        root.append(preview_frame)
        chooser_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        chooser_bar.append(Gtk.Label(label="N° de la notice observée :"))
        notice_index = Gtk.SpinButton.new_with_range(1, 12, 1)
        notice_index.set_digits(0)
        chooser_bar.append(notice_index)
        choose_button = Gtk.Button(label="Ouvrir sa fiche dans Chromium")
        chooser_bar.append(choose_button)
        root.append(chooser_bar)
        loan_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        loan_bar.append(Gtk.Label(label="N° du prêt visible (sur la dernière page) :"))
        loan_index = Gtk.SpinButton.new_with_range(1, 12, 1)
        loan_index.set_digits(0)
        loan_bar.append(loan_index)
        loan_book_button = Gtk.Button(label="Ouvrir la fiche du livre emprunté")
        loan_bar.append(loan_book_button)
        root.append(loan_bar)

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
            account_ack.set_sensitive(same and mode == "login" and not busy)
            confirm_button.set_sensitive(
                same and mode == "login" and not busy
                and account_confirmation_ready(observed, active_identity,
                                               account_ack.get_active())
            )
            loans_button.set_sensitive(same and mode == "readonly" and not busy)
            search_button.set_sensitive(same and mode == "readonly" and not busy)
            search_field.set_sensitive(same and mode == "readonly" and not busy)
            search_action.set_sensitive(same and mode == "readonly" and not busy)
            detail_button.set_sensitive(same and mode == "readonly" and not busy)
            probe_button.set_sensitive(same and mode in {"login", "readonly"} and not busy)
            choose_button.set_sensitive(
                same and mode == "readonly" and not busy and self.selection is not None
            )
            notice_index.set_sensitive(
                same and mode == "readonly" and not busy and self.selection is not None
            )
            has_loan_choices = self.loan_selection is not None and self.loan_selection.count > 0
            loan_book_button.set_sensitive(same and mode == "readonly"
                                           and not busy and has_loan_choices)
            loan_index.set_sensitive(same and mode == "readonly"
                                     and not busy and has_loan_choices)
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
                account_ack.set_active(False)
                preview.set_label("")
                search_field.set_text("")
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
            if name == "confirm_account" and not account_confirmation_ready(
                    current(), scope.identity_id, account_ack.get_active()):
                status.set_label(
                    "Confirmez d'abord que le compte BnF utilisé correspond au profil actif."
                )
                return
            search_query = None
            if name == "search":
                search_query = search_field.get_text()
                if not valid_search_text(search_query):
                    status.set_label("Recherche invalide : 2–128 caractères, sans contrôle.")
                    return
            resource_id = None
            operation = name
            if name in ("detail", "loan_detail"):
                selection = self.selection if name == "detail" else self.loan_selection
                if selection is None:
                    status.set_label("Aucune fiche issue de la page visible à ouvrir.")
                    return
                try:
                    resource_id = selection.choose(
                        (notice_index.get_value_as_int() if name == "detail"
                         else loan_index.get_value_as_int()) - 1,
                        expected=scope, revision=selection.revision,
                    )
                except Exception:
                    status.set_label("Sélection invalide ou expirée : relire la page.")
                    return
                operation = "detail"  # Only public resource GET, never /loans/{id}.
            # Any browser command invalidates previous lists. No stale choice can
            # survive a navigation, refresh, identity change, or failed operation.
            selection, loans = self.selection, self.loan_selection
            self.selection = self.loan_selection = None
            if selection is not None:
                selection.close()
            if loans is not None:
                loans.close()
            busy = True
            reset_buttons()
            status.set_label("Lecture BnF en cours…")
            try:
                future = worker.submit(
                    operation, expected=scope, resource_id=resource_id,
                    account_confirmed=(name == "confirm_account"),
                    search_query=search_query,
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
                        preview.set_label("")
                        message = safe_error(exc)
                        if name in ("confirm_account", "loans") and self.observations is not None:
                            message += " Observations antérieures conservées sans attestation actuelle."
                        status.set_label(message)
                    else:
                        preview.set_label("")
                        if name == "confirm_account":
                            account_ack.set_active(False)
                        if name in ("confirm_account", "loans"):
                            chosen = None
                            try:
                                # Validate *all* entries even though only twelve
                                # may be chosen to open a public bibliographic page.
                                chosen = VisibleLoanBookSelection(scope)
                                visible_count = chosen.observe(result, expected=scope)
                                if self.observations is None:
                                    self.observations = ObservedLoans(scope)
                                counts = self.observations.observe(result)
                            except Exception:
                                if chosen is not None:
                                    chosen.close()
                                status.set_label(_DEFAULT_ERROR)
                            else:
                                self.loan_selection = chosen
                                # The user must see which public book corresponds
                                # to the number before choosing to navigate.
                                preview.set_label("\n".join(chosen.visible_book_lines()))
                                loan_index.set_range(1, max(1, visible_count))
                                loan_index.set_value(1)
                                message = loan_observation_status(counts)
                                if visible_count:
                                    message += (
                                        f" {visible_count} fiche(s) bibliographique(s) "
                                        "ouvrable(s) par numéro, sans téléchargement ni droit déduit."
                                    )
                                status.set_label(message)
                        elif name == "dom_probe":
                            status.set_label(dom_probe_message(result))
                        elif name in ("visible_detail", "detail", "loan_detail"):
                            if len(result.records) != 1:
                                status.set_label("Fiche non reconnue : aucune notice publiée.")
                            else:
                                record = result.records[0]
                                title = readable(record.title) or "Titre non renseigné"
                                authors = readable(", ".join(record.authors))
                                isbn = readable(record.isbn, 24)
                                availability = ("indisponible" if record.availability == "unavailable"
                                                else "non déterminée")
                                parts = ["Fiche visible : " + title]
                                if authors:
                                    parts.append("Auteur(s) : " + authors)
                                if isbn:
                                    parts.append("ISBN : " + isbn)
                                parts.append("Disponibilité : " + availability
                                             + " ; aucune capacité d'emprunt déduite.")
                                status.set_label("\n".join(parts))
                        else:
                            rows = suggestion_lines(result)
                            selection = None
                            try:
                                selection = VisibleResourceSelection(scope)
                                observed = selection.observe(result, expected=scope)
                                if observed != len(rows):
                                    raise ValueError("inconsistent_visible_projection")
                            except Exception:
                                if selection is not None:
                                    selection.close()
                                status.set_label(_DEFAULT_ERROR)
                            else:
                                self.selection = selection
                                preview.set_label("\n".join(rows))
                                notice_index.set_range(1, max(1, len(rows)))
                                notice_index.set_value(1)
                                status.set_label(
                                    f"{len(result.records)} suggestion(s) visibles ; "
                                    f"{len(rows)} affichée(s) ici, non exhaustives. "
                                    "Choisissez un numéro pour ouvrir une fiche."
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
            account_ack.set_active(False)
            preview.set_label("")
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
                    account_ack.set_active(False)
                    preview.set_label("")
                    search_field.set_text("")
                    status.set_label(message)
                    reset_buttons()
                    return False
                GLib.idle_add(show)
            handle.add_done_callback(closed)

        def stop(_button=None):
            nonlocal busy
            self._stop()
            busy = False
            account_ack.set_active(False)
            preview.set_label("")
            search_field.set_text("")
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
        account_ack.connect("toggled", lambda *_args: reset_buttons())
        loans_button.connect("clicked", lambda _b: command("loans"))
        search_button.connect("clicked", lambda _b: command("suggestions"))
        search_action.connect("clicked", lambda _b: command("search"))
        detail_button.connect("clicked", lambda _b: command("visible_detail"))
        probe_button.connect("clicked", lambda _b: command("dom_probe"))
        choose_button.connect("clicked", lambda _b: command("detail"))
        loan_book_button.connect("clicked", lambda _b: command("loan_detail"))
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
