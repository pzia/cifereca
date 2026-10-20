"""Explicit GTK review for PRIVATE HTTP capture; no ZIP or raw HAR export.

The legacy metadata-only recorder remains separate. Browser HTTP bytes stay
in memory, and only the user can decide how to treat each suggested value.
"""
from __future__ import annotations

import json
from threading import Event

from browser_recorder import RecorderError, site_origin
from browser_recorder_http import record_http
from browser_recorder_review import IdentifierReview


class PrivateReviewPanel:
    def __init__(self, Gtk, GLib, tasks, site, consent, legacy_running):
        self.Gtk, self.GLib, self.tasks = Gtk, GLib, tasks
        self.site, self.consent = site, consent
        self.legacy_running = legacy_running
        self._handle = None
        self._stop = None
        self._trace = None
        self._review = None
        self._closed = False
        self._generation = 0

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.widget = box
        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        box.append(Gtk.Label(
            label="Parcours HTTP privé (P1) — revue locale, aucun export",
            xalign=0, wrap=True,
        ))
        box.append(Gtk.Label(
            label="Capture facultative des échanges textuels sur le site HTTPS choisi. "
                  "Les valeurs peuvent contenir des données privées et restent en mémoire. "
                  "Choisissez pour chaque identifiant ; aucun ZIP n'est produit.",
            xalign=0, wrap=True,
        ))
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.start = Gtk.Button(label="Démarrer HTTP privé")
        self.stop = Gtk.Button(label="Arrêter / revoir")
        self.preview_button = Gtk.Button(label="Prévisualiser les choix")
        self.clear = Gtk.Button(label="Effacer les données privées")
        for button in (self.start, self.stop, self.preview_button, self.clear):
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

        self.start.connect("clicked", lambda *_: self.begin())
        self.stop.connect("clicked", lambda *_: self.stop_capture())
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
        self.preview_button.set_sensitive(
            not self._closed and not busy and self._review is not None
        )
        self.clear.set_sensitive(
            not self._closed and not busy and self._trace is not None
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

    def _discard_private(self):
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
            site_origin(start_url)
        except RecorderError:
            self.status.set_label("Origine HTTPS publique valide requise.")
            return
        self._discard_private()
        self._generation += 1
        generation = self._generation
        self._stop = Event()
        stop = self._stop
        self.status.set_label("Navigateur HTTP privé en cours, sans export…")

        def work(_control):
            return record_http(start_url, stop)

        try:
            handle = self.tasks.submit(
                work, kind="browser-recorder", label="Parcours HTTP privé",
            )
        except Exception:
            self._stop = None
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

    def stop_capture(self):
        if self._stop is not None:
            self._stop.set()
            self.status.set_label("Arrêt demandé ; préparation de la revue…")

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
