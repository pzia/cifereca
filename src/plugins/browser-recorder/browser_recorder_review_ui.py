"""Private HTTP review and explicitly confirmed, bounded ZIP preview/export.

HAR v0.1 stays separate. Browser bytes live in RAM until the user reviews
all individual identifiers and separately approves each frozen ZIP archive.
No upload, hidden export, automatic GET replay or raw HAR persistence.
"""
from __future__ import annotations

import json
from pathlib import Path
from threading import Event
from time import monotonic

from browser_recorder import RecorderError, site_origin
from browser_recorder_http import CapturePause, record_http
from browser_recorder_review import IdentifierReview
from browser_recorder_zip import prepare_reviewed_zip
from browser_recorder_vault import PrivateSessionVault, VaultError
from browser_recorder_private_zip import save_session_llm_zip


_REVIEW_PAGE_SIZE = 24  # Bound GTK widgets even for 256 distinct private IDs.

_PRIVATE_EXPORT_FAILURES = {
    "vault_unlock_failed": "Coffre impossible à déverrouiller : fermez-le puis ouvrez-le avec la bonne phrase secrète. Une archive altérée peut aussi empêcher son ouverture.",
    "vault_passphrase_required": "Ouvrez le coffre avec une phrase secrète de 12 caractères minimum, puis réessayez.",
    "vault_crypto_unavailable": "Chiffrement indisponible : ouvrez Plugins → Browser Recorder → Installer les dépendances Python…, puis réessayez.",
    "vault_unsafe_directory": "Dossier du coffre non sûr : vérifiez son propriétaire et ses droits privés (0700). Le dossier de destination du ZIP n’exige pas ces droits.",
    "vault_unsafe_file": "Session du coffre inaccessible ou non sûre : actualisez la liste et choisissez une session valide.",
    "vault_invalid": "Archive privée invalide ou incompatible : choisissez une autre session ou mettez à jour Browser Recorder. Ne supprimez pas la source.",
    "private_zip_invalid": "Archive privée invalide : rouvrez la session depuis le coffre, puis réessayez.",
    "vault_limit": "Limite de taille du coffre atteinte : choisissez une session plus courte.",
    "private_zip_limit": "Limite de taille du ZIP atteinte : choisissez une session plus courte.",
    "private_zip_destination": "Destination invalide : choisissez un dossier existant et un nom de fichier terminé par .zip.",
    "private_zip_exists": "Ce fichier existe déjà : choisissez un autre nom dans Enregistrer sous. Aucun écrasement.",
    "private_zip_write_failed": "Écriture impossible : vérifiez l’espace libre et les droits d’écriture du dossier, puis réessayez.",
}


def _private_export_failure_message(error):
    # Never stringify an exception or trust an arbitrary string argument.
    code = error.args[0] if type(error) is VaultError and error.args else None
    return (_PRIVATE_EXPORT_FAILURES.get(code,
            "Export privé impossible : rouvrez la session et choisissez une autre destination, puis réessayez.")
            if type(code) is str else
            "Export privé impossible : rouvrez la session et choisissez une autre destination, puis réessayez.")

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
        # Unlocked only by an explicit open action. Transient bytearray is
        # wiped on lock/destroy; never persisted, logged, or sent to plugins.
        self._vault_secret = None
        self.vault_state = None
        self.vault_open_button = None
        self.vault_close_button = None
        self.session_list = None
        self.delete_confirm = None
        self.private_export_button = None
        self.current_private_export_button = None
        self._private_destination_dialog = None
        self._bundle_plan = None
        self._bundle_preview = None
        self._export_dialog = None
        self._last_export_path = None
        self._download_dialog = None
        self._download_choice_done = None
        self._download_outcomes = {}
        self._handle = None
        self._stop = None
        self._pause_control = None
        self._trace = None
        self._review = None
        self._candidate_page = 0
        self._pending_cursor = -1
        self._closed = False
        self._generation = 0
        self._index_handle = None
        self._retention_plan = None

        # Workspaces have independent scroll positions. The vault lock
        # controls remain reachable regardless of the selected tab.
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_hexpand(True)
        root.set_vexpand(True)
        self.widget = root
        self.status = Gtk.Label(
            label="Aucune observation HTTP privée en mémoire.",
            xalign=0, wrap=True,
        )
        self.status.set_selectable(True)
        root.append(self.status)
        self.coverage_status = Gtk.Label(label="", xalign=0, wrap=True)
        root.append(self.coverage_status)
        if self.vault_directory is not None:
            self.vault_state = Gtk.Label(
                label="Coffre fermé · saisir la phrase secrète une fois pour l'ouvrir.",
                xalign=0, wrap=True,
            )
            root.append(self.vault_state)
            vault_controls = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
            )
            self.vault_passphrase = Gtk.Entry()
            self.vault_passphrase.set_visibility(False)
            self.vault_passphrase.set_hexpand(True)
            self.vault_passphrase.set_placeholder_text(
                "Phrase secrète du coffre (12 caractères minimum)"
            )
            vault_controls.append(self.vault_passphrase)
            self.vault_open_button = Gtk.Button(label="Ouvrir le coffre")
            self.vault_close_button = Gtk.Button(label="Fermer le coffre")
            vault_controls.append(self.vault_open_button)
            vault_controls.append(self.vault_close_button)
            root.append(vault_controls)
            self.vault_open_button.connect(
                "clicked", lambda *_: self.unlock_sessions(),
            )
            self.vault_close_button.connect(
                "clicked", lambda *_: self.lock_vault(),
            )

        self.tabs = Gtk.Notebook()
        self.tabs.set_scrollable(True)
        self.tabs.set_vexpand(True)
        self.tabs.set_hexpand(True)
        self.settings_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10,
        )
        self.capture_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10,
        )
        self.sessions_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10,
        )
        self.review_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10,
        )
        for section, title in (
            (self.settings_box, "1 · Paramètres"),
            (self.capture_box, "2 · Capture"),
            (self.sessions_box, "3 · Sessions et export privé"),
            (self.review_box, "4 · Revue et ZIP LLM"),
        ):
            for side in ("top", "bottom", "start", "end"):
                getattr(section, "set_margin_" + side)(12)
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroller.set_hexpand(True)
            scroller.set_vexpand(True)
            scroller.set_child(section)
            self.tabs.append_page(scroller, Gtk.Label(label=title))
        root.append(self.tabs)
        box = self.settings_box
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
            label="Téléchargements Chromium : les fichiers binaires/licences "
                  "n'entrent JAMAIS dans la trace ou le ZIP. À l'arrêt, un "
                  "« Enregistrer sous… » distinct est proposé pour chaque "
                  "téléchargement (8 maximum). Annuler = aucune conservation.",
            xalign=0, wrap=True,
        ))
        # Page host supplies real Gtk widgets; standalone headless tests use
        # immutable site/consent proxies, which have no GTK parent.
        if isinstance(site, getattr(Gtk, "Widget", ())):
            box.append(Gtk.Label(
                label="Adresse HTTPS du site à parcourir",
                xalign=0, wrap=True,
            ))
            box.append(site)
        if isinstance(consent, getattr(Gtk, "Widget", ())):
            box.append(consent)
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
        self.prefer_identity = Gtk.CheckButton(
            label="Demander des réponses non compressées (Accept-Encoding: identity)."
        )
        box.append(self.prefer_identity)
        box.append(Gtk.Label(
            label="Option pour une nouvelle capture : modifie la négociation HTTP "
                  "de tout ce navigateur, sans ajouter de requête. Les limites "
                  "de capture et les origines restent identiques. Si le serveur "
                  "impose gzip/Brotli, les corps restent exclus ; les anciennes "
                  "captures ne sont pas réparées.", xalign=0, wrap=True,
        ))
        self.diagnose_origins = Gtk.CheckButton(
            label="Pour cette capture, afficher localement les domaines exclus (RAM uniquement)."
        )
        box.append(self.diagnose_origins)
        box.append(Gtk.Label(
            label="Opt-in privé : 16 origines HTTPS maximum, sans chemin ni corps. "
                  "Aucun ajout automatique au périmètre, aucun domaine dans "
                  "le coffre, les ZIP ou Diagnostics. Liste effacée à la fermeture.",
            xalign=0, wrap=True,
        ))
        self.excluded_domains = Gtk.Label(label="", xalign=0, wrap=True)
        self.excluded_domains.set_selectable(True)
        domain_scroll = Gtk.ScrolledWindow()
        domain_scroll.set_max_content_height(160)
        domain_scroll.set_propagate_natural_height(True)
        domain_scroll.set_child(self.excluded_domains)
        self.sessions_box.append(domain_scroll)
        if self.vault_directory is not None:
            box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            box.append(Gtk.Label(
                label="Coffre chiffré : cliquez « Ouvrir le coffre » une seule "
                      "fois pour réutiliser la phrase secrète en mémoire "
                      "pendant cette ouverture de page. « Fermer le coffre » "
                      "efface l'accès en mémoire sans supprimer les sessions. "
                      "Chaque capture exige toujours son consentement.",
                xalign=0, wrap=True,
            ))
            self.vault_consent = Gtk.CheckButton(
                label="J'autorise le stockage local chiffré de cette capture."
            )
            box.append(self.vault_consent)
            box = self.sessions_box
            box.append(Gtk.Label(
                label="Sessions archivées : consultez les captures chiffrées "
                      "sans contacter les sites. Une session sélectionnée peut "
                      "être exportée telle que capturée pour un LLM. L'archive "
                      "est alors en clair et contient des données personnelles.",
                xalign=0, wrap=True,
            ))
            self.sessions_refresh = Gtk.Button(label="Lister les sessions archivées")
            self.sessions_unlock = Gtk.Button(
                label="Déverrouiller les métadonnées des sessions…"
            )
            self.session_list = Gtk.ComboBoxText()
            self.session_list.connect("changed", lambda *_: self._refresh())
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
                label="Exporter une session pour un LLM : ouvrez le coffre "
                      "une fois, sélectionnez une session, puis choisissez où "
                      "enregistrer le fichier ZIP. Pas de second mot de passe ni "
                      "de permissions de dossier à configurer. Le coffre reste "
                      "chiffré ; le ZIP exporté est EN CLAIR (URL, identifiants "
                      "et corps conservés) : à partager uniquement volontairement "
                      "avec le LLM de votre choix. Aucun envoi automatique.",
                xalign=0, wrap=True,
            ))
            self.private_export_button = Gtk.Button(
                label="Exporter cette session pour un LLM…"
            )
            box.append(self.private_export_button)
            self.private_export_button.connect(
                "clicked", lambda *_: self.export_private_zip(),
            )
        box = self.capture_box
        box.append(Gtk.Label(
            label="Capture du parcours — configurez d'abord le site HTTPS, "
                  "les autorisations et le coffre dans Paramètres. "
                  "Seul un démarrage volontaire ouvre Chromium.",
            xalign=0, wrap=True,
        ))
        self.configure_button = Gtk.Button(label="Modifier les paramètres…")
        box.append(self.configure_button)
        self.configure_button.connect(
            "clicked", lambda *_: self.tabs.set_current_page(0),
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
        for button in (self.start, self.stop, self.pause_button):
            controls.append(button)
        box.append(controls)
        box.append(Gtk.Label(
            label="Après l'arrêt, la revue s'ouvre dans l'onglet 4. "
                  "Aucun export ni envoi externe n'est automatique.",
            xalign=0, wrap=True,
        ))
        box = self.review_box
        if self.vault_directory is not None:
            box.append(Gtk.Label(
                label="Session archivée dans le coffre : vous pouvez l'exporter "
                      "directement pour un LLM sans attendre les décisions "
                      "d'anonymisation. Ce ZIP est NON CHIFFRÉ et contient les "
                      "observations privées conservées. Ouvrez le coffre "
                      "une fois en haut, puis choisissez où l'enregistrer.",
                xalign=0, wrap=True,
            ))
            self.current_private_export_button = Gtk.Button(
                label="Exporter directement cette session privée pour LLM…"
            )
            box.append(self.current_private_export_button)
            self.current_private_export_button.connect(
                "clicked", lambda *_: self.export_private_zip(current=True),
            )
            box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        review_controls = Gtk.FlowBox()
        review_controls.set_selection_mode(Gtk.SelectionMode.NONE)
        review_controls.set_min_children_per_line(1)
        review_controls.set_max_children_per_line(2)
        review_controls.set_row_spacing(6)
        review_controls.set_column_spacing(8)
        for button in (self.preview_button, self.clear):
            review_controls.append(button)
        box.append(review_controls)
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
        self.candidate_next_pending = Gtk.Button(
            label="Aller au prochain identifiant sans décision"
        )
        pagination.append(self.candidate_previous)
        pagination.append(self.candidate_page_status)
        pagination.append(self.candidate_next)
        pagination.append(self.candidate_next_pending)
        box.append(pagination)
        self.candidate_previous.connect("clicked", lambda *_: self.change_candidate_page(-1))
        self.candidate_next.connect("clicked", lambda *_: self.change_candidate_page(1))
        self.candidate_next_pending.connect(
            "clicked", lambda *_: self.go_to_next_pending(),
        )

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
        box.append(Gtk.Label(
            label="Membres du ZIP exact — la recherche filtre seulement les "
                  "NOMS des fichiers, jamais leurs valeurs. Sélectionnez "
                  "un fichier pour consulter les octets UTF-8 approuvés.",
            xalign=0, wrap=True,
        ))
        self.member_filter = Gtk.Entry()
        self.member_filter.set_placeholder_text(
            "Rechercher un nom : manifest, coverage, timeline, exchanges, bodies…"
        )
        box.append(self.member_filter)
        self.bundle_member = Gtk.ComboBoxText()
        box.append(self.bundle_member)
        self.bundle_progress = Gtk.Label(
            label="Aucun ZIP à parcourir.", xalign=0, wrap=True,
        )
        box.append(self.bundle_progress)
        self.coverage_button = Gtk.Button(
            label="Voir directement le rapport de couverture"
        )
        box.append(self.coverage_button)
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
            label="J'ai examiné le ZIP revu. Il peut contenir des identifiants "
                  "expressément conservés et des données sensibles. "
                  "J'accepte de créer localement cette archive, sans envoi.",
            xalign=0, wrap=True,
        ))
        box.append(self.export_confirm)
        self.export_button = Gtk.Button(label="Enregistrer sous… le ZIP revu pour LLM")
        box.append(self.export_button)
        self.open_export_folder_button = Gtk.Button(
            label="Ouvrir le dossier du ZIP enregistré"
        )
        box.append(self.open_export_folder_button)
        self.open_export_folder_button.connect(
            "clicked", lambda *_: self.open_export_folder()
        )
        self.plan_button.connect("clicked", lambda *_: self.prepare_zip())
        self.bundle_member.connect("changed", lambda *_: self._show_zip_member())
        self.member_filter.connect(
            "changed", lambda *_: self._populate_zip_members(),
        )
        self.coverage_button.connect(
            "clicked", lambda *_: self._select_coverage_report(),
        )
        self.export_confirm.connect("toggled", lambda *_: self._refresh())
        self.export_button.connect("clicked", lambda *_: self.export_zip())

        self.start.connect("clicked", lambda *_: self.begin())
        self.stop.connect("clicked", lambda *_: self.stop_capture())
        self.pause_button.connect("clicked", lambda *_: self.toggle_pause())
        self.preview_button.connect("clicked", lambda *_: self.preview())
        self.clear.connect("clicked", lambda *_: self.discard())
        self._refresh()

    def _vault_password(self):
        """Read the unlocked in-process key, or one explicitly typed fallback.

        Legacy per-action typing remains possible while closed. The opened
        vault never writes its master phrase to files, settings or logs.
        """
        cached = getattr(self, "_vault_secret", None)
        entered = self.vault_passphrase.get_text()
        self.vault_passphrase.set_text("")
        return cached.decode("utf-8") if cached is not None else entered

    def _wipe_vault_password(self):
        """Best-effort overwrite of the sole retained mutable password buffer."""
        cached = getattr(self, "_vault_secret", None)
        if cached is not None:
            for index in range(len(cached)):
                cached[index] = 0
            self._vault_secret = None

    def lock_vault(self):
        """Explicitly forget key and decrypted UI, never delete vault files."""
        if (self.vault_directory is None or self._closed
                or self.is_recording() or self.is_scanning()):
            return
        # Invalidate deferred Save-As callbacks, even if a chooser was opened
        # while the vault was unlocked; a stale dialog cannot publish a ZIP.
        self._generation += 1
        if self._private_destination_dialog is not None:
            chooser, self._private_destination_dialog = (
                self._private_destination_dialog, None,
            )
            chooser.destroy()
        if self._export_dialog is not None:
            chooser, self._export_dialog = self._export_dialog, None
            chooser.destroy()
        self._wipe_vault_password()
        self.vault_passphrase.set_text("")
        self._retention_plan = None
        self._last_export_path = None
        self.session_list.remove_all()
        self.session_name.set_text("")
        self._discard_private()
        self.status.set_label(
            "Coffre fermé : phrase secrète effacée de la session, aperçus "
            "privés masqués. Archives chiffrées conservées sur disque."
        )
        self._refresh()

    def _show_coverage(self, metrics):
        if type(metrics) is not dict:
            return
        names = ("exchanges", "limit", "text_responses", "binary_skipped",
                 "compressed", "out_of_scope", "static_metadata_budget")
        if any(type(metrics.get(key)) is not int or not 0 <= metrics[key] <= 1_000_000
               for key in names):
            return
        label = (
            f"{metrics['exchanges']}/{metrics['limit']} échanges · "
            f"{metrics['text_responses']} réponses textuelles conservées · "
            f"{metrics['binary_skipped']} binaires exclus · "
            f"{metrics['compressed']} corps compressés exclus · "
            f"{metrics['static_metadata_budget']} ressources statiques hors budget · "
            f"{metrics['out_of_scope']} hors périmètre."
        )
        if metrics["text_responses"] == 0:
            label += " Aucune réponse conservée : analyse des réponses impossible."
        if metrics["limit"] and metrics["exchanges"] * 5 >= metrics["limit"] * 4:
            label += " Attention : capacité de capture presque atteinte."
        self.coverage_status.set_label(label)

    def _show_origin_diagnostics(self, trace):
        origins, overflow = trace.origin_diagnostics()
        if not origins and not overflow:
            self.excluded_domains.set_label("")
            return
        lines = ["Domaines exclus observés (ne prouve pas leur utilité). "
                 "Pour les examiner, démarrer une nouvelle capture avec son "
                 "URL et consentement, ou les autoriser en métadonnées seulement."]
        lines.extend(f"{origin} : {count} requête(s)" for origin, count in sorted(origins.items()))
        if overflow:
            lines.append(f"{overflow} requête(s) supplémentaires hors inventaire (limite 16 domaines).")
        self.excluded_domains.set_label("\n".join(lines))

    def is_recording(self):
        return self._handle is not None and not self._handle.done()

    def has_private_data(self):
        return (self.is_recording()
                or self._trace is not None or self._review is not None)

    def _refresh(self):
        self.prefer_identity.set_sensitive(not self._closed and not self.is_recording())
        busy = self.is_recording()
        self.diagnose_origins.set_sensitive(not self._closed and not busy)
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
            unlocked = self._vault_secret is not None
            self.vault_state.set_label(
                "Coffre OUVERT · clé en mémoire uniquement · Fermer pour verrouiller."
                if unlocked else
                "Coffre FERMÉ · phrase secrète requise pour l'ouvrir."
            )
            self.vault_passphrase.set_sensitive(
                not self._closed and not busy and not scanning and not unlocked
            )
            self.vault_open_button.set_sensitive(
                not self._closed and not busy and not scanning and not unlocked
            )
            self.vault_close_button.set_sensitive(
                not self._closed and not busy and not scanning and unlocked
            )
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
                and self._private_destination_dialog is None
                and bool(self.session_list.get_active_id())
            )
            if self.current_private_export_button is not None:
                self.current_private_export_button.set_sensitive(
                    not self._closed and not busy and not scanning
                    and self._private_destination_dialog is None
                    and bool(getattr(self._trace, "vault_session_id", None))
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
        self.candidate_next_pending.set_sensitive(
            not self._closed and not busy and self._review is not None
            and self._review.pending() > 0
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
        self.open_export_folder_button.set_sensitive(
            not self._closed and self._last_export_path is not None
        )
        self.coverage_button.set_sensitive(
            not self._closed and self._bundle_preview is not None
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
        self.member_filter.set_text("")
        self.bundle_progress.set_label("Aucun ZIP à parcourir.")
        self.coverage_button.set_sensitive(False)
        self.bundle_text.get_buffer().set_text("")
        self.bundle_summary.set_label("Aucune archive préparée.")
        self.export_confirm.set_active(False)
        self._refresh()

    def _discard_private(self):
        self.coverage_status.set_label("")
        self.excluded_domains.set_label("")
        self._candidate_page = 0
        self._pending_cursor = -1
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

    def _emit_private_export(self, code, started, output_bytes=0):
        try:
            if callable(self._diagnostic):
                self._diagnostic(
                    "private_zip_export", code,
                    duration_ms=max(0, int((monotonic() - started) * 1000)),
                    output_bytes=output_bytes,
                )
        except Exception:
            # Optional/older Diagnostics cannot affect an encrypted export.
            pass

    def is_scanning(self):
        # The worker may finish before its GTK idle delivery runs. Keep
        # actions locked and report "busy" until the UI has *received* the
        # result, not merely until the background future is done.
        return self._index_handle is not None

    def _download_status(self, code):
        """Only fixed counters from worker, never URL or output path."""
        if code in {
            "download_started", "download_saved_by_user", "download_failed",
            "download_cancelled", "download_limit",
        }:
            self._download_outcomes[code] = self._download_outcomes.get(code, 0) + 1

    def _cancel_download_dialog(self, ready):
        """GTK thread: unblock worker on cancel, timeout, or page shutdown."""
        if self._download_choice_done is ready:
            self._download_choice_done = None
            chooser, self._download_dialog = self._download_dialog, None
            if chooser is not None:
                chooser.destroy()
        ready.set()
        return False

    def _choose_download_from_worker(self, number):
        """Save-As choice; only the worker waits, GTK remains responsive."""
        ready = Event()
        result = {}
        generation = self._generation

        def show():
            if (self._closed or self._generation != generation
                    or self._download_dialog is not None
                    or not hasattr(self.Gtk, "FileChooserNative")):
                ready.set()
                return False
            Gtk = self.Gtk
            root = self.widget.get_root()
            parent = root if isinstance(root, Gtk.Window) else None
            chooser = Gtk.FileChooserNative.new(
                f"Enregistrer volontairement le téléchargement {number}",
                parent, Gtk.FileChooserAction.SAVE, "Enregistrer", "Annuler",
            )
            chooser.set_current_name(
                f"cifereca-telechargement-{number:02d}.bin",
            )
            self._download_dialog = chooser
            self._download_choice_done = ready
            self.status.set_label(
                f"Téléchargement {number} : choisir un fichier ou annuler. "
                "Aucun contenu téléchargé dans le ZIP."
            )

            def responded(dialog, response):
                if dialog is not self._download_dialog:
                    dialog.destroy()
                    ready.set()
                    return
                self._download_dialog = None
                self._download_choice_done = None
                try:
                    if (response == Gtk.ResponseType.ACCEPT
                            and not self._closed
                            and self._generation == generation):
                        selected = dialog.get_file()
                        selected_path = selected.get_path() if selected else None
                        if selected_path:
                            result["path"] = Path(selected_path)
                finally:
                    dialog.destroy()
                    ready.set()

            chooser.connect("response", responded)
            chooser.show()
            return False

        self.GLib.idle_add(show)
        if not ready.wait(timeout=120):
            self.GLib.idle_add(self._cancel_download_dialog, ready)
            return None
        return result.get("path")

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
            passphrase = self._vault_password()
            if len(passphrase) < 12:
                self.status.set_label(
                    "Phrase secrète d'au moins 12 caractères requise."
                )
                return
            self.vault_consent.set_active(False)
        self._discard_private()
        self._generation += 1
        generation = self._generation
        self._stop = Event()
        stop = self._stop
        self._pause_control = CapturePause()
        pause_control = self._pause_control
        self._download_outcomes = {}
        prefer_identity = self.prefer_identity.get_active()
        diagnose_origins = self.diagnose_origins.get_active()
        self.diagnose_origins.set_active(False)
        self.status.set_label("Navigateur HTTP privé en cours, sans export…")

        def progress(metrics):
            # Worker passes only numeric counts. Late callbacks cannot restore
            # another session after discard, close or a new capture.
            def deliver():
                if not self._closed and self._generation == generation:
                    self._show_coverage(metrics)
                return False
            self.GLib.idle_add(deliver)

        def work(_control):
            kwargs = {
                "pause_control": pause_control,
                "metadata_only_origins": metadata_only_origins,
                "download_destination": self._choose_download_from_worker,
                "status": self._download_status,
                "coverage_callback": progress,
            }
            if diagnose_origins:
                kwargs["diagnose_origins"] = True
            if prefer_identity:
                kwargs["prefer_identity"] = True
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
                    self._show_coverage(result.coverage())
                    self._show_origin_diagnostics(result)
                    try:
                        self._review = IdentifierReview(result)
                    except Exception:
                        # Identifier heuristics may reject an otherwise valid
                        # encrypted capture (e.g. candidate limit). Never
                        # destroy a vaulted trace just because the *filtered*
                        # ZIP cannot be produced: the operator explicitly
                        # requested a separate direct LLM export path.
                        # Do not surface provider exceptions or original URLs.
                        if getattr(result, "vault_session_id", None):
                            self.tabs.set_current_page(3)
                            self.status.set_label(
                                "Revue filtrée impossible : capture conservée "
                                "dans le coffre chiffré. Vous pouvez exporter "
                                "directement cette session privée pour un LLM "
                                "avec la phrase secrète du coffre. Le ZIP sera "
                                "en clair et non anonymisé."
                            )
                        else:
                            result.destroy()
                            self._trace = None
                            self.status.set_label(
                                "Limite de revue atteinte : capture effacée, aucun export."
                            )
                        self._emit_failure("http_review", "review_failed")
                    else:
                        self._show_candidates()
                        self.tabs.set_current_page(3)
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
                observed = self._download_outcomes.get("download_started", 0)
                if observed:
                    saved = self._download_outcomes.get("download_saved_by_user", 0)
                    skipped = self._download_outcomes.get("download_cancelled", 0)
                    failed = self._download_outcomes.get("download_failed", 0)
                    self.status.set_label(
                        self.status.get_label()
                        + f" · Téléchargements : {observed} observés, "
                        + f"{saved} enregistrés explicitement, "
                        + f"{skipped} annulés, {failed} en échec. "
                        + "Aucun binaire dans le ZIP."
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
        secret = self._vault_password()
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
                if self._index_handle is done:
                    self._index_handle = None
                if self._closed or generation != self._generation:
                    return False
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
        passphrase = self._vault_password()
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
                if self._index_handle is done:
                    self._index_handle = None
                if self._closed or generation != self._generation:
                    return False
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
                if self._index_handle is done:
                    self._index_handle = None
                if self._closed or generation != self._generation:
                    return False
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
        self._invalidate_retention()
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
        self._refresh()

    def unlock_sessions(self):
        """Build the sensitive catalogue only on explicit local unlock.

        Scrypt scans happen on a Core task worker, never on the GTK thread.
        Session summaries are neither persisted nor sent to diagnostics.
        """
        if (self.vault_directory is None or self._closed
                or self.is_recording() or self.is_scanning()):
            return
        passphrase = self._vault_password()
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
                if self._index_handle is done:
                    self._index_handle = None
                if self._closed or generation != self._generation:
                    return False
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
                    # First opening of an empty vault is permitted. For a
                    # nonempty vault, the master phrase must authenticate at
                    # least one existing session before being retained.
                    if ids and not entries:
                        self.status.set_label(
                            "Ouverture refusée : phrase secrète incorrecte "
                            "ou toutes les sessions sont indisponibles."
                        )
                        self._refresh()
                        return False
                    if self._vault_secret is None:
                        try:
                            self._vault_secret = bytearray(
                                passphrase.encode("utf-8")
                            )
                        except UnicodeError:
                            self.status.set_label("Phrase secrète invalide.")
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
                        f"Coffre ouvert · {len(entries)} sessions déverrouillées et "
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
        secret = self._vault_password()
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
                if self._index_handle is done:
                    self._index_handle = None
                if self._closed or self._generation != generation:
                    return False
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
        passphrase = self._vault_password()
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
        self._show_coverage(stored.trace.coverage())
        try:
            self._review = IdentifierReview(stored.trace)
        except Exception:
            self._discard_private()
            self.status.set_label(
                "Archive conservée dans le coffre, revue impossible en mémoire."
            )
            return
        self._show_candidates(update_status=False)
        self.tabs.set_current_page(3)
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
        passphrase = self._vault_password()
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

    def _prompt_private_zip_file(self, callback, cancelled):
        """Single native Save As approval; never requires a 0700 folder."""
        if self._closed or self._private_destination_dialog is not None:
            return
        Gtk = self.Gtk
        if not hasattr(Gtk, "FileChooserNative"):
            self.status.set_label("Dialogue d'enregistrement indisponible.")
            cancelled(False)
            return
        root = self.widget.get_root()
        parent = root if isinstance(root, Gtk.Window) else None
        chooser = Gtk.FileChooserNative.new(
            "Exporter la session privée pour LLM (ZIP en clair)", parent,
            Gtk.FileChooserAction.SAVE, "Enregistrer", "Annuler",
        )
        chooser.set_current_name("cifereca-session-privee.zip")
        self._private_destination_dialog = chooser
        self._refresh()

        def responded(dialog, response):
            if dialog is not self._private_destination_dialog:
                dialog.destroy()
                return
            self._private_destination_dialog = None
            try:
                if response == Gtk.ResponseType.ACCEPT and not self._closed:
                    selected = dialog.get_file()
                    filename = selected.get_path() if selected else None
                    if filename:
                        callback(Path(filename))
                    else:
                        cancelled(False)
                elif not self._closed:
                    cancelled(True)
            finally:
                dialog.destroy()
                self._refresh()

        chooser.connect("response", responded)
        chooser.show()

    def export_private_zip(self, *, current=False):
        """Export chosen session or active review, without forced list refresh.

        The active-session path is intentionally independent of the selected
        catalogue row; a later selection must not redirect its export.
        """
        if (self.vault_directory is None or self._closed
                or self.is_recording() or self.is_scanning()
                or self._private_destination_dialog is not None):
            return
        started = monotonic()
        sid = (
            getattr(self._trace, "vault_session_id", None) if current
            else self.session_list.get_active_id()
        )
        passphrase = self._vault_password()
        if not sid:
            self.status.set_label(
                "Aucune session sélectionnée : choisissez une session dans Sessions et export privé."
            )
            return
        if len(passphrase) < 12:
            self.status.set_label(
                "Coffre fermé : cliquez Ouvrir le coffre avec votre phrase secrète, puis réessayez l’export."
            )
            return
        generation = self._generation

        def cancelled(user_cancelled):
            if self._closed:
                return
            if user_cancelled:
                self.status.set_label("Export privé annulé ; aucun ZIP créé.")
                self._emit_private_export("private_zip_export_cancelled", started)
            else:
                self.status.set_label("Destination ZIP indisponible ; aucun fichier créé.")
                self._emit_private_export("private_zip_export_failed", started)

        def selected(destination):
            active_sid = (
                getattr(self._trace, "vault_session_id", None) if current
                else self.session_list.get_active_id()
            )
            if (self._closed or self._generation != generation
                    or active_sid != sid):
                cancelled(True)
                return
            stored = None
            try:
                stored = PrivateSessionVault(self.vault_directory).open(
                    sid, passphrase,
                )
                size = save_session_llm_zip(
                    stored, Path(destination), confirmed=True,
                )
            except Exception as error:
                # Never include chosen pathname or decrypted provider text.
                self.status.set_label(_private_export_failure_message(error))
                self._emit_private_export("private_zip_export_failed", started)
                return
            finally:
                if stored is not None:
                    stored.trace.destroy()
            self._emit_private_export("private_zip_exported", started, size)
            # Do not put a sensitive local path in diagnostics.
            self.status.set_label(
                "Session exportée en ZIP privé NON CHIFFRÉ (fichier 0600). "
                "Aucun envoi automatique. Conservez ou supprimez ce ZIP "
                "après l'avoir transmis volontairement à votre LLM."
            )

        self._prompt_private_zip_file(selected, cancelled)

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
            self._pending_cursor = target * _REVIEW_PAGE_SIZE - 1
            self._show_candidates(update_status=False)

    def go_to_next_pending(self):
        """Navigate pending identifiers without applying any decisions in bulk."""
        if (self._closed or self._review is None or self.is_recording()
                or self.is_scanning()):
            return
        rows = self._review.local_rows()
        if not rows:
            return
        start = max(self._candidate_page * _REVIEW_PAGE_SIZE, self._pending_cursor + 1)
        pending = [
            i for i, row in enumerate(rows) if row["decision"] is None
        ]
        if not pending:
            self.status.set_label("Toutes les décisions individuelles sont prises.")
            return
        target = next((i for i in pending if i >= start), pending[0])
        self._candidate_page = target // _REVIEW_PAGE_SIZE
        self._pending_cursor = target
        self._show_candidates(update_status=False, focus_index=target)
        self.status.set_label(
            f"Identifiant {target + 1}/{len(rows)} encore à décider "
            f"(page {self._candidate_page + 1}). "
            "Aucune décision automatique."
        )

    def _show_candidates(self, *, update_status=True, focus_index=None):
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
            f"Identifiants {first + 1}–{last} sur {total} "
            f"· {self._review.pending()} à décider"
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
        for row_index, row in enumerate(rows[first:last], first):
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            # Candidate values are shown only to the local operator.
            label = Gtk.Label(
                label=f"{row['key']} · {row['kind']} · {row['occurrences']}× : "
                      f"{row['value']}\n"
                      "Confiance non évaluée : suggestion heuristique, "
                      "sensibilité à vérifier.\n"
                      "Provenance locale : " + ", ".join(row["fields"]),
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
                self.candidate_page_status.set_label(
                    f"Identifiants {first + 1}–{last} sur {total} "
                    f"· {current.pending()} à décider"
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
            if row_index == focus_index and hasattr(chooser, "grab_focus"):
                # Focus causes GTK's containing ScrolledWindow to reveal the
                # choice. Defer until the replacement page is laid out.
                captured_review = self._review

                def focus_choice(widget=chooser, current=captured_review):
                    if not self._closed and self._review is current:
                        widget.grab_focus()
                    return False

                self.GLib.idle_add(focus_choice)
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
        coverage = preview["body_coverage"]
        empty_warning = (
            "\nATTENTION — ZIP sans aucun corps HTTP : impossible de "
            "reconstituer les réponses."
            if coverage["exported_structural_bodies"] == 0 else ""
        )
        self.bundle_summary.set_label(
            f"ZIP en mémoire : {len(preview['members'])} membres, "
            f"{preview['bytes']} octets, SHA-256 : {preview['sha256']}. "
            "Sélectionner chaque membre pour inspecter son texte EXACT.\n"
            f"Corps textuels retenus à la capture : {coverage['captured_text_bodies']} ; "
            f"structures exportées : {coverage['exported_structural_bodies']} ; "
            f"non retenus dans la trace : {coverage['not_retained_in_trace']} ; "
            f"exclus par la revue : {coverage['excluded_by_review']} ; "
            f"omis à la projection : {coverage['omitted_during_projection']} ; "
            f"routes masquées : {coverage['masked_exchange_routes']}"
            f"/{coverage['exported_exchange_count']}."
            + empty_warning
        )
        self._populate_zip_members()
        self.status.set_label(
            "ZIP préparé en mémoire uniquement. Relire les membres, "
            "cocher la confirmation puis exporter volontairement."
        )
        self._refresh()

    def _populate_zip_members(self):
        """Filter only approved archive member names; never search raw bytes."""
        if self._bundle_preview is None or self._closed:
            return
        previous = self.bundle_member.get_active_id()
        query = self.member_filter.get_text().strip().casefold()
        names = [member["name"] for member in self._bundle_preview["members"]]
        preferred = (
            "manifest.json", "coverage-report.json", "timeline.json",
            "identifiers.json", "README.md", "LLM_INSTRUCTIONS.md",
        )
        ranked = sorted(names, key=lambda name: (
            preferred.index(name) if name in preferred else len(preferred),
            name,
        ))
        visible = [name for name in ranked if query in name.casefold()]
        self.bundle_member.remove_all()
        for name in visible:
            self.bundle_member.append(name, name)
        if visible:
            self.bundle_member.set_active_id(
                previous if previous in visible else visible[0],
            )
        else:
            self.bundle_text.get_buffer().set_text("")
        self.bundle_progress.set_label(
            f"{len(visible)} membre(s) correspondant sur {len(names)}. "
            "Recherche locale dans les noms uniquement."
            if query else
            f"{len(names)} membres disponibles ; premier aperçu : manifest.json."
        )

    def _select_coverage_report(self):
        if self._closed or self._bundle_preview is None:
            return
        # Clearing a filter must never approve the archive or change choices.
        self.member_filter.set_text("")
        if hasattr(self.member_filter, "get_text"):
            self._populate_zip_members()
        self.bundle_member.set_active_id("coverage-report.json")

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
            "Enregistrer le ZIP revu (données sensibles possibles)", parent,
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

    def open_export_folder(self):
        """Open only the directory of a ZIP saved by this panel, on a click."""
        if self._closed or self._last_export_path is None:
            return
        try:
            # The only destination is the local file:// URI of the parent.
            # A file-manager launch is never implied by export or preview.
            from gi.repository import Gio
            Gio.AppInfo.launch_default_for_uri(
                self._last_export_path.parent.as_uri(), None,
            )
        except Exception:
            # Never put a chosen private pathname into a diagnostic or log.
            self.status.set_label(
                "Impossible d'ouvrir le dossier automatiquement ; "
                "utilisez le chemin affiché après l'enregistrement."
            )

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
            self._last_export_path = Path(path)
            # This GTK label is private UI only: never send the pathname
            # through Diagnostics, callbacks, telemetry or logs.
            self.status.set_label(
                "ZIP revu/filtré enregistré (mode privé 0600) : "
                + str(path) + " — aucun téléversement."
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
        self._retention_plan = None
        self._last_export_path = None
        self.status.set_label("Page Browser Recorder fermée.")
        self.open_export_folder_button.set_sensitive(False)
        if self._download_choice_done is not None:
            self._cancel_download_dialog(self._download_choice_done)
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
        self._wipe_vault_password()
        self._discard_private()
        self._stop = None
        self._handle = None
        self._index_handle = None
