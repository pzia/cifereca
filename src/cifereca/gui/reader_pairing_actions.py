"""P1 #2854: explicit GTK declaration/activation of profile-scoped USB readers.

No hardware, network, provider or file I/O. Pairing accepts a user-supplied
block-volume UUID only as a declarative claim; the scanner independently
attests Linux mountinfo/sysfs/UUID before reporting any physical presence.
Activation is a separate deliberate control, never implied by pairing.
"""
from __future__ import annotations


class _PairingRefusal(ValueError):
    """Only stable local GUI refusal reasons; never raw device metadata."""


_PAIRING_FEEDBACK = {
    "selection_required": (
        "Choisissez un volume USB monté dans la liste, ou activez "
        "la saisie d'UUID en mode expert."
    ),
    "volume_changed": (
        "Le volume USB a changé ou a été débranché. Actualisez "
        "les volumes et sélectionnez à nouveau le lecteur."
    ),
}


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
    """Choose a currently mounted USB volume or opt into manual UUID entry.

    Pairing stores a declarative UUID only. The volume list is a revocable
    read-only observation; neither choosing nor pairing activates examination.
    """
    scope = _identity_scope(window)
    if scope is None:
        return
    if Adw is None:
        from gi.repository import Adw as AdwRuntime
        Adw = AdwRuntime
    from ..core.device_usb_discovery import (
        MountedUsbVolume, discover_linux_usb_volumes,
    )
    from ..core.device_usb_binding_proposal import propose_usb_reader_binding

    nonce = object()
    window._reader_pairing_nonce = nonce
    dialog = Adw.MessageDialog.new(
        window, "Associer une liseuse USB",
        "Sélectionnez un volume USB monté. L'association reste désactivée et "
        "ne prouve pas la présence future de la liseuse ; aucun examen, "
        "transfert ou effacement n'est déclenché.",
    )
    fields = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    volume_label = Gtk.Label(label="Volume USB monté", xalign=0)
    fields.append(volume_label)
    volume_choice = Gtk.DropDown.new_from_strings(["Aucun volume compatible détecté"])
    volume_choice.set_selected(0xFFFFFFFF)  # Explicit choice, no first-device default.
    volume_choice.set_hexpand(True)
    volume_choice.set_tooltip_text(
        "Support, capacité, format et point de montage ; UUID masqué."
    )
    fields.append(volume_choice)
    refresh = Gtk.Button(label="Actualiser les volumes")
    fields.append(refresh)
    status = Gtk.Label(label="", xalign=0)
    status.set_wrap(True)
    fields.append(status)
    fields.append(Gtk.Label(label="Nom de la liseuse", xalign=0))
    name = Gtk.Entry()
    name.set_placeholder_text("Ex. Kobo Clara BW")
    name.set_max_length(90)
    fields.append(name)
    expert = Gtk.CheckButton(label="Saisir l'UUID manuellement (mode expert)")
    fields.append(expert)
    uuid_label = Gtk.Label(label="UUID exact du volume USB", xalign=0)
    fields.append(uuid_label)
    fingerprint = Gtk.Entry()
    fingerprint.set_placeholder_text("Ex. 1234-ABCD (lsblk -f)")
    fingerprint.set_max_length(128)
    fields.append(fingerprint)
    expert_notice = Gtk.Label(
        label="Mode expert : un UUID déclaré ne prouve ni montage ni "
              "présence physique. Aucun transfert sans vérification USB.",
        xalign=0,
    )
    expert_notice.set_wrap(True)
    fields.append(expert_notice)

    dialog.set_extra_child(fields)
    dialog.add_response("cancel", "Annuler")
    dialog.add_response("confirm", "Associer")
    dialog.set_close_response("cancel")
    window._reader_pairing_dialog = dialog
    observed: tuple[MountedUsbVolume, ...] = ()
    suggested_name = ""
    reloading = False

    def current() -> bool:
        return (
            _still_current(window, scope)
            and getattr(window, "_reader_pairing_nonce", None) is nonce
            and getattr(window, "_reader_pairing_dialog", None) is dialog
        )

    def mode_changed(*_args):
        manual = expert.get_active() is True
        for widget in (volume_label, volume_choice, refresh, status):
            widget.set_visible(not manual)
        for widget in (uuid_label, fingerprint, expert_notice):
            widget.set_visible(manual)

    def selected_changed(*_args):
        nonlocal suggested_name
        if reloading or not current() or expert.get_active():
            return
        index = volume_choice.get_selected()
        if type(index) is not int or not 0 <= index < len(observed):
            return
        label = observed[index].label
        if not name.get_text() or name.get_text() == suggested_name:
            name.set_text(label)
        suggested_name = label

    def reload_volumes(*_args):
        nonlocal observed, suggested_name, reloading
        if not current():
            return
        try:
            found = discover_linux_usb_volumes()
            if (type(found) is not tuple or len(found) > 128
                    or any(type(row) is not MountedUsbVolume for row in found)):
                found = ()
        except Exception:
            found = ()
        reloading = True
        try:
            if suggested_name and name.get_text() == suggested_name:
                # Refresh must not leave the old device label attached to a
                # newly selected USB mount. Explicit user edits survive.
                name.set_text("")
            observed = found
            volume_choice.set_model(Gtk.StringList.new(
                [row.display_label() for row in found]
                or ["Aucun volume compatible détecté"]
            ))
            volume_choice.set_selected(0xFFFFFFFF)
            volume_choice.set_sensitive(bool(found))
            status.set_label(
                "Choisissez explicitement un volume ; aucun volume présélectionné."
                if found else
                "Aucun volume USB compatible détecté. Actualisez ou utilisez "
                "la saisie d'UUID en mode expert."
            )
            suggested_name = ""
        finally:
            reloading = False

    def done(_dialog, response):
        try:
            if not current() or response != "confirm":
                return
            identity, storage = scope[:2]
            if expert.get_active() is True:
                uuid = fingerprint.get_text()
            else:
                index = volume_choice.get_selected()
                if type(index) is not int or not 0 <= index < len(observed):
                    raise _PairingRefusal("selection_required")
                chosen = observed[index]
                # Reattest a *fresh* kernel observation at the confirmation
                # edge. Replug, changed UUID or changed mount ID invalidates
                # the earlier UI choice. Never fall back to a path-only match.
                live = discover_linux_usb_volumes()
                if (type(live) is not tuple
                        or sum(
                            type(row) is MountedUsbVolume
                            and row.selection_key() == chosen.selection_key()
                            for row in live
                        ) != 1):
                    raise _PairingRefusal("volume_changed")
                uuid = chosen.usb_fingerprint
            if not current():
                return
            proposal = propose_usb_reader_binding(
                identity_id=identity, label=name.get_text(),
                usb_fingerprint=uuid, existing=storage.device_bindings(),
            )
            if not current():
                return
            # Storage still enforces uniqueness/owner in its IMMEDIATE
            # transaction; association never activates a newly added reader.
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
        except _PairingRefusal as refusal:
            if current():
                window.show_error(
                    _PAIRING_FEEDBACK.get(
                        str(refusal), "Association USB impossible.",
                    ),
                    "Liseuses",
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

    expert.connect("toggled", mode_changed)
    volume_choice.connect("notify::selected", selected_changed)
    refresh.connect("clicked", reload_volumes)
    dialog.connect("response", done)
    mode_changed()
    reload_volumes()
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
