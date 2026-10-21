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

    def __init__(self, *, Gtk, Adw, Pango, parent, row, title, alive, inspector=None, inspection_row=None):
        self.Gtk, self.Adw, self.Pango = Gtk, Adw, Pango
        self.parent, self.row, self.title, self.alive = parent, row, title, alive
        self.button = self.dialog = None
        self.text = ""
        self.inspector = inspector
        if inspector is not None:
            inspector.attach_activity_row(inspection_row if inspection_row is not None else row)

    def clear(self):
        if self.inspector is not None:
            self.inspector.clear()
        self.text = ""
        if self.dialog is not None:
            self.dialog.destroy()
            self.dialog = None
        if self.button is not None:
            self.button.set_visible(False)

    def restore_text(self, text: str) -> None:
        """Reattach a private report to rebuilt widgets; never restart audit."""
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

    def _open_large_results(self):
        """Local reconciliation results are an exploration window, not consent.

        Keep the old MessageDialog for non-inspector Maintenance tools and
        older libadwaita runtimes; only the reconciliation explainer gets a
        resizable titlebar and a flexible scroll region (#2712).
        """
        Gtk, Adw = self.Gtk, self.Adw
        window = Adw.Window(transient_for=self.parent, modal=False,
                            title=self.title)
        window.set_default_size(860, 650)
        window.set_decorated(True)
        window.set_resizable(True)

        top = Adw.ToolbarView()
        heading = Adw.HeaderBar()
        heading.set_title_widget(Gtk.Label(label=self.title))
        maximize = Gtk.Button(label="Agrandir / Restaurer")
        maximize.set_tooltip_text(
            "Agrandir ou restaurer la fenêtre des résultats de réconciliation"
        )
        maximize.connect('clicked', lambda *_: (
            window.unmaximize() if window.is_maximized()
            else window.maximize()
        ))
        heading.pack_end(maximize)
        top.add_top_bar(heading)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for edge in ('top', 'bottom', 'start', 'end'):
            getattr(body, 'set_margin_'+edge)(12)
        label = Gtk.Label(label=self.text, xalign=0, yalign=0)
        label.set_wrap(True)
        label.set_wrap_mode(self.Pango.WrapMode.WORD_CHAR)
        label.set_selectable(True)
        label.set_max_width_chars(100)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(100)
        scroll.set_vexpand(True)
        scroll.set_child(label)
        body.append(scroll)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        examine = Gtk.Button(label="Examiner les cas…")
        examine.set_tooltip_text(
            "Ouvrir uniquement les preuves locales privées dans une nouvelle session"
        )
        close = Gtk.Button(label="Fermer")
        controls.append(examine)
        controls.append(close)
        body.append(controls)
        top.set_content(body)
        window.set_content(top)

        def open_inspector(*_args):
            if self.dialog is not window:
                return
            self.dialog = None
            window.close()
            if self.inspector is not None and self.alive():
                self.inspector.start()

        examine.connect('clicked', open_inspector)
        close.connect('clicked', lambda *_: window.close())
        window.connect('close-request', lambda *_: self._closed(window, "close"))
        self.dialog = window
        window.present()

    def open(self, _button):
        if not self.text or not self.alive():
            self.clear()
            return
        if self.dialog is not None:
            self.dialog.present()
            return
        if (self.inspector is not None
                and getattr(self.Adw, 'ToolbarView', None) is not None
                and getattr(self.Adw, 'HeaderBar', None) is not None):
            self._open_large_results()
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
        if self.inspector is not None:
            dialog.add_response("inspect", "Examiner les cas…")
        dialog.connect("response", lambda _dialog, response: self._closed(dialog, response))
        self.dialog = dialog
        dialog.present()

    def _closed(self, dialog, response):
        if self.dialog is dialog:
            self.dialog = None
            if response == "inspect" and self.inspector is not None and self.alive():
                self.inspector.start()
