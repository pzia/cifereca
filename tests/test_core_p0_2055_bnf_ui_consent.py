"""P1 #3121: one user click on BnF Emprunter, never a second popup.

Headless controller tests use real Core/SQLite and an offline synthetic BnF.
An installed production plugin may advertise manual borrowing; no test
here makes live HTTP calls or real lender mutations.
"""
from dataclasses import replace
from types import SimpleNamespace as NS

from cifereca.gui.bnf_borrow_controller import present_bnf_borrow
from tests.test_core_p0_2055_manual_dispatch import setup


class DialogApi:
    """The removed modal must never be constructed by this controller."""
    instance = None

    class MessageDialog:
        @staticmethod
        def new(*_args, **_kwargs):
            raise AssertionError("No BnF availability confirmation dialog")


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


def test_single_explicit_click_queues_once_without_second_confirmation(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        window, errors, results, refreshes = view(service, session, account, item)
        request(window, item)
        assert DialogApi.instance is None
        assert len(window.runner.tasks) == 1
        assert session.dispatches == session.reads == []
        # A second click while the first work item is queued must not enqueue
        # a second transaction. No popup is needed to suppress double clicks.
        request(window, item)
        assert len(window.runner.tasks) == 1
        outcome = window.runner.complete_one()
        assert outcome["status"] == "confirmed"
        assert session.dispatches == [("book-1", "reader")]
        assert service.can_borrow_bnf(item) is False  # checkpoint consumed
        assert results == ["Prêt BnF actif vérifié dans Mes emprunts"]
        assert refreshes == [True]
        assert errors == []
        request(window, item)
        assert not window.runner.tasks
    finally:
        service.close()


def test_identity_aba_before_queued_work_blocks_without_claim(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        window, errors, results, refreshes = view(service, session, account, item)
        request(window, item)
        window._identity_context_generation += 2
        outcome = window.runner.complete_one()
        assert outcome["status"] == "blocked"
        assert not window.runner.tasks and not session.dispatches
        assert results == refreshes == []
    finally:
        service.close()


def test_notice_refresh_before_queued_work_blocks_even_with_same_catalogue_row(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        window, errors, results, refreshes = view(service, session, account, item)
        request(window, item)
        item.record_id = "book-other"
        outcome = window.runner.complete_one()
        assert outcome["status"] == "blocked"
        assert not window.runner.tasks and not session.dispatches
        assert results == refreshes == []
    finally:
        service.close()


def test_title_changed_after_click_blocks_before_claim(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        window, errors, results, refreshes = view(service, session, account, item)
        request(window, item)
        item.title = "Another edition with same resource id"
        outcome = window.runner.complete_one()
        assert outcome["status"] == "blocked"
        assert not window.runner.tasks and not session.dispatches
        assert results == refreshes == []
    finally:
        service.close()


def test_scope_change_after_queued_click_blocks_worker(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        window, errors, results, refreshes = view(service, session, account, item)
        request(window, item)
        window._account_context_generation += 1
        outcome = window.runner.complete_one()
        assert outcome["status"] == "blocked"
        assert session.reads == session.dispatches == []
        assert not results and not refreshes
    finally:
        service.close()


def test_core_epoch_change_after_click_does_not_claim_or_post(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        window, errors, results, refreshes = view(service, session, account, item)
        request(window, item)
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
        request(window, item)
        session.failure = TimeoutError("https://pret.bnf.fr/sensitive?csrf=SECRET")
        outcome = window.runner.complete_one()
        assert outcome["status"] == "verification_unavailable"
        assert len(session.dispatches) == 1
        assert errors and "incertain" in errors[0][0]
        assert "SECRET" not in str(errors)
        assert not results and not refreshes
    finally:
        service.close()


def test_pre_post_unavailability_is_explicitly_no_request_and_retryable(tmp_path):
    from cifereca.plugin_api import BorrowRequestNotSubmitted
    from cifereca.core.borrow_action_scope import borrow_claim_key

    service, session, account, item = setup(tmp_path)
    try:
        window, errors, results, refreshes = view(service, session, account, item)
        request(window, item)
        session.failure = BorrowRequestNotSubmitted(
            "PRIVATE_AUTH_TOKEN_DO_NOT_LEAK")
        outcome = window.runner.complete_one()
        assert outcome["status"] == "blocked"
        assert outcome["reason_code"] == "borrow_not_submitted"
        assert service.storage.loan_borrow_state(borrow_claim_key(account, item)) is None
        assert service.can_borrow_bnf(item) is True
        assert len(errors) == 1
        assert "Aucune demande de prêt" in errors[0][0]
        assert "nouvelle demande volontaire" in errors[0][0]
        assert "PRIVATE_AUTH_TOKEN" not in str(errors)
        assert results == refreshes == []
    finally:
        service.close()


def test_no_confusion_of_selected_account_by_draft_copy(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        window, errors, results, refreshes = view(service, session, account, item)
        request(window, item)
        service.accounts = [replace(account)]
        outcome = window.runner.complete_one()
        assert outcome["status"] == "blocked"
        assert session.reads == session.dispatches == []
        assert not results
    finally:
        service.close()
