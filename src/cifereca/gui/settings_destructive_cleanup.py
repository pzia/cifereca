"""#4496: separate opt-in control, never a general Settings draft or cleanup."""
from __future__ import annotations

from ..config import Config
from ..core.destructive_cleanup_policy import destructive_cleanup_enabled
from ..core.destructive_cleanup_settings import change_destructive_cleanup_setting
from ..locking import LockBusyError
from ..task_queue import TaskPriority


def build_cleanup_settings(window, *, section, page_lifetime, Gtk, Adw):
    group = Adw.PreferencesGroup()
    group.set_title("Maintenance · réglages avancés")
    group.set_description(
        "Réglage global de cette installation. Aucune suppression automatique. "
        "Chaque nettoyage admis nécessite encore une confirmation et des preuves Core."
    )
    section.append(group)
    status = Gtk.Label(xalign=0)
    status.set_wrap(True)
    group.add(status)
    button = Gtk.Button()
    group.add(button)
    pending = [None]
    dialog_token = [None]
    shown = [None]

    def alive():
        return page_lifetime.alive and not getattr(window, "_closed", False)

    def config():
        value = getattr(window.service, "config", None)
        return value if type(value) is Config else None

    def refresh(*_args):
        if not alive():
            return
        cfg = config()
        if cfg is None:
            shown[0] = None
            status.set_label("Configuration Core indisponible.")
            button.set_label("Modifier le réglage")
            button.set_sensitive(False)
            return
        enabled = destructive_cleanup_enabled(cfg)
        shown[0] = (cfg, enabled)
        status.set_label(
            "Nettoyage destructif autorisé sous contrôles Core."
            if enabled else "Nettoyage destructif désactivé."
        )
        button.set_label("Désactiver le nettoyage" if enabled
                         else "Autoriser le nettoyage supervisé…")
        button.set_sensitive(pending[0] is None and dialog_token[0] is None)

    def submit(cfg, before, desired):
        token = object()
        pending[0] = token
        button.set_sensitive(False)

        def work():
            return change_destructive_cleanup_setting(
                window.service, expected_config=cfg, expected_enabled=before,
                enabled=desired, confirmed=True,
            )

        def finish():
            if pending[0] is not token:
                return False
            pending[0] = None
            if not alive():
                return False
            refresh()
            return True

        def done(result):
            if not finish():
                return
            if not result.audit_complete:
                window.show_error(
                    "Réglage enregistré ; journal incomplet. Vérifier les événements locaux."
                )

        def failed(error):
            if not finish():
                return
            if isinstance(error, LockBusyError):
                message = "Bibliothèque occupée : réglage inchangé, réessayer après la maintenance."
            else:
                message = "Modification non confirmée par Core. Vérifier le réglage et le journal."
            window.show_error(message)

        try:
            window.runner.submit(
                "Modification du réglage de maintenance…", work, done, failed,
                priority=TaskPriority.USER_ACTION, kind="settings",
            )
        except Exception as error:
            failed(error)

    def clicked(_button):
        if not alive() or pending[0] is not None or dialog_token[0] is not None:
            return
        cfg = config()
        if cfg is None or shown[0] != (cfg, destructive_cleanup_enabled(cfg)):
            refresh()
            return
        before = destructive_cleanup_enabled(cfg)
        desired = not before
        token = object()
        dialog_token[0] = token
        button.set_sensitive(False)
        parent = button.get_root() or window
        dialog = Adw.MessageDialog.new(
            parent,
            "Autoriser le nettoyage supervisé ?" if desired else "Désactiver le nettoyage ?",
            ("Cette option globale permet les suppressions supervisées admissibles. "
             "Elle ne supprime aucun fichier maintenant. Les preuves et confirmations "
             "par opération restent obligatoires." if desired else
             "Les nouveaux nettoyages seront bloqués. Une reprise explicite de "
             "tombstone existant conserve ses propres contrôles."),
        )
        dialog.add_response("cancel", "Annuler")
        dialog.add_response("accept", "Autoriser" if desired else "Désactiver")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def response(_dialog, choice):
            if dialog_token[0] is not token:
                return
            dialog_token[0] = None
            if not alive():
                return
            if (choice != "accept" or config() is not cfg
                    or destructive_cleanup_enabled(cfg) is not before):
                refresh()
                return
            submit(cfg, before, desired)

        dialog.connect("response", response)
        dialog.present()

    button.connect("clicked", clicked)
    group.connect("map", refresh)
    refresh()
    return status, button
