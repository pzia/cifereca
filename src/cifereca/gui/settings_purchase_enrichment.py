"""Independent documentation preference; all Storage I/O on existing runner."""
from ..core.notice_c_purchase_settings import read_purchase_policy, change_purchase_policy
from ..task_queue import TaskPriority


def build_purchase_enrichment_settings(window, *, section, page_lifetime, Gtk, Adw):
    group = Adw.PreferencesGroup()
    group.set_title("Enrichissement documentaire des achats")
    group.set_description(
        "Réglage global. Après une synchronisation autorisée, les fournisseurs "
        "compatibles peuvent enrichir les notices depuis leur catalogue public activé. "
        "Désactiver ne supprime aucune notice ni aucun livre."
    )
    section.append(group)
    status = Gtk.Label(xalign=0)
    status.set_wrap(True)
    button = Gtk.Button(label="Modifier le réglage")
    button.set_sensitive(False)
    group.add(status)
    group.add(button)
    shown = [None]
    pending = [None]

    def alive():
        return page_lifetime.alive and not getattr(window, "_closed", False)

    def render():
        if not alive():
            return
        value = shown[0]
        if value is None or value[0] is not window.service.storage:
            shown[0] = None
            status.set_label("Réglage indisponible.")
            button.set_sensitive(False)
            return
        enabled = value[1].enabled
        status.set_label("Enrichissement automatique activé." if enabled
                         else "Enrichissement automatique désactivé.")
        button.set_label("Désactiver" if enabled else "Activer")
        button.set_sensitive(pending[0] is None)

    def submit(storage, work):
        if not alive() or pending[0] is not None:
            return
        token = object()
        pending[0] = token
        button.set_sensitive(False)
        status.set_label("Lecture ou modification du réglage…")

        def finish(result):
            if pending[0] is not token:
                return False
            pending[0] = None
            if not alive():
                return False
            shown[0] = ((storage, result) if result is not None
                        and storage is window.service.storage else None)
            render()
            return True

        def failed(_error):
            if finish(None):
                window.show_error("Réglage non confirmé. Rouvrir les réglages pour le vérifier.")

        try:
            window.runner.submit("Réglage de l’enrichissement documentaire…", work,
                                 finish, failed, priority=TaskPriority.USER_ACTION,
                                 kind="settings")
        except Exception as error:
            failed(error)

    def refresh(*_args):
        if not alive() or pending[0] is not None:
            return
        storage = window.service.storage
        submit(storage, lambda: read_purchase_policy(storage))

    def clicked(_button):
        if not alive() or pending[0] is not None:
            return
        value = shown[0]
        if value is None or value[0] is not window.service.storage:
            refresh()
            return
        storage, before = value
        submit(storage, lambda: change_purchase_policy(
            window.service, expected_storage=storage, expected=before,
            enabled=not before.enabled,
        ))

    button.connect("clicked", clicked)
    group.connect("map", refresh)
    return status, button
