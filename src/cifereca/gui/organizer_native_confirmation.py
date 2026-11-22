"""#3176 private native confirmation for one exact read-only Organizer page.

No mutation method crosses the guided plugin contract. The Core worker owns
scope, shared lock and production fence. A visible DTO is never authority.
"""
from ..plugin_api.library_organizer import OrganizerPageAdvice, OrganizerDryRunAdvice
from ..task_queue import TaskPriority


def has_native_organizer_review(manager, resolved):
    """Select the existing activated read port, not a concrete plugin ID."""
    from ..core.activation_scoped_broker import ActivationBrokerResolver, _ActivationReadLease
    from ..core.library_organizer_broker import LibraryOrganizerReadBrokerImpl
    try:
        if (manager._registry_generation != resolved.registry_generation
                or manager.service(resolved.plugin_id) is not resolved.service):
            return False
        record = manager._records.get(resolved.plugin_id)
        if record is None or record.service is not resolved.service:
            return False
        resolver = record.activation_brokers
        if type(resolver) is not ActivationBrokerResolver or not resolver.current():
            return False
        with resolver._lock:
            lease = resolver._scoped.get("library.organizer.read")
        return (type(lease) is _ActivationReadLease
                and type(lease._reader) is LibraryOrganizerReadBrokerImpl
                and resolver.current() is True)
    except Exception:
        return False


class NativeOrganizerConfirmation:
    def __init__(self, window, resolved, lease, *, Gtk):
        self.window, self.resolved, self.lease = window, resolved, lease
        self._page = self._used = self._dialog = None
        self.button = Gtk.Button(label="Confirmer le renommage…")
        self.button.set_visible(False)
        self.button.set_sensitive(False)
        self.button.connect("clicked", self._clicked)

    def _current(self, page):
        return self._page is page and self.lease.is_current()

    def observe(self, value):
        from ..core import library_organizer_execution as execution
        valid = (type(value) is OrganizerPageAdvice
                 and value.mutation_authorized is False
                 and value.dry_run_state == "confirmed"
                 and len(value.dry_run_entries) == 1
                 and type(value.dry_run_entries[0]) is OrganizerDryRunAdvice
                 and value.dry_run_entries[0].operation == "rename"
                 and value.dry_run_entries[0].state == "review_only"
                 and callable(getattr(execution, "admit_native_reviewed_rename", None))
                 and self.lease.is_current())
        self._page = value if valid else None
        self.button.set_visible(valid)
        self.button.set_sensitive(valid and value is not self._used and self._dialog is None)

    def dispose(self):
        self._page = None
        self.button.set_visible(False)
        self.button.set_sensitive(False)

    def _clicked(self, _button):
        from gi.repository import Adw
        return self.open(Adw)

    def open(self, Adw):
        page = self._page
        if (page is None or page is self._used or self._dialog is not None
                or not self._current(page)):
            return False
        entry, = page.dry_run_entries
        dialog = Adw.MessageDialog.new(
            self.window, "Confirmer le renommage ?",
            "Profil : " + self.lease.identity_id + "\n"
            "Chemin actuel : " + entry.current + "\n"
            "Nom proposé : " + entry.proposed + "\n\n"
            "Un seul renommage, sans suppression de doublon ni changement de droit. "
            "La confirmation ne lève pas les gardes Core ; si elles ne sont pas "
            "qualifiées, aucune modification ne sera exécutée.")
        dialog.set_body_use_markup(False)
        dialog.add_response("cancel", "Annuler")
        dialog.add_response("apply", "Confirmer")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        self._dialog = dialog
        self.button.set_sensitive(False)
        answered = False

        def response(_dialog, response_id):
            nonlocal answered
            if answered:
                return
            answered = True
            self._dialog = None
            if response_id != "apply" or not self._current(page):
                self.button.set_sensitive(self._page is not None
                                          and self._page is not self._used
                                          and self.lease.is_current())
                return
            self._used = page

            def current():
                return self._current(page)

            def work():
                from ..core import library_organizer_execution as execution
                admit = getattr(execution, "admit_native_reviewed_rename", None)
                if not callable(admit) or not current():
                    return None
                command = admit(
                    self.window.plugin_manager, self.resolved, page,
                    service=self.window.service, identity_id=self.lease.identity_id,
                    confirmed=True, view_current=current)
                return command() if callable(command) and current() else None

            def completed(result):
                if not current():
                    return
                status = result.get("status") if type(result) is dict else None
                self.window.toast({
                    "renamed": "Renommage confirmé. Relancez la revue locale.",
                    "recovery_required": "Reprise nécessaire. Relancez la vérification ; ne supprimez aucun fichier.",
                }.get(status, "Renommage indisponible ou refusé. Relancez la revue."))

            try:
                self.window.runner.submit(
                    "Confirmation de renommage local", work, completed,
                    lambda _error: completed(None), priority=TaskPriority.MAINTENANCE,
                    kind="maintenance-plan", origin="user")
            except Exception:
                completed(None)

        try:
            dialog.connect("response", response)
            dialog.present()
        except Exception:
            # A GTK mount/presentation failure must not strand the exact
            # one-shot review with a disabled button or queue Core work.
            # No response has been accepted: preserve the production fence.
            self._dialog = None
            try:
                close = getattr(dialog, "close", None)
                if callable(close):
                    close()
            except Exception:
                pass
            self.button.set_sensitive(self._current(page) and page is not self._used)
            return False
        return True
