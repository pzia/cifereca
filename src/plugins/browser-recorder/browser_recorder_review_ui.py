"""Private HTTP review and explicitly confirmed, bounded ZIP preview/export.

HAR v0.1 stays separate. Browser bytes live in RAM until the user reviews
all individual identifiers and separately approves each frozen ZIP archive.
No upload, hidden export, automatic GET replay or raw HAR persistence.
"""
from __future__ import annotations

import json
from pathlib import Path
from threading import Event

from browser_recorder import RecorderError, site_origin
from browser_recorder_http import CapturePause, record_http
from browser_recorder_review import IdentifierReview
from browser_recorder_zip import prepare_reviewed_zip
from browser_recorder_vault import PrivateSessionVault, VaultError
from browser_recorder_private_zip import save_private_zip


_REVIEW_PAGE_SIZE = 24  # Bound GTK widgets even for 256 distinct private IDs.

# User-requested capture failures are rendered with fixed, actionable text.
# RecorderError.args can contain arbitrary provider/URL text: never format it
# into GTK, Diagnostics, logs or ZIP. These are labels, not diagnostic data.
_CAPTURE_FAILURE_LABELS = {
    "playwright_unavailable": (
        "Playwright Python indisponible : ouvrez Plugins → Browser Recorder "
        "→ Installer les dépendances Python…, puis Installer Chromium…."
    ),
    "capture_interrupted": (
        "Session Chromium interrompue ou limite dépassée : mémoire temporaire "
        "effacée. Une projection partielle peut rester dans Sessions archivées "
        "si le coffre était activé. Ouvrez Plugins → Browser Recorder → "
        "Vérifier Chromium… avant de relancer."
    ),
    "capture_limit": (
        "Limite de capture atteinte : réduisez le parcours ou arrêtez la capture."
    ),
}
_CAPTURE_FAILURE_DEFAULT = (
    "Capture privée indisponible. Ouvrez Plugins → Browser Recorder → "
    "Vérifier Chromium… ; consultez ensuite Diagnostics pour les codes "
    "de prérequis et d'installation."
)


def _capture_failure_message(code: object) -> str:
    # type() rather than str() avoids side effects/repr leaks from exceptions.
    return (_CAPTURE_FAILURE_LABELS.get(code, _CAPTURE_FAILURE_DEFAULT)
            if type(code) is str else _CAPTURE_FAILURE_DEFAULT)


def _capture_diagnostic_code(code: object) -> str:
    """Map RecorderError arguments to a literal code, never calling hooks."""
    if type(code) is not str:
        return "capture_failed"
    if code == "playwright_unavailable":
        return "playwright_missing"
    return code if code in {"capture_interrupted", "capture_limit"} else "capture_failed"


def _metadata_only_origins(raw: str, primary: str) -> tuple[str, ...]:
    """Validate opt-in third-party origins; never echo private input in errors."""
    if type(raw) is not str or len(raw) > 512:
        raise RecorderError("invalid_origin")
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if len(parts) > 4:
        raise RecorderError("invalid_origin")
    selected = []
    for candidate in parts:
        origin = site_origin(candidate)
        # Accept only absolute HTTPS origins, never arbitrary URL paths,
        # queries, fragments, usernames or silent same-host capture.
        if candidate not in {origin, origin + "/"} or origin == primary:
            raise RecorderError("invalid_origin")
        if origin in selected:
            raise RecorderError("invalid_origin")
        selected.append(origin)
    return tuple(selected)


class PrivateReviewPanel:
    def __init__(self, Gtk, GLib, tasks, site, consent, legacy_running,
                 export_directory=None, diagnostic=None, vault_directory=None):
        self.Gtk, self.GLib, self.tasks = Gtk, GLib, tasks
        self._diagnostic = diagnostic
        self.site, self.consent = site, consent
        self.legacy_running = legacy_running
        self.export_directory = (Path(export_directory)
                                 if export_directory is not None else None)
        self.vault_directory = Path(vault_directory) if vault_directory else None
        self.vault_consent = None
        self.vault_passphrase = None
        self.session_list = None
        self.delete_confirm = None
        self.private_export_confirm = None
        self.private_export_button = None
        self.private_export_directory = None
        self._private_destination_dialog = None
        self.private_export_passphrase = None
        self._bundle_plan = None
        self._bundle_preview = None
        self._export_dialog = None
        self._handle = None
        self._stop = None
        self._pause_control = None
        self._trace = None
        self._review = None
        self._candidate_page = 0
        self._closed = False
        self._generation = 0
        self._index_handle = None
        self._retention_plan = None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.widget = box
        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        box.append(Gtk.Label(
            label="Parcours HTTP privé (P0) — revue et ZIP volontaire",
            xalign=0, wrap=True,
        ))
        box.append(Gtk.Label(
            label="Capture facultative des échanges textuels sur le site HTTPS choisi. "
                  "Les valeurs peuvent contenir des données privées et restent en mémoire. "
                  "Choisissez pour chaque identifiant avant de préparer le ZIP. "
                  "L'export n'a lieu qu'après prévisualisation et confirmation.",
            xalign=0, wrap=True,
        ))
        box.append(Gtk.Label(
            label="Hôtes tiers facultatifs (HTTPS, séparés par des virgules) : "
                  "métadonnées de requête seulement, jamais URL ni corps. "
                  "Laissez vide pour exclure tout trafic tiers.",
            xalign=0, wrap=True,
        ))
        self.third_party_origins = Gtk.Entry()
        self.third_party_origins.set_placeholder_text(
            "https://identite.exemple.org, https://auth.exemple.org"
        )
        box.append(self.third_party_origins)
        if self.vault_directory is not None:
            box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            box.append(Gtk.Label(
                label="Coffre chiffré : chaque capture consentie est archivée "
                      "pour être revue plus tard, sans export ZIP. Entrez une "
                      "phrase secrète d'au moins 12 caractères et conservez-la. "
                      "Une panne peut ne laisser qu'une capture partielle.",
                xalign=0, wrap=True,
            ))
            self.vault_consent = Gtk.CheckButton(
                label="J'autorise le stockage local chiffré de cette capture."
            )
            box.append(self.vault_consent)
            self.vault_passphrase = Gtk.Entry()
            self.vault_passphrase.set_visibility(False)
            self.vault_passphrase.set_placeholder_text(
                "Phrase secrète du coffre (capture, ouverture ou suppression)"
            )
            box.append(self.vault_passphrase)
            self.sessions_refresh = Gtk.Button(label="Lister les sessions archivées")
            self.sessions_unlock = Gtk.Button(
                label="Déverrouiller les métadonnées des sessions…"
            )
            self.session_list = Gtk.ComboBoxText()
            self.session_name = Gtk.Entry()
            self.session_name.set_max_length(80)
            self.session_name.set_placeholder_text(
                "Nom privé de la session sélectionnée (1–80 caractères)"
            )
            self.sessions_rename = Gtk.Button(label="Renommer cette session chiffrée")
            self.sessions_open = Gtk.Button(label="Ouvrir une session — nouvelle revue")
            self.delete_confirm = Gtk.CheckButton(
                label="Je confirme la suppression locale de la session choisie."
            )
            self.sessions_delete = Gtk.Button(label="Supprimer cette session")
            for widget in (self.sessions_refresh, self.sessions_unlock,
                           self.session_list, self.session_name,
                           self.sessions_rename, self.sessions_open, self.delete_confirm,
                           self.sessions_delete):
                box.append(widget)
            self.sessions_refresh.connect(
                "clicked", lambda *_: self.refresh_sessions(),
            )
            self.sessions_unlock.connect(
                "clicked", lambda *_: self.unlock_sessions(),
            )
            self.sessions_rename.connect(
                "clicked", lambda *_: self.rename_session(),
            )
            self.sessions_open.connect("clicked", lambda *_: self.open_session())
            self.sessions_delete.connect(
                "clicked", lambda *_: self.delete_session(),
            )
            box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            box.append(Gtk.Label(
                label="Rétention privée : uniquement sur demande. Seules les "
                      "sessions terminées et plus anciennes que le délai "
                      "sont candidates ; partielles et interrompues protégées.",
                xalign=0, wrap=True,
            ))
            self.retention_age = Gtk.ComboBoxText()
            for days in (30, 90, 180, 365):
                self.retention_age.append(str(days), f"Plus de {days} jours")
            self.retention_age.set_active_id("90")
            self.retention_preview_button = Gtk.Button(
                label="Prévisualiser les anciennes sessions, sans supprimer"
            )
            self.retention_confirm = Gtk.CheckButton(
                label="Je confirme la suppression IRRÉVERSIBLE de ces sessions "
                      "terminées après avoir vérifié le nombre affiché."
            )
            self.retention_apply_button = Gtk.Button(
                label="Supprimer uniquement les sessions prévisualisées"
            )
            for widget in (self.retention_age, self.retention_preview_button,
                           self.retention_confirm, self.retention_apply_button):
                box.append(widget)
            self.retention_age.connect(
                "changed", lambda *_: self._invalidate_retention(),
            )
            self.retention_confirm.connect(
                "toggled", lambda *_: self._refresh(),
            )
            self.retention_preview_button.connect(
                "clicked", lambda *_: self.preview_retention(),
            )
            self.retention_apply_button.connect(
                "clicked", lambda *_: self.apply_retention(),
            )
            box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            box.append(Gtk.Label(
                label="Récupération d'espace : après une coupure brutale, des "
                      "résidus temporaires CHIFFRÉS peuvent rester sur disque. "
                      "Seuls ceux de plus de 24 h sont nettoyables avec "
                      "confirmation ; aucune capture ni nom n'est supprimé.",
                xalign=0, wrap=True,
            ))
            self.staging_inspect = Gtk.Button(
                label="Examiner les résidus chiffrés sans suppression"
            )
            self.staging_confirm = Gtk.CheckButton(
                label="Je confirme le nettoyage des seuls résidus chiffrés "
                      "temporaires de plus de 24 h."
            )
            self.staging_prune = Gtk.Button(
                label="Nettoyer les résidus chiffrés anciens"
            )
            for widget in (self.staging_inspect, self.staging_confirm,
                           self.staging_prune):
                box.append(widget)
            self.staging_inspect.connect(
                "clicked", lambda *_: self.inspect_staging(),
            )
            self.staging_prune.connect(
                "clicked", lambda *_: self.prune_staging(),
            )
            self.staging_confirm.connect(
                "toggled", lambda *_: self._refresh(),
            )
            box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            box.append(Gtk.Label(
                label="Export DE SECOURS non anonymisé : contient URL, "
                      "identifiants, titres et corps privés observés. "
                      "Ne jamais partager sans une revue spécifique. "
                      "Le résultat est un .brzip CHIFFRÉ, pas le ZIP LLM.",
                xalign=0, wrap=True,
            ))
            self.private_export_directory = Gtk.Entry()
            self.private_export_directory.set_placeholder_text(
                "Dossier privé préexistant (absolu, permissions 0700)"
            )
            box.append(self.private_export_directory)
            self.private_export_choose = Gtk.Button(
                label="Choisir un dossier privé…"
            )
            box.append(self.private_export_choose)
            self.private_export_choose.connect(
                "clicked", lambda *_: self._prompt_private_folder(),
            )
            self.private_export_passphrase = Gtk.Entry()
            self.private_export_passphrase.set_visibility(False)
            self.private_export_passphrase.set_placeholder_text(
                "NOUVELLE phrase secrète réservée à ce ZIP privé chiffré"
            )
            box.append(self.private_export_passphrase)
            self.private_export_confirm = Gtk.CheckButton(
                label="Je confirme cet export privé distinct du ZIP anonymisé."
            )
            box.append(self.private_export_confirm)
            self.private_export_button = Gtk.Button(
                label="Exporter le ZIP privé chiffré de cette session"
            )
            box.append(self.private_export_button)
            self.private_export_confirm.connect(
                "toggled", lambda *_: self._refresh(),
            )
            self.private_export_button.connect(
                "clicked", lambda *_: self.export_private_zip(),
            )
        # FlowBox wraps the five capture/review buttons at sidebar/mobile
        # widths; a single horizontal Box could push Stop/Clear off-screen.
        controls = Gtk.FlowBox()
        controls.set_selection_mode(Gtk.SelectionMode.NONE)
        controls.set_min_children_per_line(1)
        controls.set_max_children_per_line(3)
        controls.set_row_spacing(6)
        controls.set_column_spacing(8)
        self.start = Gtk.Button(label="Démarrer HTTP privé")
        self.stop = Gtk.Button(label="Arrêter / revoir")
        self.pause_button = Gtk.Button(label="Mettre en pause")
        self.preview_button = Gtk.Button(label="Prévisualiser les choix")
        self.clear = Gtk.Button(label="Effacer les données privées")
        for button in (self.start, self.stop, self.pause_button,
                       self.preview_button, self.clear):
            controls.append(button)
        box.append(controls)
        self.status = Gtk.Label(label="Aucune observation HTTP privée en mémoire.",
                                xalign=0, wrap=True)
        self.status.set_selectable(True)
        box.append(self.status)
        box.append(Gtk.Label(
            label="Revue clavier : Tab pour cibler un identifiant ; "
                  "1 = anonymiser, 2 = conserver, 3 = exclure. "
                  "Chaque choix reste individuel, aucun choix par défaut.",
            xalign=0, wrap=True,
        ))

        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(180)
        scroll.set_max_content_height(280)
        scroll.set_vexpand(False)
        self.candidates_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6,
        )
        scroll.set_child(self.candidates_box)
        box.append(scroll)
        pagination = Gtk.FlowBox()
        pagination.set_selection_mode(Gtk.SelectionMode.NONE)
        pagination.set_min_children_per_line(1)
        pagination.set_max_children_per_line(3)
        pagination.set_row_spacing(6)
        pagination.set_column_spacing(8)
        self.candidate_previous = Gtk.Button(label="Identifiants précédents")
        self.candidate_next = Gtk.Button(label="Identifiants suivants")
        self.candidate_page_status = Gtk.Label(
            label="Aucun identifiant à examiner.", xalign=0, wrap=True,
        )
        pagination.append(self.candidate_previous)
        pagination.append(self.candidate_page_status)
        pagination.append(self.candidate_next)
        box.append(pagination)
        self.candidate_previous.connect("clicked", lambda *_: self.change_candidate_page(-1))
        self.candidate_next.connect("clicked", lambda *_: self.change_candidate_page(1))

        box.append(Gtk.Label(
            label="L'aperçu est local et peut contenir des identifiants "
                  "expressément conservés. Il ne constitue PAS une archive approuvée.",
            xalign=0, wrap=True,
        ))
        preview_scroll = Gtk.ScrolledWindow()
        preview_scroll.set_min_content_height(120)
        preview_scroll.set_max_content_height(240)
        self.preview_text = Gtk.TextView()
        self.preview_text.set_editable(False)
        self.preview_text.set_cursor_visible(False)
        self.preview_text.set_monospace(True)
        self.preview_text.set_wrap_mode(Gtk.WrapMode.CHAR)
        preview_scroll.set_child(self.preview_text)
        box.append(preview_scroll)

        # Two distinct steps: prepare an immutable reviewed ZIP in memory,
        # then examine EACH exact member through this read-only selector and
        # explicitly confirm writing a local archive. Nothing is uploaded.
        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        self.plan_button = Gtk.Button(label="Préparer l'aperçu exact du ZIP")
        box.append(self.plan_button)
        self.bundle_summary = Gtk.Label(
            label="Aucune archive préparée.", xalign=0, wrap=True,
        )
        self.bundle_summary.set_selectable(True)
        box.append(self.bundle_summary)
        self.bundle_member = Gtk.ComboBoxText()
        box.append(self.bundle_member)
        member_scroll = Gtk.ScrolledWindow()
        member_scroll.set_min_content_height(140)
        member_scroll.set_max_content_height(300)
        self.bundle_text = Gtk.TextView()
        self.bundle_text.set_editable(False)
        self.bundle_text.set_cursor_visible(False)
        self.bundle_text.set_monospace(True)
        self.bundle_text.set_wrap_mode(Gtk.WrapMode.CHAR)
        member_scroll.set_child(self.bundle_text)
        box.append(member_scroll)
        self.export_confirm = Gtk.CheckButton()
        self.export_confirm.set_child(Gtk.Label(
            label="J'ai revu les membres accessibles ci-dessus et j'accepte "
                  "de créer localement cette archive pour un partage volontaire.",
            xalign=0, wrap=True,
        ))
        box.append(self.export_confirm)
        self.export_button = Gtk.Button(label="Enregistrer sous… le ZIP anonymisé")
        box.append(self.export_button)
        self.plan_button.connect("clicked", lambda *_: self.prepare_zip())
        self.bundle_member.connect("changed", lambda *_: self._show_zip_member())
        self.export_confirm.connect("toggled", lambda *_: self._refresh())
        self.export_button.connect("clicked", lambda *_: self.export_zip())

        self.start.connect("clicked", lambda *_: self.begin())
        self.stop.connect("clicked", lambda *_: self.stop_capture())
        self.pause_button.connect("clicked", lambda *_: self.toggle_pause())
        self.preview_button.connect("clicked", lambda *_: self.preview())
        self.clear.connect("clicked", lambda *_: self.discard())
        self._refresh()

    def is_recording(self):
        return self._handle is not None and not self._handle.done()

    def has_private_data(self):
        return (self.is_recording()
                or self._trace is not None or self._review is not None)

    def _refresh(self):
        busy = self.is_recording()
        scanning = self.is_scanning()
        replaceable = self._trace is None or (
            self.vault_directory is not None
            and bool(getattr(self._trace, "vault_session_id", None))
        )
        self.start.set_sensitive(
            not self._closed and not busy and not scanning and replaceable
            and not self.legacy_running()
        )
        if self.vault_directory is not None:
            self.sessions_refresh.set_sensitive(
                not self._closed and not busy and not scanning
            )
            self.sessions_unlock.set_sensitive(
                not self._closed and not busy and not scanning
            )
            self.sessions_open.set_sensitive(
                not self._closed and not busy and not scanning
            )
            self.sessions_delete.set_sensitive(
                not self._closed and not busy and not scanning
            )
            self.sessions_rename.set_sensitive(
                not self._closed and not busy and not scanning
                and bool(self.session_list.get_active_id())
            )
            self.retention_age.set_sensitive(
                not self._closed and not busy and not scanning
            )
            self.retention_preview_button.set_sensitive(
                not self._closed and not busy and not scanning
            )
            self.retention_apply_button.set_sensitive(
                not self._closed and not busy and not scanning
                and self._retention_plan is not None
                and self._retention_plan.count > 0
                and self.retention_confirm.get_active()
            )
            self.staging_inspect.set_sensitive(
                not self._closed and not busy and not scanning
            )
            self.staging_prune.set_sensitive(
                not self._closed and not busy and not scanning
                and self.staging_confirm.get_active()
            )
            self.private_export_button.set_sensitive(
                not self._closed and not busy and not scanning
                and self.private_export_confirm.get_active()
                and bool(self.session_list.get_active_id())
            )
        self.stop.set_sensitive(not self._closed and busy)
        self.third_party_origins.set_sensitive(
            not self._closed and not busy and self._trace is None
        )
        can_pause = (not self._closed and busy and self._pause_control is not None
                     and self._stop is not None and not self._stop.is_set())
        self.pause_button.set_sensitive(can_pause)
        paused = self._pause_control.state()[0] if self._pause_control else False
        self.pause_button.set_label("Reprendre HTTP privé" if paused else "Mettre en pause")
        self.preview_button.set_sensitive(
            not self._closed and not busy and self._review is not None
        )
        self.clear.set_sensitive(
            not self._closed and not busy and self._trace is not None
        )
        candidate_count = len(self._review.candidates()) if self._review is not None else 0
        self.candidate_previous.set_sensitive(
            not self._closed and not busy and self._candidate_page > 0
        )
        self.candidate_next.set_sensitive(
            not self._closed and not busy
            and (self._candidate_page + 1) * _REVIEW_PAGE_SIZE < candidate_count
        )
        ready = not self._closed and not busy and self._review is not None
        self.plan_button.set_sensitive(ready and not self._review.pending()
                                       if ready else False)
        self.export_confirm.set_sensitive(
            not self._closed and self._bundle_plan is not None
        )
        self.export_button.set_sensitive(
            not self._closed and self._bundle_plan is not None
            and self.export_confirm.get_active()
        )

    def _text(self, value):
        # Gtk.TextBuffer is local process memory. Never persist to GSettings,
        # diagnostics, logging or the metadata-only HAR.
        self.preview_text.get_buffer().set_text(value)

    def _clear_candidates(self):
        child = self.candidates_box.get_first_child()
        while child is not None:
            self.candidates_box.remove(child)
            child = self.candidates_box.get_first_child()

    def _invalidate_bundle(self):
        self._bundle_plan = None
        self._bundle_preview = None
        self.bundle_member.remove_all()
        self.bundle_text.get_buffer().set_text("")
        self.bundle_summary.set_label("Aucune archive préparée.")
        self.export_confirm.set_active(False)
        self._refresh()

    def _discard_private(self):
        self._candidate_page = 0
        self._invalidate_bundle()
        self._text("")
        self._clear_candidates()
        if self._review is not None:
            self._review.destroy()
            self._review = None
        if self._trace is not None:
            self._trace.destroy()
            self._trace = None

    def _emit_failure(self, operation: str, code: str) -> None:
        """Only fixed, Core-validated action outcomes; no private payload."""
        try:
            if callable(self._diagnostic):
                self._diagnostic(operation, code)
        except Exception:
            # Optional Diagnostics must not interrupt private review or reveal
            # exceptions from a provider, exporter, or installed plugin.
            pass

    def is_scanning(self):
        return self._index_handle is not None and not self._index_handle.done()

    def begin(self):
        if (self._closed or self.is_recording() or self.is_scanning()
                or self.legacy_running()):
            return
        if (self.vault_directory is not None and self._trace is not None
                and not getattr(self._trace, "vault_session_id", None)):
            self.status.set_label(
                "Capture non archivée en mémoire : ne pas la remplacer."
            )
            return
        if not self.consent.get_active():
            self.status.set_label("Consentement explicite requis.")
            return
        try:
            start_url = self.site.get_text().strip()
            primary = site_origin(start_url)
            metadata_only_origins = _metadata_only_origins(
                self.third_party_origins.get_text(), primary,
            )
        except RecorderError:
            self.status.set_label(
                "Origines HTTPS invalides : saisir au plus 4 hôtes tiers, "
                "sans chemin ni identifiant. Aucun navigateur lancé."
            )
            return
        passphrase = None
        if self.vault_directory is not None:
            if not self.vault_consent.get_active():
                self.status.set_label(
                    "Consentement au stockage local chiffré requis avant capture."
                )
                return
            passphrase = self.vault_passphrase.get_text()
            if len(passphrase) < 12:
                self.status.set_label(
                    "Phrase secrète d'au moins 12 caractères requise."
                )
                return
            self.vault_passphrase.set_text("")
            self.vault_consent.set_active(False)
        self._discard_private()
        self._generation += 1
        generation = self._generation
        self._stop = Event()
        stop = self._stop
        self._pause_control = CapturePause()
        pause_control = self._pause_control
        self.status.set_label("Navigateur HTTP privé en cours, sans export…")

        def work(_control):
            kwargs = {
                "pause_control": pause_control,
                "metadata_only_origins": metadata_only_origins,
            }
            if self.vault_directory is not None:
                writer = PrivateSessionVault(self.vault_directory).begin(passphrase)
                kwargs["vault_writer"] = writer
            return record_http(start_url, stop, **kwargs)

        try:
            handle = self.tasks.submit(
                work, kind="browser-recorder", label="Parcours HTTP privé",
            )
        except Exception:
            self._stop = None
            self._pause_control = None
            self.status.set_label(_capture_failure_message(None))
            self._emit_failure("http_capture", "capture_failed")
            self._refresh()
            return
        self._handle = handle
        self._refresh()

        def finished(done):
            try:
                result, message, failure_code = done.result(), None, None
            except VaultError:
                result = None
                message = (
                    "Coffre chiffré indisponible : vérifiez les dépendances, "
                    "permissions et phrase secrète. Aucun stockage en clair."
                )
                failure_code = "capture_failed"
            except RecorderError as exc:
                # Only a literal known RecorderError code may affect UI text;
                # never call str(exc) or inspect Playwright's raw exception.
                code = exc.args[0] if len(exc.args) == 1 else None
                result, message = None, _capture_failure_message(code)
                failure_code = _capture_diagnostic_code(code)
            except Exception:
                result, message, failure_code = None, _capture_failure_message(None), "capture_failed"

            def deliver():
                if self._closed or self._generation != generation:
                    if result is not None:
                        result.destroy()
                    return False
                if self._handle is done:
                    self._handle = None
                self._stop = None
                self._pause_control = None
                if message:
                    self.status.set_label(message)
                    self._emit_failure("http_capture", failure_code)
                else:
                    # A window closed before any HTTP observation is not a
                    # usable archive and must not create review/export state.
                    if not result.exchanges:
                        reason = (" (navigateur fermé)"
                                  if result.end_reason == "browser_closed" else "")
                        result.destroy()
                        self.status.set_label(
                            "Capture terminée" + reason
                            + " : aucune donnée recueillie ; aucun ZIP possible."
                        )
                        self._refresh()
                        return False
                    self._trace = result
                    try:
                        self._review = IdentifierReview(result)
                    except Exception:
                        # Never show exception text from a provider response
                        # or URL that might contain a token.
                        result.destroy()
                        self._trace = None
                        self.status.set_label(
                            "Limite de revue atteinte : capture effacée, aucun export.",
                        )
                        self._emit_failure("http_review", "review_failed")
                    else:
                        self._show_candidates()
                        if getattr(result, "vault_session_id", None):
                            self.status.set_label(
                                f"Session archivée en coffre chiffré : "
                                f"{len(result.exchanges)} échanges. "
                                "Nouvelle capture possible sans effacer l'archive."
                            )
                        if result.end_reason == "browser_closed":
                            self.status.set_label(
                                "Capture terminée (navigateur fermé) : "
                                f"{len(result.exchanges)} échanges, "
                                f"{sum(result.omitted.values())} omissions. "
                                f"{self._review.pending()} décisions individuelles "
                                "restantes ; aucun ZIP automatique."
                            )
                self._refresh()
                return False

            self.GLib.idle_add(deliver)

        handle.add_done_callback(finished)

    def _invalidate_retention(self):
        """UI consent is bound to one preview and one selected duration."""
        self._retention_plan = None
        if getattr(self, "retention_confirm", None) is not None:
            self.retention_confirm.set_active(False)
        if hasattr(self, "start"):
            self._refresh()

    def preview_retention(self):
        """Check authenticated, completed-only sessions off the GTK thread."""
        if (self.vault_directory is None or self._closed
                or self.is_recording() or self.is_scanning()):
            return
        self._invalidate_retention()
        try:
            days = int(self.retention_age.get_active_id())
        except (TypeError, ValueError):
            self.status.set_label("Délai de rétention invalide.")
            return
        secret = self.vault_passphrase.get_text()
        self.vault_passphrase.set_text("")
        if len(secret) < 12:
            self.status.set_label("Phrase secrète requise pour prévisualiser.")
            return
        generation = self._generation
        directory = self.vault_directory

        def work(_control):
            return PrivateSessionVault(directory).retention_preview(
                secret, days,
            )

        try:
            handle = self.tasks.submit(
                work, kind="browser-recorder",
                label="Prévisualiser la rétention du coffre",
            )
        except Exception:
            self.status.set_label("Prévisualisation indisponible.")
            return
        self._index_handle = handle
        self.status.set_label("Vérification des sessions chiffrées…")
        self._refresh()

        def finished(done):
            try:
                plan = done.result()
            except Exception:
                plan = None

            def deliver():
                if self._closed or generation != self._generation:
                    return False
                if self._index_handle is done:
                    self._index_handle = None
                if plan is None or self.retention_age.get_active_id() != str(days):
                    self.status.set_label(
                        "Prévisualisation refusée : clé ou coffre non sûrs."
                    )
                else:
                    self._retention_plan = plan if plan.count else None
                    self.status.set_label(
                        f"{plan.count} sessions TERMINÉES de plus de {days} "
                        f"jours ({plan.total_bytes} octets chiffrés). "
                        "Les partielles, interrompues et autres clés sont "
                        "préservées. Confirmation distincte ; aperçu 10 min."
                    )
                self._refresh()
                return False

            self.GLib.idle_add(deliver)

        handle.add_done_callback(finished)

    def apply_retention(self):
        """Delete only the checked fingerprint set with a fresh unlock."""
        if (self.vault_directory is None or self._closed
                or self.is_recording() or self.is_scanning()
                or self._retention_plan is None
                or not self.retention_confirm.get_active()):
            self.status.set_label(
                "Suppression refusée : prévisualisation et confirmation requises."
            )
            return
        plan, self._retention_plan = self._retention_plan, None
        self.retention_confirm.set_active(False)
        passphrase = self.vault_passphrase.get_text()
        self.vault_passphrase.set_text("")
        if len(passphrase) < 12:
            self.status.set_label(
                "Saisir de nouveau la clé ; refaire l'aperçu ensuite."
            )
            self._refresh()
            return
        directory = self.vault_directory
        generation = self._generation

        def work(_control):
            return PrivateSessionVault(directory).retention_apply(
                plan, passphrase, confirmed=True,
            )

        try:
            handle = self.tasks.submit(
                work, kind="browser-recorder",
                label="Appliquer la rétention consentie",
            )
        except Exception:
            self.status.set_label("Rétention indisponible ; refaire l'aperçu.")
            return
        self._index_handle = handle
        self._refresh()

        def finished(done):
            try:
                result = done.result()
            except Exception:
                result = None

            def deliver():
                if self._closed or generation != self._generation:
                    return False
                if self._index_handle is done:
                    self._index_handle = None
                if result is None:
                    self.status.set_label(
                        "Suppression refusée : clé, session modifiée ou aperçu "
                        "expiré. Refaire l'aperçu ; aucune purge automatique."
                    )
                else:
                    count, size = result
                    if (self._trace is not None
                            and getattr(self._trace, "vault_session_id", None)
                            in {row[0] for row in plan.fingerprints}):
                        self._discard_private()
                    self.refresh_sessions()
                    self.status.set_label(
                        f"{count} sessions terminées prévisualisées supprimées "
                        f"({size} octets). Autres sessions intactes."
                    )
                self._refresh()
                return False

            self.GLib.idle_add(deliver)

        handle.add_done_callback(finished)

    def inspect_staging(self):
        """Show non-sensitive counts; never touch bytes or expose filenames."""
        if (self.vault_directory is None or self._closed
                or self.is_recording() or self.is_scanning()):
            return
        try:
            count, size, eligible = PrivateSessionVault(
                self.vault_directory
            ).staging_summary()
        except VaultError:
            self.status.set_label(
                "Résidus inaccessibles : permissions ou liens non sûrs."
            )
            return
        self.status.set_label(
            f"{count} résidus chiffrés ({size} octets), "
            f"dont {eligible} nettoyables après confirmation. "
            "Aucune session supprimée."
        )

    def prune_staging(self):
        """Explicitly and asynchronously remove only aged ciphertext staging."""
        if (self.vault_directory is None or self._closed
                or self.is_recording() or self.is_scanning()):
            return
        if not self.staging_confirm.get_active():
            self.status.set_label(
                "Nettoyage refusé : confirmation distincte requise."
            )
            return
        self.staging_confirm.set_active(False)
        generation = self._generation
        directory = self.vault_directory

        def work(_control):
            return PrivateSessionVault(directory).prune_staging(confirmed=True)

        try:
            handle = self.tasks.submit(
                work, kind="browser-recorder",
                label="Nettoyer les résidus chiffrés",
            )
        except Exception:
            self.status.set_label("Nettoyage indisponible.")
            return
        self._index_handle = handle
        self._refresh()

        def finished(done):
            try:
                result = done.result()
            except Exception:
                result = None

            def deliver():
                if self._closed or generation != self._generation:
                    return False
                if self._index_handle is done:
                    self._index_handle = None
                if result is None:
                    self.status.set_label(
                        "Nettoyage refusé : résidu non sûr ou coffre inaccessible."
                    )
                else:
                    count, size = result
                    self.status.set_label(
                        f"{count} résidus chiffrés anciens nettoyés "
                        f"({size} octets). Aucune session supprimée."
                    )
                self._refresh()
                return False

            self.GLib.idle_add(deliver)

        handle.add_done_callback(finished)

    def refresh_sessions(self):
        if (self.vault_directory is None or self._closed
                or self.is_recording() or self.is_scanning()):
            return
        try:
            ids = PrivateSessionVault(self.vault_directory).list_ids()
        except VaultError:
            self.status.set_label("Bibliothèque chiffrée inaccessible.")
            return
        self.session_list.remove_all()
        for index, session_id in enumerate(ids, 1):
            self.session_list.append(session_id, f"Session {index} · {session_id[:8]}")
        if ids:
            self.session_list.set_active_id(ids[-1])
        self.status.set_label(
            f"{len(ids)} sessions chiffrées trouvées ; aucun site contacté."
        )

    def unlock_sessions(self):
        """Build the sensitive catalogue only on explicit local unlock.

        Scrypt scans happen on a Core task worker, never on the GTK thread.
        Session summaries are neither persisted nor sent to diagnostics.
        """
        if (self.vault_directory is None or self._closed
                or self.is_recording() or self.is_scanning()):
            return
        passphrase = self.vault_passphrase.get_text()
        self.vault_passphrase.set_text("")
        if len(passphrase) < 12:
            self.status.set_label(
                "Phrase secrète de 12 caractères minimum requise."
            )
            return
        generation = self._generation
        vault_path = self.vault_directory
        self.status.set_label(
            "Déchiffrement local des métadonnées en cours, sans accès réseau…"
        )

        def work(_control):
            return PrivateSessionVault(vault_path).unlocked_index(passphrase)

        try:
            handle = self.tasks.submit(
                work, kind="browser-recorder", label="Indexer le coffre privé",
            )
        except Exception:
            self.status.set_label("Index privé indisponible.")
            return
        self._index_handle = handle
        self._refresh()

        def finished(done):
            try:
                result = done.result()
            except Exception:
                result = None

            def deliver():
                if self._closed or generation != self._generation:
                    return False
                if self._index_handle is done:
                    self._index_handle = None
                if result is None:
                    self.status.set_label(
                        "Déverrouillage impossible : clé, quota ou coffre invalide."
                    )
                else:
                    entries, inaccessible = result
                    try:
                        ids = PrivateSessionVault(vault_path).list_ids()
                    except VaultError:
                        self.status.set_label("Index local inaccessible.")
                        self._refresh()
                        return False
                    known = {entry["id"]: entry for entry in entries}
                    old_selected = self.session_list.get_active_id()
                    self.session_list.remove_all()
                    ordered = [item["id"] for item in entries]
                    ordered.extend(sid for sid in ids if sid not in known)
                    for number, sid in enumerate(ordered, 1):
                        info = known.get(sid)
                        if info is None:
                            label = f"Session {number} · {sid[:8]} — verrouillée/illisible"
                        else:
                            private_name = info.get("label", "")
                            label = (
                                (f"{private_name[:80]} · " if private_name else "")
                                + f"{info['saved_at'][:16]} · {info['status']} · "
                                f"{info['origin'][:90]} · {info['exchanges']} échanges "
                                f"· {sum(info['omissions'].values())} omissions"
                            )
                        self.session_list.append(sid, label)
                    if ordered:
                        self.session_list.set_active_id(
                            old_selected if old_selected in ordered else ordered[0]
                        )
                    self.status.set_label(
                        f"{len(entries)} sessions déverrouillées et "
                        f"{inaccessible} indisponibles ; aucun contact fournisseur."
                    )
                self._refresh()
                return False

            self.GLib.idle_add(deliver)

        handle.add_done_callback(finished)

    def rename_session(self):
        """Explicit rename: never persist readable titles or mutate .cbrv."""
        if (self.vault_directory is None or self._closed
                or self.is_recording() or self.is_scanning()):
            return
        sid = self.session_list.get_active_id()
        label = self.session_name.get_text().strip()
        secret = self.vault_passphrase.get_text()
        self.vault_passphrase.set_text("")
        self.session_name.set_text("")
        if not sid or not label or len(secret) < 12:
            self.status.set_label(
                "Sélectionnez une session et saisissez nom + phrase secrète."
            )
            return
        generation = self._generation
        vault_path = self.vault_directory

        def work(_control):
            PrivateSessionVault(vault_path).set_label(sid, secret, label)
            return True

        try:
            handle = self.tasks.submit(
                work, kind="browser-recorder", label="Nommer la session chiffrée",
            )
        except Exception:
            self.status.set_label("Renommage indisponible.")
            return
        self._index_handle = handle
        self.status.set_label("Modification chiffrée du nom en cours…")
        self._refresh()

        def finished(done):
            try:
                ok = bool(done.result())
            except Exception:
                ok = False

            def deliver():
                if self._closed or self._generation != generation:
                    return False
                if self._index_handle is done:
                    self._index_handle = None
                self.status.set_label(
                    "Nom privé chiffré enregistré ; déverrouillez la liste "
                    "pour afficher le nouveau nom."
                    if ok else
                    "Renommage refusé : clé, fichier ou nom invalide."
                )
                # Avoid presenting stale private labels after rename.
                self.refresh_sessions()
                # refresh_sessions clears the private labels intentionally.
                self.status.set_label(
                    "Nom privé chiffré enregistré ; déverrouillez la liste."
                    if ok else
                    "Renommage refusé : clé, fichier ou nom invalide."
                )
                self._refresh()
                return False

            self.GLib.idle_add(deliver)

        handle.add_done_callback(finished)

    def open_session(self):
        if (self.vault_directory is None or self._closed
                or self.is_recording() or self.is_scanning()):
            return
        session_id = self.session_list.get_active_id()
        if not session_id:
            self.status.set_label("Choisir d'abord une session locale.")
            return
        passphrase = self.vault_passphrase.get_text()
        self.vault_passphrase.set_text("")
        try:
            stored = PrivateSessionVault(self.vault_directory).open(
                session_id, passphrase,
            )
        except VaultError:
            self.status.set_label(
                "Ouverture refusée : clé incorrecte, session illisible "
                "ou coffre non sûr. Aucun site contacté."
            )
            return
        self._discard_private()
        self._trace = stored.trace
        try:
            self._review = IdentifierReview(stored.trace)
        except Exception:
            self._discard_private()
            self.status.set_label(
                "Archive conservée dans le coffre, revue impossible en mémoire."
            )
            return
        self._show_candidates(update_status=False)
        private = stored.private_summary()
        # This is the user's private, unlocked GTK panel: origin and time
        # never travel through diagnostics, public broker, or ZIP manifest.
        labels = {
            "completed": "terminée",
            "interrupted": "interrompue",
            "partial": "partielle (dernière sauvegarde seulement)",
        }
        suffix = (" La fin du parcours n'est PAS attestée."
                  if stored.recovery_status != "completed" else "")
        self.status.set_label(
            f"Session {labels[stored.recovery_status]} ; "
            f"dernière sauvegarde {private['saved_at']} ; "
            f"origine {private['origin']} ; "
            f"{private['exchanges']} échanges, "
            f"{sum(private['omissions'].values())} omissions ; "
            f"{self._review.pending()} décisions à reprendre."
            + suffix
        )
        self._refresh()

    def delete_session(self):
        if (self.vault_directory is None or self._closed
                or self.is_recording() or self.is_scanning()):
            return
        if not self.delete_confirm.get_active():
            self.status.set_label("Suppression interdite sans confirmation.")
            return
        self.delete_confirm.set_active(False)
        session_id = self.session_list.get_active_id()
        passphrase = self.vault_passphrase.get_text()
        self.vault_passphrase.set_text("")
        try:
            PrivateSessionVault(self.vault_directory).delete(
                session_id, passphrase, confirmed=True,
            )
        except VaultError:
            self.status.set_label("Suppression refusée : session ou clé invalide.")
            return
        if (self._trace is not None
                and getattr(self._trace, "vault_session_id", None) == session_id):
            self._discard_private()
        self.refresh_sessions()
        self.status.set_label("Session locale supprimée du coffre.")
        self._refresh()

    def _prompt_private_folder(self):
        """Native opt-in directory picker; only private 0700 is accepted on save."""
        if (self._closed or self._private_destination_dialog is not None
                or self.is_recording()):
            return
        Gtk = self.Gtk
        if not hasattr(Gtk, "FileChooserNative"):
            self.status.set_label("Sélection de dossier indisponible.")
            return
        root = self.widget.get_root()
        parent = root if isinstance(root, Gtk.Window) else None
        chooser = Gtk.FileChooserNative.new(
            "Choisir un dossier privé pour le ZIP chiffré", parent,
            Gtk.FileChooserAction.SELECT_FOLDER, "Choisir", "Annuler",
        )
        self._private_destination_dialog = chooser

        def responded(dialog, response):
            if dialog is not self._private_destination_dialog:
                dialog.destroy()
                return
            self._private_destination_dialog = None
            try:
                if response == Gtk.ResponseType.ACCEPT and not self._closed:
                    selected = dialog.get_file()
                    path = selected.get_path() if selected else None
                    if path:
                        self.private_export_directory.set_text(path)
            finally:
                dialog.destroy()

        chooser.connect("response", responded)
        chooser.show()

    def export_private_zip(self):
        if self.vault_directory is None or self._closed or self.is_recording():
            return
        if not self.private_export_confirm.get_active():
            self.status.set_label(
                "Export privé refusé : confirmation spécifique requise."
            )
            return
        self.private_export_confirm.set_active(False)
        sid = self.session_list.get_active_id()
        path = self.private_export_directory.get_text().strip()
        passphrase = self.vault_passphrase.get_text()
        self.vault_passphrase.set_text("")
        export_passphrase = self.private_export_passphrase.get_text()
        self.private_export_passphrase.set_text("")
        if not sid or not path or len(export_passphrase) < 12:
            self.status.set_label(
                "Choisir une session, un dossier privé et une nouvelle phrase "
                "secrète d'export distincte (12 caractères minimum)."
            )
            return
        if passphrase == export_passphrase:
            self.status.set_label(
                "Utiliser une phrase d'export différente de celle du coffre."
            )
            return
        stored = None
        try:
            stored = PrivateSessionVault(self.vault_directory).open(
                sid, passphrase,
            )
            filename = save_private_zip(
                stored, Path(path), export_passphrase, confirmed=True,
            )
        except Exception:
            # Never emit path, provider data or cryptography exception text.
            self.status.set_label(
                "ZIP privé refusé : déverrouillage, répertoire 0700 ou "
                "limite de sécurité incorrecte. Aucun export non chiffré."
            )
            return
        finally:
            if stored is not None:
                stored.trace.destroy()
        self.status.set_label(
            "ZIP PRIVÉ CHIFFRÉ enregistré : " + filename
            + " — ne pas partager sans revue ; aucun téléversement."
        )
        self._refresh()

    def toggle_pause(self):
        if (self._closed or not self.is_recording() or self._pause_control is None
                or self._stop is None or self._stop.is_set()):
            return
        previous = self._pause_control.state()[0]
        self._pause_control.set_paused(not previous)
        self.status.set_label(
            "Capture HTTP privée suspendue ; aucun nouveau corps conservé."
            if not previous else "Capture HTTP privée reprise."
        )
        self._refresh()

    def stop_capture(self):
        if self._stop is not None and not self._stop.is_set():
            self._stop.set()
            self.status.set_label("Arrêt demandé ; préparation de la revue…")
            self._refresh()

    def change_candidate_page(self, offset: int):
        """Navigate bounded local GTK rows; review decisions stay in RAM."""
        if self._closed or self._review is None or self.is_recording():
            return
        total = len(self._review.candidates())
        last = max(0, (total - 1) // _REVIEW_PAGE_SIZE)
        target = min(last, max(0, self._candidate_page + offset))
        if target != self._candidate_page:
            self._candidate_page = target
            self._show_candidates(update_status=False)

    def _show_candidates(self, *, update_status=True):
        self._clear_candidates()
        self._text("")
        rows = self._review.local_rows()
        total = len(rows)
        self._candidate_page = min(
            self._candidate_page, max(0, (total - 1) // _REVIEW_PAGE_SIZE),
        )
        first = self._candidate_page * _REVIEW_PAGE_SIZE
        last = min(total, first + _REVIEW_PAGE_SIZE)
        self.candidate_page_status.set_label(
            f"Identifiants {first + 1}–{last} sur {total}"
            if total else "Aucun identifiant à examiner."
        )
        if update_status:
            self.status.set_label(
                f"{len(self._trace.exchanges)} échanges en mémoire ; "
                f"{total} identifiants à décider individuellement. "
                "Aucun export possible."
            )
        Gtk = self.Gtk
        if not rows:
            self.candidates_box.append(Gtk.Label(
                label="Aucun identifiant proposé : vérifier quand même les contenus.",
                xalign=0, wrap=True,
            ))
        for row in rows[first:last]:
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            # Candidate values are shown only to the local operator.
            label = Gtk.Label(
                label=f"{row['key']} · {row['kind']} · {row['occurrences']}× : "
                      f"{row['value']}",
                xalign=0, wrap=True,
            )
            label.set_selectable(True)
            label.set_hexpand(True)
            line.append(label)
            chooser = Gtk.ComboBoxText()
            chooser.append("choose", "Choisir…")
            chooser.append("anonymize", "Anonymiser")
            chooser.append("keep", "Conserver explicitement")
            chooser.append("exclude", "Exclure l'échange")
            chooser.set_active_id(row["decision"] or "choose")
            candidate_key = row["key"]
            review = self._review

            def changed(combo, key=candidate_key, current=review):
                if self._review is not current or self._closed:
                    return
                value = combo.get_active_id()
                if value in {"anonymize", "keep", "exclude"}:
                    current.decide(key, value)
                self._invalidate_bundle()
                self._text("")
                self.status.set_label(
                    f"Décisions restantes : {current.pending()}. "
                    "Conserver un identifiant peut révéler des données personnelles."
                )
            chooser.connect("changed", changed)
            chooser.set_tooltip_text(
                "Raccourcis clavier : 1 anonymiser, 2 conserver, 3 exclure."
            )
            # Gtk.ComboBoxText navigation depends on a popover / GTK minor
            # version. Capture only unmodified keys in this candidate row
            # before the popup handles them, allowing a reliable accessible
            # choice with XTEST and keyboard alone. No bulk decision is made.
            # Offline headless unit tests use a minimal fake Gtk contract;
            # keyboard event controllers exist only in a real GTK4 runtime.
            # Preserve non-GUI review/redaction tests without importing GI.
            if hasattr(Gtk, "EventControllerKey"):
                shortcuts = Gtk.EventControllerKey()
                shortcuts.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

                def candidate_key_pressed(_controller, keyval, _keycode, state,
                                          current=chooser):
                    from gi.repository import Gdk
                    if state & (Gdk.ModifierType.CONTROL_MASK
                                | Gdk.ModifierType.ALT_MASK
                                | Gdk.ModifierType.SUPER_MASK):
                        return False
                    action = {
                        Gdk.KEY_1: "anonymize", Gdk.KEY_KP_1: "anonymize",
                        Gdk.KEY_2: "keep", Gdk.KEY_KP_2: "keep",
                        Gdk.KEY_3: "exclude", Gdk.KEY_KP_3: "exclude",
                    }.get(keyval)
                    if action is None:
                        return False
                    current.set_active_id(action)
                    return True

                shortcuts.connect("key-pressed", candidate_key_pressed)
                line.add_controller(shortcuts)
            line.append(chooser)
            self.candidates_box.append(line)
        self._refresh()

    def preview(self):
        if self._closed or self._review is None:
            return
        if self._review.pending():
            self.status.set_label(
                f"Encore {self._review.pending()} choix individuels requis.",
            )
            return
        try:
            sample = json.dumps(self._review.preview(), ensure_ascii=False, indent=2)
        except RecorderError:
            self.status.set_label("Prévisualisation impossible.")
            return
        # This GTK preview is intentionally bounded. It is NOT the full ZIP
        # preview, which must be implemented and re-scanned under #2530.
        max_chars = 80000
        self._text(sample[:max_chars])
        self.status.set_label(
            "Prévisualisation locale"
            + (" tronquée à 80 000 caractères" if len(sample) > max_chars else "")
            + " — aucune archive générée."
        )

    def prepare_zip(self):
        if (self._closed or self.is_recording()
                or self._review is None or self._review.pending()):
            self.status.set_label("Toutes les décisions individuelles sont requises.")
            return
        self._invalidate_bundle()
        try:
            plan = prepare_reviewed_zip(self._review)
            preview = plan.preview()
        except Exception:
            # Provider observations and rejection details are not diagnostic
            # material for the GTK status or application logs.
            self.status.set_label("Archive refusée : risque ou limite de contenu.")
            self._emit_failure("zip_prepare", "zip_prepare_failed")
            return
        self._bundle_plan = plan
        self._bundle_preview = preview
        self.bundle_summary.set_label(
            f"ZIP en mémoire : {len(preview['members'])} membres, "
            f"{preview['bytes']} octets, SHA-256 : {preview['sha256']}. "
            "Sélectionner chaque membre pour inspecter son texte EXACT."
        )
        for item in preview["members"]:
            self.bundle_member.append(item["name"], item["name"])
        if preview["members"]:
            self.bundle_member.set_active_id(preview["members"][0]["name"])
        self.status.set_label(
            "ZIP préparé en mémoire uniquement. Relire les membres, "
            "cocher la confirmation puis exporter volontairement."
        )
        self._refresh()

    def _show_zip_member(self):
        if self._bundle_preview is None:
            return
        name = self.bundle_member.get_active_id()
        match = next((m for m in self._bundle_preview["members"]
                      if m["name"] == name), None)
        if match is not None:
            # Per-member text equals the exact bytes that would be archived,
            # decoded strictly, with no truncation or stale request body.
            self.bundle_text.get_buffer().set_text(match["text"])

    def _prompt_export_file(self, finished):
        """Native GTK4 Save-As; only an accepted filename reaches the writer."""
        Gtk = self.Gtk
        if not hasattr(Gtk, "FileChooserNative"):
            self.status.set_label("Dialogue d'enregistrement indisponible.")
            return
        root = self.widget.get_root()
        parent = root if isinstance(root, Gtk.Window) else None
        chooser = Gtk.FileChooserNative.new(
            "Enregistrer le ZIP anonymisé", parent,
            Gtk.FileChooserAction.SAVE, "Enregistrer", "Annuler",
        )
        chooser.set_current_name("cifereca-revue.zip")
        self._export_dialog = chooser

        def responded(dialog, response):
            if self._export_dialog is not dialog:
                dialog.destroy()
                return
            self._export_dialog = None
            try:
                if (response == Gtk.ResponseType.ACCEPT
                        and not self._closed):
                    selected = dialog.get_file()
                    native_path = selected.get_path() if selected else None
                    if native_path:
                        finished(Path(native_path))
                    else:
                        self.status.set_label(
                            "Destination locale inaccessible : aucun ZIP créé."
                        )
            finally:
                dialog.destroy()

        chooser.connect("response", responded)
        chooser.show()

    def export_zip(self):
        if (self._closed or self._bundle_plan is None
                or self._export_dialog is not None
                or not self.export_confirm.get_active()):
            self.status.set_label("Export interdit : confirmation manquante.")
            return
        plan = self._bundle_plan
        generation = self._generation

        def selected(path):
            if (self._closed or generation != self._generation
                    or self._bundle_plan is not plan
                    or not self.export_confirm.get_active()):
                return  # plan or user consent changed while chooser was open
            try:
                plan.save_as(path, user_confirmed=True)
            except Exception:
                # Never report paths or private values in diagnostics.
                self.status.set_label(
                    "Enregistrement refusé : vérifiez le dossier, le nom .zip "
                    "et l'absence de fichier existant. Aucun ZIP écrasé."
                )
                self._emit_failure("zip_export", "zip_export_failed")
                return
            self._invalidate_bundle()
            self.status.set_label(
                "ZIP anonymisé enregistré à l'emplacement choisi "
                "(mode privé 0600) — aucun téléversement."
            )
            self._refresh()

        self._prompt_export_file(selected)

    def discard(self):
        if self.is_recording():
            return
        self._discard_private()
        self.status.set_label("Données privées effacées de la revue.")
        self._refresh()

    def destroy(self):
        if self._private_destination_dialog is not None:
            chooser, self._private_destination_dialog = (
                self._private_destination_dialog, None
            )
            chooser.destroy()
        if self._export_dialog is not None:
            chooser, self._export_dialog = self._export_dialog, None
            chooser.destroy()
        self._closed = True
        self._generation += 1
        if self._stop is not None:
            self._stop.set()
        # Clear decrypted origin/date/count labels in the detached GTK page:
        # list of opaque session IDs alone is also discarded on deactivation.
        if self.session_list is not None:
            self.session_list.remove_all()
        if getattr(self, "session_name", None) is not None:
            self.session_name.set_text("")
        # Never leave typed vault/export secrets in a detached GTK page.
        if self.vault_passphrase is not None:
            self.vault_passphrase.set_text("")
        if self.private_export_passphrase is not None:
            self.private_export_passphrase.set_text("")
        self._discard_private()
        self._stop = None
        self._handle = None
        self._index_handle = None
