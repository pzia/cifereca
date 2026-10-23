"""Explicit, local support export and selection preservation; no broker I/O."""
from __future__ import annotations

from cifereca.plugin_api import prefer_downloads_for_save


def support_report_bytes(text):
    from cifereca_diagnostics_page import _redacted_text
    if type(text) is not str or len(text) > 1_000_000:
        raise ValueError("support_report_too_large")
    if len(text.encode("utf-8")) > 1_000_000:
        raise ValueError("support_report_too_large")
    safe = _redacted_text(text, max_chars=1_000_000, max_input_chars=1_000_000)
    data = safe.encode("utf-8")
    if len(data) > 1_000_000:
        raise ValueError("support_report_too_large")
    return data


def diagnostic_json_bytes(value):
    """Serialize a detail from the SAME bounded redacted renderer as GTK.

    The caller supplies a diagnostic value, not arbitrary pre-encoded bytes.
    This prevents the file action from bypassing the redaction boundary.
    """
    import json
    from cifereca_diagnostics_page import format_diagnostic_detail
    encoded = format_diagnostic_detail(value, "json").encode("utf-8")
    if len(encoded) > 24_000:
        raise ValueError("diagnostic_json_too_large")
    json.loads(encoded.decode("utf-8"))  # A JSON file is never a truncated fragment.
    return encoded


class SupportReportExport:
    def __init__(
        self, Gtk, Gio, button, parent, text, toast, *,
        filename="cifereca-diagnostic-support.txt",
        title="Enregistrer le rapport support expurgé",
        success_message="Rapport support expurgé enregistré",
        prepare_bytes=support_report_bytes,
    ):
        self.Gtk, self.Gio = Gtk, Gio
        self.button, self.parent, self.text, self.toast = button, parent, text, toast
        self.filename, self.title = filename, title
        self.success_message, self.prepare_bytes = success_message, prepare_bytes
        self.dialog = None
        self.cancellable = None
        self.closed = False
        self.data = None

    def choose(self, *_args):
        if self.closed or self.dialog is not None or self.cancellable is not None:
            return
        try:
            self.data = self.prepare_bytes(self.text())
            self.dialog = self.Gtk.FileChooserNative.new(
                self.title, self.parent(),
                self.Gtk.FileChooserAction.SAVE, "Enregistrer", "Annuler",
            )
            prefer_downloads_for_save(self.dialog)
            self.dialog.set_current_name(self.filename)
            self.dialog.connect("response", self.response)
            self.button.set_sensitive(False)
            self.dialog.show()
        except Exception:
            self._release()
            self.toast("Export impossible : rapport indisponible ou trop volumineux")

    def response(self, dialog, response):
        try:
            if self.closed or response != self.Gtk.ResponseType.ACCEPT:
                self._release()
                return
            destination = dialog.get_file()
            # A remote URI must not turn a support export into a network send.
            if destination is None or not destination.get_path():
                raise ValueError("local_destination_required")
            self.cancellable = self.Gio.Cancellable()
            dialog.destroy()
            self.dialog = None
            destination.replace_contents_async(
                self.data, None, False, self.Gio.FileCreateFlags.PRIVATE,
                self.cancellable, self.finished, None,
            )
        except Exception:
            self._release()
            if not self.closed:
                self.toast("Export impossible : choisir un fichier local accessible")

    def finished(self, destination, result, _data):
        try:
            ok, _etag = destination.replace_contents_finish(result)
            if not ok:
                raise OSError("write_failed")
        except Exception:
            message = "Export impossible : écriture non terminée"
        else:
            message = self.success_message
        self._release()
        if not self.closed:
            self.toast(message)

    def _release(self):
        if self.dialog is not None:
            self.dialog.destroy()
        self.dialog = None
        self.cancellable = None
        self.data = None
        if not self.closed:
            self.button.set_sensitive(True)

    def close(self):
        self.closed = True
        if self.cancellable is not None:
            self.cancellable.cancel()
        self._release()


def preserve_report_selection(GLib, expander, buffer):
    """GTK unmapping clears selection; restore its last range after collapse."""
    saved = [None]

    def remember(*_args):
        if expander.get_expanded():
            saved[0] = (tuple(it.get_offset() for it in buffer.get_selection_bounds())
                        if buffer.get_has_selection() else None)

    def clear(*_args):
        saved[0] = None

    def restore():
        if saved[0] is not None:
            start, end = saved[0]
            if end <= buffer.get_char_count():
                buffer.select_range(buffer.get_iter_at_offset(start), buffer.get_iter_at_offset(end))
        return False

    def changed(*_args):
        GLib.idle_add(restore)

    buffer.connect("mark-set", remember)
    buffer.connect("changed", clear)
    expander.connect("notify::expanded", changed)
