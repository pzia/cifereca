"""P0 #2055: GUI consent is one user gesture, never a generic BnF borrow.

Headless controller tests use real Core/SQLite and an offline synthetic BnF.
An installed production plugin may advertise manual borrowing; no test
here makes live HTTP calls or real lender mutations.
"""
from dataclasses import replace
from types import SimpleNamespace as NS

from cifereca.gui.bnf_borrow_controller import present_bnf_borrow
from tests.test_core_p0_2055_manual_dispatch import setup


class Dialog:
    def __init__(self):
        self.responses = {}
        self.callback = None
        self.present_count = 0

    def add_response(self, key, title):
        self.responses[key] = title

    def connect(self, event, callback):
        assert event == "response"
        self.callback = callback

    def present(self):
        self.present_count += 1

    def respond(self, key):
        assert self.callback is not None
        self.callback(self, key)


class DialogApi:
    instance = None

    class MessageDialog:
        @staticmethod
        def new(window, title, body):
            assert "BnF" in title and "Un seul envoi" in body
            assert "pret.bnf.fr" in body and "Synthetic book" in body
            dialog = Dialog()
            DialogApi.instance = dialog
            return dialog


class DeferredRunner:
    def __init__(self):
        self.tasks = []

    def submit(self, title, work, done, failed, **kw):
        assert kw["kind"] == "borrow"
        self.tasks.append((work, done, failed))

    def complete_one(self):
        work, done, failed = self.tasks.pop(0)
        try:
            result = work()
        except Exception as error:
            failed(error)
            return None
        done(result)
        return result


def view(service, session, account, item):
    messages, results, refreshes = [], [], []
    window = NS(
        service=service, _closed=False, _identity_context_generation=0,
        _account_context_generation=0,
        _selected_search_id=item.id, _search_detail_request_generation=9,
        _eligible_borrow_accounts=lambda _item: (session, [account]),
        show_error=lambda message, title: messages.append((message, title)),
        _activity_result=results.append,
        refresh_loans=lambda force: refreshes.append(force),
        runner=DeferredRunner(),
    )
    return window, messages, results, refreshes


def request(window, item):
    present_bnf_borrow(
        window, item, None,
        Adw=DialogApi, TaskPriority=NS(USER_ACTION=object()),
    )
    return DialogApi.instance


def test_unpublished_bnf_action_cannot_even_open_a_confirmation(tmp_path):
    service, session, account, item = setup(tmp_path, declared=False)
    try:
        DialogApi.instance = None
        window, errors, results, refreshes = view(service, session, account, item)
        assert request(window, item) is None
        assert not window.runner.tasks
        assert session.dispatches == session.reads == []
        assert errors == results == refreshes == []
    finally:
        service.close()


def test_future_positive_bnf_requires_dialog_confirm_one_time_only(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        window, errors, results, refreshes = view(service, session, account, item)
        dialog = request(window, item)
        assert dialog is not None and dialog.present_count == 1
        assert len(dialog.responses) == 2
        assert dialog.responses["borrow"] == "J’ai vérifié sur BnF et je confirme"
        assert session.dispatches == session.reads == []
        dialog.respond("cancel")
        assert not window.runner.tasks
        dialog.respond("borrow")
        dialog.respond("borrow")  # the same modal cannot dispatch twice
        assert len(window.runner.tasks) == 1
        assert session.dispatches == []
        outcome = window.runner.complete_one()
        assert outcome["status"] == "confirmed"
        assert session.dispatches == [("book-1", "reader")]
        assert service.can_borrow_bnf(item) is False  # checkpoint consumed
        assert results == ["Prêt BnF actif vérifié dans Mes emprunts"]
        assert refreshes == [True]
        assert errors == []
        dialog.respond("borrow")
        assert not window.runner.tasks
    finally:
        service.close()


def test_identity_aba_before_confirm_blocks_without_claim(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        window, errors, results, refreshes = view(service, session, account, item)
        dialog = request(window, item)
        window._identity_context_generation += 2
        dialog.respond("borrow")
        assert not window.runner.tasks and not session.dispatches
        assert errors and "a changé" in errors[0][0]
    finally:
        service.close()


def test_notice_refresh_before_confirm_blocks_even_with_same_catalogue_row(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        window, errors, results, refreshes = view(service, session, account, item)
        dialog = request(window, item)
        item.record_id = "book-other"
        dialog.respond("borrow")
        assert not window.runner.tasks and not session.dispatches
        assert len(errors) == 1
    finally:
        service.close()


def test_title_changed_after_site_checked_dialog_blocks_before_claim(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        window, errors, results, refreshes = view(service, session, account, item)
        dialog = request(window, item)
        assert dialog is not None
        item.title = "Another edition with same resource id"
        dialog.respond("borrow")
        assert not window.runner.tasks and not session.dispatches
        assert errors and "a changé" in errors[0][0]
    finally:
        service.close()


def test_scope_change_after_queued_confirmation_blocks_worker(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        window, errors, results, refreshes = view(service, session, account, item)
        dialog = request(window, item)
        dialog.respond("borrow")
        window._account_context_generation += 1
        outcome = window.runner.complete_one()
        assert outcome["status"] == "blocked"
        assert session.reads == session.dispatches == []
        assert not results and not refreshes
    finally:
        service.close()


def test_core_epoch_change_after_modal_does_not_claim_or_post(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        window, errors, results, refreshes = view(service, session, account, item)
        dialog = request(window, item)
        dialog.respond("borrow")
        service._account_context_epoch += 2
        outcome = window.runner.complete_one()
        assert outcome["status"] == "blocked"
        assert session.reads == session.dispatches == []
    finally:
        service.close()


def test_upstream_permission_denial_never_leaks_sensitive_errors_to_modal(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        window, errors, results, refreshes = view(service, session, account, item)
        dialog = request(window, item)
        dialog.respond("borrow")
        session.failure = TimeoutError("https://pret.bnf.fr/sensitive?csrf=SECRET")
        outcome = window.runner.complete_one()
        assert outcome["status"] == "verification_unavailable"
        assert len(session.dispatches) == 1
        assert errors and "incertain" in errors[0][0]
        assert "SECRET" not in str(errors)
        assert not results and not refreshes
    finally:
        service.close()


def test_no_confusion_of_selected_account_by_draft_copy(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        window, errors, results, refreshes = view(service, session, account, item)
        dialog = request(window, item)
        dialog.respond("borrow")
        service.accounts = [replace(account)]
        outcome = window.runner.complete_one()
        assert outcome["status"] == "blocked"
        assert session.reads == session.dispatches == []
        assert not results
    finally:
        service.close()
