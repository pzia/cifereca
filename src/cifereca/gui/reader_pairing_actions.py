"""P1 #2854: explicit GTK declaration/activation of profile-scoped USB readers.

No hardware, network, provider or file I/O. Pairing accepts a user-supplied
block-volume UUID only as a declarative claim; the scanner independently
attests Linux mountinfo/sysfs/UUID before reporting any physical presence.
Activation is a separate deliberate control, never implied by pairing.
"""
from __future__ import annotations


def _identity_scope(window):
    service = getattr(window, "service", None)
    identity = getattr(getattr(service, "current_identity", None), "identity_id", None)
    storage = getattr(service, "storage", None)
    if (getattr(window, "_closed", False)
            or type(identity) is not str or not identity
            or identity != getattr(window, "_reader_scope_identity", None)
            or storage is None):
        return None
    return (identity, storage,
            getattr(window, "_identity_context_generation", None),
            getattr(window, "_account_context_generation", None),
            getattr(window, "_route_display_token", None))


def _still_current(window, scope) -> bool:
    return (
        _identity_scope(window) == scope
        and getattr(window, "_reader_scope_identity", None) == scope[0]
    )


def request_reader_pairing(window, *, Gtk, Adw=None) -> None:
    """A native explicit pairing form; no existing bindings are reparented."""
    scope = _identity_scope(window)
    if scope is None:
        return
    if Adw is None:
        from gi.repository import Adw as AdwRuntime
        Adw = AdwRuntime

    nonce = object()
    window._reader_pairing_nonce = nonce
    dialog = Adw.MessageDialog.new(
        window, "Associer une liseuse USB",
        "Saisissez le nom de la liseuse et l'UUID exact de son volume "
        "(consultable avec « lsblk -f »). Cette déclaration ne prouve aucune "
        "présence physique et n'active aucun examen.",
    )
    fields = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    title = Gtk.Label(label="Nom de la liseuse", xalign=0)
    fields.append(title)
    name = Gtk.Entry()
    name.set_placeholder_text("Ex. Kobo Clara BW")
    name.set_max_length(90)
    fields.append(name)
    uuid_label = Gtk.Label(label="UUID du volume USB", xalign=0)
    fields.append(uuid_label)
    fingerprint = Gtk.Entry()
    fingerprint.set_placeholder_text("Ex. 1234-ABCD")
    fingerprint.set_max_length(128)
    fields.append(fingerprint)
    dialog.set_extra_child(fields)
    dialog.add_response("cancel", "Annuler")
    dialog.add_response("confirm", "Associer")
    dialog.set_close_response("cancel")
    window._reader_pairing_dialog = dialog

    def current() -> bool:
        return (
            _still_current(window, scope)
            and getattr(window, "_reader_pairing_nonce", None) is nonce
            and getattr(window, "_reader_pairing_dialog", None) is dialog
        )

    def done(_dialog, response):
        try:
            if not current() or response != "confirm":
                return
            from ..core.device_usb_binding_proposal import (
                propose_usb_reader_binding,
            )
            identity, storage = scope[:2]
            previous = storage.device_bindings()
            proposal = propose_usb_reader_binding(
                identity_id=identity,
                label=name.get_text(), usb_fingerprint=fingerprint.get_text(),
                existing=previous,
            )
            if not current():
                return
            # The storage operation rechecks owner, UUID uniqueness and
            # exact device ID under its own IMMEDIATE transaction.
            # Pairing is deliberately DISABLED until a separate user click.
            storage.device_usb_binding_declare(
                identity_id=proposal.identity_id, device_id=proposal.device_id,
                label=proposal.label, usb_fingerprint=proposal.usb_fingerprint,
                enabled=proposal.enabled,
            )
            if current():
                window._reader_selected_device_id = proposal.device_id
                window._render_readers()
                window._reader_status_label.set_label(
                    "Liseuse déclarée · support non vérifié. "
                    "Activez les examens USB avant de choisir son volume."
                )
        except Exception:
            if current():
                window.show_error(
                    "Association impossible : vérifiez le profil, l'UUID et les "
                    "liseuses déjà enregistrées.",
                    "Liseuses",
                )
        finally:
            if getattr(window, "_reader_pairing_dialog", None) is dialog:
                window._reader_pairing_dialog = None

    dialog.connect("response", done)
    dialog.present()


def toggle_reader_examination(window) -> None:
    """Explicit setting toggle; does not scan, copy or delete a USB volume."""
    scope = _identity_scope(window)
    if scope is None:
        return
    identity, storage = scope[:2]
    device_id = getattr(window, "_reader_selected_device_id", None)
    if type(device_id) is not str or not device_id:
        return
    try:
        from ..core.device_usb_binding_proposal import (
            propose_usb_reader_binding,
        )
        binding = storage.device_binding_get(device_id)
        if (type(binding) is not dict
                or binding.get("identity_id") != identity
                or binding.get("device_id") != device_id
                or type(binding.get("metadata")) is not dict
                or type(binding["metadata"].get("usb_fingerprint")) is not str
                or type(binding.get("label")) is not str
                or type(binding.get("enabled")) is not bool):
            raise ValueError("invalid reader binding")
        fingerprint = binding["metadata"]["usb_fingerprint"]
        old_enabled = binding["enabled"]
        proposal = propose_usb_reader_binding(
            identity_id=identity,
            label=binding["label"], usb_fingerprint=fingerprint,
            existing=storage.device_bindings(),
        )
        if (not proposal.already_bound
                or proposal.device_id != device_id
                or proposal.enabled is not old_enabled
                or not _still_current(window, scope)
                or getattr(window, "_reader_selected_device_id", None) != device_id):
            return
        storage.device_usb_binding_declare(
            identity_id=identity, device_id=device_id, label=proposal.label,
            usb_fingerprint=proposal.usb_fingerprint, enabled=not old_enabled,
        )
        if _still_current(window, scope):
            window._render_readers()
            window._reader_status_label.set_label(
                ("Examens USB activés · présence physique toujours inconnue."
                 if not old_enabled else
                 "Examens USB désactivés · aucune opération effectuée.")
            )
    except Exception:
        if _still_current(window, scope):
            window.show_error(
                "Modification indisponible : vérifiez le profil et la liseuse.",
                "Liseuses",
            )


__all__ = ["request_reader_pairing", "toggle_reader_examination"]
