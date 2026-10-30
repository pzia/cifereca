"""Bounded local Maintenance results; opaque execution facts are never rendered."""
from __future__ import annotations


def result_text(result) -> str:
    """Only explicitly user-facing strings enter the local result window.

    Facts may contain private execution capabilities and are deliberately ignored.
    Limits also apply to malformed third-party results; no arbitrary str/repr.
    """
    def text(value):
        return (value[:2048] + ("… [texte abrégé]" if len(value) > 2048 else "")) if type(value) is str else ""

    def lines(value):
        if type(value) not in (tuple, list):
            return []
        selected = [text(item) for item in value[:64] if type(item) is str and item]
        if len(value) > 64:
            selected.append("Affichage limité aux 64 premières lignes.")
        return selected

    sections = [text(getattr(result, "summary", "")) or "Outil terminé."]
    details = lines(getattr(result, "details", ()))
    warnings = lines(getattr(result, "warnings", ()))
    # Reconciliation can report hundreds of counters on an incomplete scan.
    # At 1280/1366 px the result scroll is capped at 340 px: limitations
    # must not be hidden below those numbers (#2565). Only the fixed public
    # status marker controls order; never inspect execution facts or repr().
    if warnings and sections[0].startswith("Analyse incomplète."):
        sections.append("Avertissements et limites :\n" +
                        "\n".join("• " + line for line in warnings))
    if details:
        sections.append("Résultats :\n" + "\n".join("• " + line for line in details))
    if warnings and not sections[0].startswith("Analyse incomplète."):
        sections.append("Avertissements et limites :\n" +
                        "\n".join("• " + line for line in warnings))
    return "\n\n".join(sections)


class MaintenanceResultView:
    """GTK-thread-owned view, revocable independently from session report text."""

    def __init__(self, *, Gtk, Adw, Pango, parent, row, title, alive):
        self.Gtk, self.Adw, self.Pango = Gtk, Adw, Pango
        self.parent, self.row, self.title, self.alive = parent, row, title, alive
        self.button = self.dialog = None
        self.text = ""
        self.state = None

    def clear(self):
        self.text = ""
        self.state = None
        if self.dialog is not None:
            self.dialog.destroy()
            self.dialog = None
        if self.button is not None:
            self.button.set_visible(False)

    def restore_text(self, text: str) -> None:
        """Reattach the last read-only Maintenance report without restarting work."""
        if type(text) is not str or not text or not self.alive():
            return
        self.text = text
        if self.button is None:
            self.button = self.Gtk.Button(label="Résultats…")
            self.button.connect("clicked", self.open)
            self.row.add_suffix(self.button)
        self.button.set_visible(True)

    def publish(self, result):
        self.clear()
        self.restore_text(result_text(result))

    def open(self, _button):
        if not self.text or not self.alive():
            self.clear()
            return
        if self.dialog is not None:
            self.dialog.present()
            return
        dialog = self.Adw.MessageDialog.new(self.parent, self.title, "")
        label = self.Gtk.Label(label=self.text)
        label.set_wrap(True)
        label.set_wrap_mode(self.Pango.WrapMode.WORD_CHAR)
        label.set_xalign(0)
        label.set_yalign(0)
        label.set_selectable(True)
        label.set_max_width_chars(76)
        scroll = self.Gtk.ScrolledWindow()
        scroll.set_policy(self.Gtk.PolicyType.NEVER, self.Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(100)
        scroll.set_max_content_height(340)
        scroll.set_propagate_natural_height(True)
        scroll.set_child(label)
        dialog.set_extra_child(scroll)
        dialog.add_response("close", "Fermer")
        dialog.connect("response", lambda _dialog, response: self._closed(dialog, response))
        self.dialog = dialog
        dialog.present()

    def _closed(self, dialog, response):
        if self.dialog is dialog:
            self.dialog = None
