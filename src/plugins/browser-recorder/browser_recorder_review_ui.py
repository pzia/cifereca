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
                 export_directory=None):
        self.Gtk, self.GLib, self.tasks = Gtk, GLib, tasks
        self.site, self.consent = site, consent
        self.legacy_running = legacy_running
        self.export_directory = (Path(export_directory)
                                 if export_directory is not None else None)
        self._bundle_plan = None
        self._bundle_preview = None
        self._handle = None
        self._stop = None
        self._pause_control = None
        self._trace = None
        self._review = None
        self._closed = False
        self._generation = 0

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.widget = box
        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        box.append(Gtk.Label(
            label="Parcours HTTP privé (P1) — revue et ZIP volontaire",
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
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
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

        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(180)
        scroll.set_max_content_height(280)
        scroll.set_vexpand(False)
        self.candidates_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6,
        )
        scroll.set_child(self.candidates_box)
        box.append(scroll)

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
        self.export_confirm = Gtk.CheckButton(
            label="J'ai revu les membres accessibles ci-dessus et j'accepte "
                  "de créer localement cette archive pour un partage volontaire."
        )
        box.append(self.export_confirm)
        self.export_button = Gtk.Button(label="Exporter ce ZIP localement")
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
        self.start.set_sensitive(
            not self._closed and not busy and self._trace is None
            and not self.legacy_running()
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
        self._invalidate_bundle()
        self._text("")
        self._clear_candidates()
        if self._review is not None:
            self._review.destroy()
            self._review = None
        if self._trace is not None:
            self._trace.destroy()
            self._trace = None

    def begin(self):
        if self._closed or self.is_recording() or self.legacy_running():
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
        self._discard_private()
        self._generation += 1
        generation = self._generation
        self._stop = Event()
        stop = self._stop
        self._pause_control = CapturePause()
        pause_control = self._pause_control
        self.status.set_label("Navigateur HTTP privé en cours, sans export…")

        def work(_control):
            return record_http(
                start_url, stop, pause_control=pause_control,
                metadata_only_origins=metadata_only_origins,
            )

        try:
            handle = self.tasks.submit(
                work, kind="browser-recorder", label="Parcours HTTP privé",
            )
        except Exception:
            self._stop = None
            self._pause_control = None
            self.status.set_label("Impossible de démarrer le navigateur privé.")
            self._refresh()
            return
        self._handle = handle
        self._refresh()

        def finished(done):
            try:
                result, message = done.result(), None
            except RecorderError:
                result, message = None, "Capture interrompue ou indisponible."
            except Exception:
                result, message = None, "Navigateur indisponible."

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
                else:
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
                    else:
                        self._show_candidates()
                self._refresh()
                return False

            self.GLib.idle_add(deliver)

        handle.add_done_callback(finished)

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
        if self._stop is not None:
            self._stop.set()
            self.status.set_label("Arrêt demandé ; préparation de la revue…")
            self._refresh()

    def _show_candidates(self):
        self._clear_candidates()
        self._text("")
        rows = self._review.local_rows()
        self.status.set_label(
            f"{len(self._trace.exchanges)} échanges en mémoire ; "
            f"{len(rows)} identifiants à décider individuellement. "
            "Aucun export possible."
        )
        Gtk = self.Gtk
        if not rows:
            self.candidates_box.append(Gtk.Label(
                label="Aucun identifiant proposé : vérifier quand même les contenus.",
                xalign=0, wrap=True,
            ))
        for row in rows:
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
            chooser.set_active_id("choose")
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
            line.append(chooser)
            self.candidates_box.append(line)

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

    def export_zip(self):
        if (self._closed or self._bundle_plan is None
                or self.export_directory is None
                or not self.export_confirm.get_active()):
            self.status.set_label("Export interdit : confirmation manquante.")
            return
        try:
            filename = self._bundle_plan.save(
                self.export_directory, user_confirmed=True,
            )
        except Exception:
            self.status.set_label("Enregistrement local du ZIP impossible.")
            return
        # Do not allow repeated saves without another exact preview and
        # confirmation. Keep the trace in RAM only for further local review.
        self._invalidate_bundle()
        self.status.set_label(
            "ZIP revu et enregistré localement : " + filename
            + " — aucun envoi vers un service externe."
        )

    def discard(self):
        if self.is_recording():
            return
        self._discard_private()
        self.status.set_label("Données privées effacées de la revue.")
        self._refresh()

    def destroy(self):
        self._closed = True
        self._generation += 1
        if self._stop is not None:
            self._stop.set()
        self._discard_private()
        self._stop = None
        self._handle = None
