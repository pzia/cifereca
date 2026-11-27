"""Explicit local reader association, setup and non-destructive unlink."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from ..task_queue import TaskPriority
from .reader_pairing_actions import _identity_scope, _still_current
from .reader_usb_preview_actions import _discover_mounted_binding


def request_local_reader(window, *, Gtk):
    scope = _identity_scope(window)
    if scope is None:
        return
    chooser = Gtk.FileChooserNative.new(
        "Associer un dossier dédié", window, Gtk.FileChooserAction.SELECT_FOLDER,
        "Associer", "Annuler",
    )
    window._reader_local_chooser = chooser

    def selected(dialog, response):
        try:
            if (response != Gtk.ResponseType.ACCEPT
                    or window._reader_local_chooser is not dialog
                    or not _still_current(window, scope)):
                return
            file = dialog.get_file()
            path = file.get_path() if file is not None else None
            if not path:
                return
            root = Path(path)
            owner, storage = scope[:2]
            # Reject overlap with all configured profile roots, including
            # empty libraries. Storage separately verifies physical identity.
            from ..core.library_audit_roots import configured_identity_roots
            config = window.service.config
            for profile in config.identities():
                for protected in configured_identity_roots(config, profile["id"])[profile["id"]]:
                    if root.is_relative_to(protected) or protected.is_relative_to(root):
                        raise ValueError("local reader overlaps library")
            if not _still_current(window, scope):
                return
            device = storage.device_local_reader_declare(
                identity_id=owner, device_id="local-" + uuid4().hex,
                label=root.name[:90], root=root, enabled=False,
            )
            if _still_current(window, scope):
                window._reader_selected_device_id = device
                window._render_readers()
                window._reader_status_label.set_label(
                    "Dossier associé. Activez la liseuse puis initialisez son dossier géré.")
        except Exception:
            if _still_current(window, scope):
                window.show_error("Dossier dédié non admissible ou déjà associé.", "Liseuses")
        finally:
            dialog.destroy()
            if window._reader_local_chooser is dialog:
                window._reader_local_chooser = None
    chooser.connect("response", selected)
    chooser.show()


def _selected(window):
    scope = _identity_scope(window)
    if scope is None:
        return None
    device = getattr(window, "_reader_selected_device_id", None)
    if not device:
        return None
    row = scope[1].device_binding_get(device)
    if not row or row.get("identity_id") != scope[0]:
        return None
    fingerprint = (row.get("metadata") or {}).get("usb_fingerprint")
    if not fingerprint:
        return None
    return scope, device, row, fingerprint, getattr(window, "_reader_selection_generation", None)


def _current(window, selected):
    try:
        return _still_current(window, selected[0]) and _selected(window) == selected
    except Exception:
        return False


def request_reader_setup(window, button, *, Adw=None):
    selected = _selected(window)
    if selected is None or selected[2].get("enabled") is not True:
        return
    scope, device, _, fingerprint, _ = selected
    epoch = window.service._account_context_epoch
    broker = window.plugin_manager.broker("reader.usb")
    if not callable(getattr(broker, "initialize_target", None)):
        return
    if Adw is None:
        from gi.repository import Adw
    dialog = Adw.MessageDialog.new(window, "Initialiser le dossier géré ?",
        "Crée uniquement les répertoires nécessaires sur la destination attestée. "
        "Aucun livre existant ne sera adopté, déplacé ou supprimé.")
    dialog.add_response("cancel", "Annuler")
    dialog.add_response("setup", "Initialiser")
    dialog.set_close_response("cancel")
    def answered(_dialog, response):
        if response != "setup" or not _current(window, selected):
            return
        def work():
            volume = _discover_mounted_binding(storage=scope[1], identity_id=scope[0],
                device_id=device, fingerprint=fingerprint)
            return volume is not None and broker.initialize_target(
                identity_id=scope[0], device_id=device, mountpoint=volume.mountpoint,
                expected_profile_epoch=epoch, mount_key=volume.selection_key())
        def done(result):
            if _current(window, selected):
                window._reader_status_label.set_label(
                    "Dossier géré initialisé." if result is True else "Initialisation refusée.")
        window.runner.submit("Initialiser le dossier géré…", work, ok=done,
            err=lambda _: done(False), control=button, priority=TaskPriority.USER_ACTION,
            kind="maintenance", origin="user")
    dialog.connect("response", answered)
    dialog.present()


def request_reader_forget(window, *, Adw=None):
    selected = _selected(window)
    if selected is None or getattr(window, "_reader_sync_active", False):
        return
    scope, device, _, fingerprint, _ = selected
    if Adw is None:
        from gi.repository import Adw
    dialog = Adw.MessageDialog.new(window, "Dissocier cette liseuse ?",
        "Les souhaits et l'association seront oubliés. Aucun fichier ne sera supprimé.")
    dialog.add_response("cancel", "Annuler")
    dialog.add_response("forget", "Dissocier")
    dialog.set_close_response("cancel")
    def answered(_dialog, response):
        if (response != "forget" or not _current(window, selected)
                or getattr(window, "_reader_sync_active", False)):
            return
        try:
            scope[1].device_usb_binding_forget(identity_id=scope[0], device_id=device,
                                               fingerprint=fingerprint)
            window._render_readers()
        except Exception:
            window.show_error("Dissociation refusée.", "Liseuses")
    dialog.connect("response", answered)
    dialog.present()


def request_reader_directory(window, *, Gtk, Adw=None):
    selected = _selected(window)
    if selected is None or getattr(window, "_reader_sync_active", False):
        return
    scope, device, row, fingerprint, _ = selected
    if Adw is None:
        from gi.repository import Adw
    dialog = Adw.MessageDialog.new(window, "Dossier géré de la liseuse",
        "Chemin relatif à la racine du lecteur, modifiable avant toute réservation. "
        "Le nommage des nouveaux ajouts peut changer ensuite, sans migration ni "
        "suppression des fichiers existants.")
    entry = Gtk.Entry()
    entry.set_text(row["target_directory"])
    entry.set_max_length(512)
    from ..core.device_usb_target_name import NAMING_MODES
    mode = (row.get("metadata") or {}).get("reader_target_naming", "sha256")
    if mode not in NAMING_MODES:
        window.show_error("Mode de nommage non reconnu.", "Liseuses")
        return
    fields = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    fields.append(entry)
    fields.append(Gtk.Label(label="Nommage des prochains ajouts", xalign=0))
    naming = Gtk.DropDown.new_from_strings([
        "Empreinte SHA uniquement", "Nom local + empreinte SHA",
    ])
    naming.set_selected(NAMING_MODES.index(mode))
    fields.append(naming)
    dialog.set_extra_child(fields)
    dialog.add_response("cancel", "Annuler")
    dialog.add_response("save", "Enregistrer")
    dialog.set_close_response("cancel")
    def answered(_dialog, response):
        if (response != "save" or not _current(window, selected)
                or getattr(window, "_reader_sync_active", False)):
            return
        try:
            index = naming.get_selected()
            if type(index) is not int or not 0 <= index < len(NAMING_MODES):
                raise ValueError("invalid reader naming mode")
            scope[1].device_usb_directory_set(identity_id=scope[0], device_id=device,
                fingerprint=fingerprint, target_directory=entry.get_text(),
                target_naming=NAMING_MODES[index])
            window._render_readers()
        except Exception:
            window.show_error("Chemin invalide ou destinations déjà réservées.", "Liseuses")
    dialog.connect("response", answered)
    dialog.present()
