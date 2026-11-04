"""P1 #2854: edit declared reader formats without USB I/O or transfer rights.

The selected profile, device, UUID and live binding are rechecked before
saving. A format declaration is not hardware evidence or permission to copy.
"""
from __future__ import annotations

from .reader_pairing_actions import _identity_scope, _still_current


_FORMATS = (
    ("epub", "EPUB"),
    ("kepub", "KEPUB"),
    ("fxl.kepub", "FXL-KEPUB"),
    ("pdf", "PDF"),
)


def request_reader_formats(window, *, Gtk, Adw=None) -> None:
    """Open an explicit, revocable, device-scoped format declaration dialog."""
    scope = _identity_scope(window)
    if scope is None:
        return
    identity_id, storage = scope[:2]
    device_id = getattr(window, "_reader_selected_device_id", None)
    generation = getattr(window, "_reader_selection_generation", None)
    if type(device_id) is not str or not device_id:
        return

    try:
        from ..core.device_usb_reader_formats import declared_usb_reader_formats

        binding = storage.device_binding_get(device_id)
        if (type(binding) is not dict
                or binding.get("identity_id") != identity_id
                or binding.get("device_id") != device_id
                or type(binding.get("metadata")) is not dict):
            raise ValueError("reader_scope_changed")
        metadata = binding["metadata"]
        fingerprint = metadata.get("usb_fingerprint")
        if type(fingerprint) is not str:
            raise ValueError("reader_volume_unknown")
        declaration = declared_usb_reader_formats(
            identity_id=identity_id, device_id=device_id,
            usb_fingerprint=fingerprint, binding=binding,
        )
        # A corrupt legacy declaration must not silently become an empty one.
        if "reader_formats" in metadata and declaration is None:
            raise ValueError("reader_formats_corrupt")
        selected = set(declaration.supported_formats) if declaration else set()
    except (ValueError, TypeError, KeyError, OSError, AttributeError):
        if _still_current(window, scope):
            window.show_error(
                "Formats indisponibles : vérifiez la liseuse et son volume.",
                "Liseuses",
            )
        return

    if Adw is None:
        from gi.repository import Adw as AdwRuntime
        Adw = AdwRuntime

    nonce = object()
    window._reader_formats_nonce = nonce
    dialog = Adw.MessageDialog.new(
        window, "Formats de la liseuse",
        "Cochez uniquement les formats que vous déclarez compatibles avec "
        "cet appareil et ce volume USB. Ils ne sont pas vérifiés : aucun "
        "droit de copie, scan ni transfert n'est autorisé par ce réglage.",
    )
    fields = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    controls = {}
    for fmt, title in _FORMATS:
        checkbox = Gtk.CheckButton(label=title)
        checkbox.set_active(fmt in selected)
        fields.append(checkbox)
        controls[fmt] = checkbox
    dialog.set_extra_child(fields)
    dialog.add_response("cancel", "Annuler")
    dialog.add_response("confirm", "Enregistrer")
    dialog.set_close_response("cancel")
    window._reader_formats_dialog = dialog

    def current() -> bool:
        return (
            _still_current(window, scope)
            and getattr(window, "_reader_formats_nonce", None) is nonce
            and getattr(window, "_reader_formats_dialog", None) is dialog
            and getattr(window, "_reader_selected_device_id", None) == device_id
            and getattr(window, "_reader_selection_generation", None) == generation
        )

    def done(_dialog, response):
        try:
            if response != "confirm" or not current():
                return
            # A second dialog, a rebind, or an external metadata update may
            # not overwrite a newer declaration. Storage independently checks
            # the exact owner and UUID inside its SQLite transaction.
            live = storage.device_binding_get(device_id)
            if live != binding:
                raise ValueError("reader_binding_changed")
            formats = tuple(
                fmt for fmt, _title in _FORMATS if controls[fmt].get_active()
            )
            if not current():
                return
            storage.device_usb_reader_formats_set(
                identity_id=identity_id, device_id=device_id,
                usb_fingerprint=fingerprint, supported_formats=formats,
            )
            if current():
                window._render_readers()
        except (ValueError, TypeError, KeyError, OSError, AttributeError):
            if current():
                window.show_error(
                    "Formats non enregistrés : le profil, le volume ou la "
                    "configuration de la liseuse a changé.",
                    "Liseuses",
                )
        finally:
            if getattr(window, "_reader_formats_dialog", None) is dialog:
                window._reader_formats_dialog = None

    dialog.connect("response", done)
    dialog.present()


__all__ = ["request_reader_formats"]
