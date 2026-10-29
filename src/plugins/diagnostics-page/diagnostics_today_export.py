"""#3649 — private daily journal ZIP export from the Diagnostics GTK page.

No raw journal bytes traverse the GUI broker boundary. The Core Diagnostics
broker reads the physical canonical journal, filters the local day and writes
an exclusive ZIP. This controller only chooses a destination and displays
the outcome. No upload, provider traffic or additional journal reads.
"""
from __future__ import annotations

from datetime import datetime


class DailyJournalExport:
    LABEL = "Exporter le journal brut du jour (ZIP)…"
    CONFIRM = "Confirmer l’export ZIP non masqué"

    def __init__(self, button, *, broker, tasks, host, current, folder,
                 gtk=None, gio=None, glib=None):
        if gtk is None:
            from gi.repository import Gtk, Gio, GLib
            gtk, gio, glib = Gtk, Gio, GLib
        self.gtk, self.gio, self.glib = gtk, gio, glib
        self.button, self.broker, self.tasks, self.host = button, broker, tasks, host
        self.current, self.folder = current, folder
        self.closed = self.armed = False
        self.chooser = self.handle = None
        button.connect("clicked", self.clicked)
        self.refresh()

    def active(self):
        return (not self.closed and self.current()
                and callable(getattr(self.broker, "export_today_journal_zip", None)))

    def refresh(self):
        active = self.active()
        self.button.set_sensitive(active)
        self.button.set_tooltip_text(
            "Export ZIP du jour local, sans filtre d'écran : contient des "
            "données privées et éventuellement des credentials. "
            "Aucun envoi automatique. Fichier créé avec mode 0600."
            if active else "Core incompatible : export ZIP du jour indisponible."
        )
        return active

    def clicked(self, button):
        if not self.active():
            self.refresh()
            return
        if self.handle is not None or self.chooser is not None:
            self.host.toast("Un export ZIP du journal est déjà en cours")
            return
        if not self.armed:
            self.armed = True
            button.set_label(self.CONFIRM)
            self.host.toast(
                "Le ZIP inclut tous les événements du jour, sans masquage : "
                "logins, mots de passe, cookies, tokens, URLs signées et "
                "informations personnelles peuvent y figurer. "
                "Ne partagez pas ce fichier sans vérification. "
                "Cliquez de nouveau pour choisir un emplacement local."
            )
            return
        self.armed = False
        button.set_label(self.LABEL)
        root = button.get_root()
        parent = root if isinstance(root, self.gtk.Window) else None
        chooser = self.gtk.FileChooserNative.new(
            self.LABEL, parent, self.gtk.FileChooserAction.SAVE,
            "Exporter", "Annuler",
        )
        chooser.set_modal(parent is not None)
        folder = self.folder()
        if folder is not None:
            try:
                chooser.set_current_folder(self.gio.File.new_for_path(folder))
            except Exception:
                pass
        chooser.set_current_name(
            "cifereca-journal-brut-" + datetime.now().strftime("%Y%m%d-%H%M%S")
            + ".zip"
        )
        self.chooser = chooser
        chooser.connect("response", self.chose_file)
        chooser.show()

    def chose_file(self, dialog, response):
        try:
            if (self.closed or not self.current() or dialog is not self.chooser
                    or response != self.gtk.ResponseType.ACCEPT):
                return
            output = dialog.get_file()
            destination = output.get_path() if output is not None else None
            if not destination:
                self.host.toast("Sélectionnez un fichier ZIP sur un disque local")
                return
            if not destination.lower().endswith(".zip"):
                self.host.toast("L'export doit porter l'extension .zip")
                return
            if self.handle is not None:
                self.host.toast("Un export est déjà en cours")
                return

            def write(control):
                if control.cancelled() or self.closed or not self.current():
                    return False
                exported = self.broker.export_today_journal_zip(
                    destination, cancelled=control.cancelled,
                )
                return exported == destination and not control.cancelled()

            try:
                self.handle = self.tasks.submit(
                    write, label="Export ZIP journal du jour", kind="diagnostics",
                )
            except Exception:
                self.host.toast("Impossible de lancer l'export ZIP")
                return
            self.handle.add_done_callback(self.completed)
        finally:
            if self.chooser is dialog:
                self.chooser = None
            dialog.destroy()

    def completed(self, handle):
        try:
            okay = handle.result() is True
            error = None
        except FileExistsError:
            okay, error = False, "Le fichier existe déjà : choisissez un nouveau nom"
        except PermissionError:
            okay, error = False, "Accès refusé au dossier choisi"
        except InterruptedError:
            okay, error = False, "Export ZIP annulé, aucun fichier publié"
        except ValueError as exc:
            labels = {
                "journal_timezone_unavailable": "Fuseau local non résolu : export refusé",
                "journal_export_limit": "Journal trop volumineux pour l'export ZIP",
                "journal_zip_suffix_required": "Le fichier doit se terminer par .zip",
                "journal_export_directory_invalid": "Dossier de destination invalide",
            }
            okay, error = False, labels.get(str(exc), "Export ZIP invalide")
        except OSError:
            okay, error = False, "Erreur disque ou espace insuffisant"
        except Exception:
            okay, error = False, "Erreur inattendue lors de l'export ZIP"
        if self.handle is handle:
            self.handle = None

        def show_status():
            if not self.closed and self.current():
                self.host.toast(
                    "Journal brut du jour exporté en ZIP (fichier local privé)"
                    if okay else error or "Export ZIP annulé"
                )
            return False

        self.glib.idle_add(show_status)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.armed = False
        if self.chooser is not None:
            self.chooser.destroy()
            self.chooser = None
        if self.handle is not None:
            self.handle.cancel()
            self.handle = None
