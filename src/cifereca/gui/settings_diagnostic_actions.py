"""Settings Diagnostics actions with explicit page and toolkit dependencies.

The window owns the GTK widgets and signal connections. This factory creates
callbacks only: it neither collects diagnostics nor registers signals or writes
preferences until a live page invokes the corresponding action. Late clipboard
and file chooser replies retain the originating page's lifetime guard.
"""
from __future__ import annotations

from pathlib import Path

from cifereca.plugin_api import prefer_downloads_for_save


def build_settings_diagnostic_callbacks(
    self, *, page_lifetime, refresh_text, diag_text, level_drop,
    diag_switch, levels, Gtk, Gdk,
):
    def copy_text(_button=None) -> None:
        """Copie le diagnostic via un Gdk.ContentProvider GTK4 natif.

            Les bindings PyGObject de certaines distributions n'exposent pas
            ``Gdk.Clipboard.set_text``. ``set_content`` avec un ContentProvider
            fonctionne sur GTK4/Wayland/X11 et remplace correctement un ancien
            contenu du presse-papiers.
            """
        if not page_lifetime.alive:
            return
        refresh_text()
        buffer = diag_text.get_buffer()
        start, end = buffer.get_bounds()
        content = buffer.get_text(start, end, True)
        display = Gdk.Display.get_default()
        if not display:
            self.show_error("Aucun presse-papiers graphique disponible", "Copie impossible")
            return
        clipboard = display.get_clipboard()
        try:
            provider = Gdk.ContentProvider.new_for_value(content)
            # Garder une référence côté fenêtre est volontaire : le provider
            # doit rester vivant tant que notre application possède le clipboard.
            self._clipboard_provider = provider
            if not clipboard.set_content(provider):
                raise RuntimeError("GDK a refusé la prise de possession du presse-papiers")
        except Exception:
            self.diagnostics.add(
                "UI", "clipboard write", "error",
                detail={"code": "clipboard_write_failed"},
                level="detailed",
            )
            self.show_error("Impossible d'écrire dans le presse-papiers", "Copie impossible")
            return

        def verified(source, result, *_args) -> None:
            # Async clipboard verification may arrive after the Settings
            # view was rebuilt, or after its window was closed.
            if not page_lifetime.alive:
                return
            try:
                copied = source.read_text_finish(result)
            except Exception:
                self.diagnostics.add(
                    "UI", "clipboard verify", "error",
                    detail={"code": "clipboard_verify_failed"},
                    level="detailed",
                )
                # L'écriture GDK a réussi ; seule la relecture n'est pas disponible.
                self.toast("Diagnostic copié dans le presse-papiers")
                return
            if copied == content:
                self.diagnostics.add("UI", "clipboard copy", "ok", detail={"verified": True}, level="detailed")
                self.toast("Diagnostic copié dans le presse-papiers")
            else:
                self.diagnostics.add(
                    "UI", "clipboard verify", "error",
                    detail={"message": "clipboard content mismatch"},
                    level="detailed",
                )
                self.toast("Le presse-papiers n'a pas pu être vérifié", 5)

        try:
            clipboard.read_text_async(None, verified)
        except Exception:
            self.diagnostics.add(
                "UI", "clipboard verify", "error",
                detail={"code": "clipboard_verify_failed"},
                level="detailed",
            )
            self.toast("Diagnostic copié dans le presse-papiers")


    def export_text(_button=None) -> None:
        """Enregistrer le rapport expurgé visible dans Diagnostic."""
        if not page_lifetime.alive:
            return
        refresh_text()
        buffer = diag_text.get_buffer()
        start, end = buffer.get_bounds()
        content = buffer.get_text(start, end, True)
        chooser = Gtk.FileChooserNative.new(
            "Exporter le diagnostic",
            self,
            Gtk.FileChooserAction.SAVE,
            "Exporter",
            "Annuler",
        )
        prefer_downloads_for_save(chooser)
        chooser.set_current_name("cifereca-diagnostic.txt")

        def chosen(dialog, response) -> None:
            try:
                # A native chooser can respond after its originating page
                # was disposed. Do not export or show a stale success/error.
                if not page_lifetime.alive:
                    return
                if response != Gtk.ResponseType.ACCEPT:
                    return
                selected = dialog.get_file()
                path = selected.get_path() if selected is not None else None
                if not path:
                    raise RuntimeError("destination locale indisponible")
                Path(path).write_text(content, encoding="utf-8")
                self.diagnostics.add(
                    "UI", "diagnostic export", "ok",
                    detail={"code": "export_completed"},
                    level="detailed",
                )
                self.toast("Diagnostic exporté")
            except Exception:
                self.diagnostics.add(
                    "UI", "diagnostic export", "error",
                    detail={"code": "diagnostic_export_failed"},
                    level="detailed",
                )
                self.show_error("Impossible d’exporter le diagnostic", "Export impossible")
            finally:
                dialog.destroy()

        chooser.connect("response", chosen)
        chooser.show()


    def persist_diagnostics(*_args):
        if not page_lifetime.alive:
            return
        selected = int(level_drop.get_selected())
        if not 0 <= selected < len(levels):
            return
        try:
            self.service.save_diagnostic_preferences(enabled=True, level=levels[selected])
            refresh_text()
        except Exception:
            self.show_error("Impossible d’enregistrer le niveau de diagnostic")


    def clear_diagnostics(_button=None) -> None:
        # Clearing diagnostics is a Core mutation. A detached Settings
        # button must never perform it after the page was replaced.
        if not page_lifetime.alive or getattr(self, "_closed", False):
            return
        self.diagnostics.clear()
        refresh_text()

    return copy_text, export_text, persist_diagnostics, clear_diagnostics
