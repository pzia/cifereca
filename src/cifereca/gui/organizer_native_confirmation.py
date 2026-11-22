"""#3176 private native confirmation for one exact read-only Organizer page.

No mutation method crosses the guided plugin contract. The Core worker owns
scope, shared lock and production fence. A visible DTO is never authority.
"""
from ..plugin_api.library_organizer import OrganizerPageAdvice, OrganizerDryRunAdvice
from ..task_queue import TaskPriority
from unicodedata import category


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
        self._identity_id = getattr(lease, "identity_id", None)
        self._page = self._used = self._dialog = None
        self._review_witness = None
        self._disposed = False
        self.button = Gtk.Button(label="Confirmer le renommage…")
        self.button.set_visible(False)
        self.button.set_sensitive(False)
        self.button.connect("clicked", self._clicked)

    def _lease_current(self):
        # Revocation is a negative-only guard. A disposed activation may
        # raise while GTK delivers a late response or worker completion.
        try:
            return (not self._disposed
                    and type(self._identity_id) is str
                    and 0 < len(self._identity_id) <= 1024
                    and self._displayable_path(self._identity_id)
                    and self.lease.identity_id == self._identity_id
                    and self.lease.is_current() is True)
        except Exception:
            return False

    @staticmethod
    def _advice_witness(page):
        # The DTO is frozen by convention, but Python can still replace its
        # attributes through object.__setattr__. Pin the exact visible text.
        if (type(page) is not OrganizerPageAdvice
                or type(page.dry_run_entries) is not tuple
                or len(page.dry_run_entries) != 1
                or type(page.dry_run_entries[0]) is not OrganizerDryRunAdvice):
            return None
        entry = page.dry_run_entries[0]
        return (page.token, page.mutation_authorized, page.dry_run_state,
                entry.operation, entry.state, entry.current, entry.proposed)

    def _current(self, page):
        return (self._page is page and self._review_witness is not None
                and self._advice_witness(page) == self._review_witness
                and self._lease_current())

    @staticmethod
    def _displayable_path(value):
        # Plugin advice is a presentation DTO, not a Core execution permit.
        # Refuse malformed/unsafe display text before enabling confirmation.
        return (type(value) is str and 0 < len(value) <= 4096
                and all(category(ch) not in ("Cc", "Cf", "Cs", "Zl", "Zp")
                        for ch in value))

    def observe(self, value):
        from ..core import library_organizer_execution as execution
        entries = value.dry_run_entries if type(value) is OrganizerPageAdvice else ()
        valid = (type(value) is OrganizerPageAdvice
                 and value.mutation_authorized is False
                 and value.dry_run_state == "confirmed"
                 and type(entries) is tuple and len(entries) == 1
                 and type(entries[0]) is OrganizerDryRunAdvice
                 and entries[0].operation == "rename"
                 and entries[0].state == "review_only"
                 and self._displayable_path(entries[0].current)
                 and self._displayable_path(entries[0].proposed)
                 and callable(getattr(execution, "admit_native_reviewed_rename", None))
                 and self._lease_current())
        # A newly displayed or revoked DTO supersedes the exact consent
        # shown in a mounted native dialog. Dismiss it now, not only on
        # disposal: an old Apply signal can never target the new review.
        witness = self._advice_witness(value) if valid else None
        old_dialog = self._dialog if self._dialog is not None and (
            self._page is not value or not valid
            or self._review_witness != witness) else None
        self._page = value if valid else None
        self._review_witness = witness
        if old_dialog is not None:
            self._dialog = None
            try:
                old_dialog.close()
            except Exception:
                pass
        self.button.set_visible(valid)
        self.button.set_sensitive(valid and value is not self._used and self._dialog is None)

    def dispose(self):
        # Disposal is terminal for this widget. A queued observer callback
        # must not re-arm a review even when the old lease still reports live.
        self._disposed = True
        # A profile switch or unmount revokes even an already presented
        # confirmation. Close the native dialog before it can outlive the
        # owning view; its late response must never enqueue Core work.
        dialog = self._dialog
        self._dialog = None
        self._page = None
        self._review_witness = None
        self.button.set_visible(False)
        self.button.set_sensitive(False)
        if dialog is not None:
            try:
                dialog.close()
            except Exception:
                pass

    def _clicked(self, _button):
        from gi.repository import Adw
        return self.open(Adw)

    def open(self, Adw):
        page = self._page
        if (page is None or page is self._used or self._dialog is not None
                or not self._current(page)):
            return False
        entry, = page.dry_run_entries
        shown_witness = self._review_witness
        dialog = None
        try:
            dialog = Adw.MessageDialog.new(
                self.window, "Confirmer le renommage ?",
                "Profil : " + self._identity_id + "\n"
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
        except Exception:
            # Factory/response setup runs before the response handler exists.
            # Do not consume the plan or leave an incomplete native dialog.
            if dialog is not None:
                try:
                    close = getattr(dialog, "close", None)
                    if callable(close):
                        close()
                except Exception:
                    pass
            return False
        self._dialog = dialog
        self.button.set_sensitive(False)
        answered = False

        def response(_dialog, response_id):
            nonlocal answered
            if answered:
                return
            answered = True
            # An old GTK signal may arrive after a replacement review has
            # already opened its own dialog. Never clear or re-arm that
            # newer dialog (and never enqueue the old Core command).
            if self._dialog is not dialog:
                return
            self._dialog = None
            if (response_id != "apply" or not self._current(page)
                    or self._review_witness != shown_witness):
                self.button.set_sensitive(self._page is not None
                                          and self._page is not self._used
                                          and self._lease_current())
                return
            self._used = page

            def current():
                return (self._review_witness == shown_witness
                        and self._current(page))

            def work():
                from ..core import library_organizer_execution as execution
                admit = getattr(execution, "admit_native_reviewed_rename", None)
                if not callable(admit) or not current():
                    return None
                command = admit(
                    self.window.plugin_manager, self.resolved, page,
                    service=self.window.service, identity_id=self._identity_id,
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
            # A late signal from closing a half-mounted dialog is inert.
            answered = True
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
