"""HTTP Trace private export controller, using only the public broker."""
from __future__ import annotations


class HTTPTraceExport:
    """Own a button, confirmation and native chooser for one page lifetime."""

    LABEL = "Exporter les échanges HTTP…"
    CONFIRM = "Confirmer l’export HTTP non masqué"

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
        self.timer = None
        button.connect("clicked", self.clicked)
        self.refresh()
        self.timer = glib.timeout_add(500, self.refresh)

    def active(self):
        if self.closed or not self.current():
            return False
        if not callable(getattr(self.broker, 'export_http_trace', None)):
            return False
        try:
            return self.broker.http_trace_status().get('active') is True
        except Exception:
            return False

    def refresh(self):
        if self.closed or not self.current():
            self.timer = None
            return False
        active = self.active()
        self.button.set_sensitive(active)
        self.button.set_tooltip_text(
            "Export local brut : mots de passe, cookies, tokens et URLs signées "
            "peuvent être présents. Ne pas partager sans vérification."
            if active else "Trace inactif ou Core incompatible : activez le niveau "
            "Trace pour exporter les échanges HTTP de cette session."
        )
        if not active:
            self.armed = False
            self.button.set_label(self.LABEL)
        return True

    def clicked(self, button):
        if not self.active():
            self.refresh()
            return
        if not self.armed:
            self.armed = True
            button.set_label(self.CONFIRM)
            self.host.toast(
                "L’archive HTTP brute peut contenir des credentials : mots de "
                "passe, cookies, tokens et URLs signées. Cliquez à nouveau pour "
                "choisir une destination locale."
            )
            return
        if self.chooser is not None:
            self.host.toast("Un sélecteur d’export HTTP est déjà ouvert")
            return
        self.armed = False
        button.set_label(self.LABEL)
        root = button.get_root()
        parent = root if isinstance(root, self.gtk.Window) else None
        chooser = self.gtk.FileChooserNative.new(
            self.LABEL, parent, self.gtk.FileChooserAction.SAVE, "Exporter", "Annuler",
        )
        chooser.set_modal(parent is not None)
        folder = self.folder()
        if folder is not None:
            try:
                chooser.set_current_folder(self.gio.File.new_for_path(folder))
            except Exception:
                pass
        chooser.set_current_name("cifereca-echanges-http.zip")
        self.chooser = chooser
        chooser.connect("response", self.chose_file)
        chooser.show()

    def chose_file(self, dialog, response):
        try:
            if (self.closed or not self.current() or dialog is not self.chooser
                    or response != self.gtk.ResponseType.ACCEPT):
                return
            if not self.active():
                self.host.toast("Export HTTP indisponible : Trace est désactivé")
                return
            output = dialog.get_file()
            destination = output.get_path() if output is not None else None
            if not destination:
                self.host.toast("Destination locale requise")
                return

            def write(control):
                if control.cancelled() or self.closed or not self.current():
                    return False
                self.broker.export_http_trace(destination)
                return True

            try:
                self.handle = self.tasks.submit(
                    write, label="Export local des échanges HTTP", kind="diagnostics",
                )
            except Exception:
                self.host.toast("Export HTTP indisponible")
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
            okay, error = False, "Ce fichier existe déjà : choisissez un nouveau nom"
        except Exception as exc:
            okay, error = False, f"Export HTTP impossible : {exc}"

        def show_status():
            if not self.closed and self.current():
                self.host.toast("Échanges HTTP exportés localement" if okay
                                else error or "Export HTTP annulé")
            return False

        self.glib.idle_add(show_status)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.armed = False
        if self.timer is not None:
            self.glib.source_remove(self.timer)
            self.timer = None
        if self.chooser is not None:
            self.chooser.destroy()
            self.chooser = None
        if self.handle is not None:
            self.handle.cancel()
            self.handle = None
