"""BnF loan action opt-in UX: two independently confirmed operations."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.gui.explicit_loan_acsm_controller import prepare_explicit_loan_acsm


class Dialog:
    shown = []
    @classmethod
    def new(cls, parent, title, message):
        obj = cls()
        obj.title, obj.message, obj.on_response = title, message, None
        cls.shown.append(obj)
        return obj
    def add_response(self, name, label):
        pass
    def connect(self, signal, callback):
        assert signal == "response"
        self.on_response = callback
    def present(self):
        pass
    def respond(self, response):
        self.on_response(self, response)


def window():
    operations = []
    service = NS(
        current_identity=NS(identity_id="default"),
        stage_bnf_loan_acsm=Mock(return_value={
            "status": "candidate_acsm", "path": "/private/staged.acsm",
        }),
        import_bnf_loan_acsm=Mock(return_value={"status": "complete"}),
    )
    w = NS(
        service=service, _closed=False, _identity_context_generation=1,
        _account_context_generation=2, show_error=Mock(),
        show_attention=Mock(), _activity_result=Mock(), refresh_library=Mock(),
    )
    def submit(label, work, ok, error=None, **kw):
        operations.append((label, kw.get("kind")))
        try:
            ok(work())
        except Exception as exc:
            if error is not None:
                error(exc)
            else:
                raise
    w.runner=NS(submit=submit)
    return w, operations


def start(w):
    Dialog.shown.clear()
    loan = NS(source_provider="bnf-pret", identity_id="default", title="Fiction", id="loan-a", record_id="book-a", source_credential="card-a", source_account_id="reader", state="active", medium="digital")
    prepare_explicit_loan_acsm(w, loan, None, Adw=NS(MessageDialog=Dialog),
                               TaskPriority=NS(USER_ACTION=1),
                               stage=w.service.stage_bnf_loan_acsm,
                               importer=w.service.import_bnf_loan_acsm)
    return loan


def test_cancel_retrieval_never_starts_http_or_adept():
    w, actions = window()
    start(w)
    assert len(Dialog.shown) == 1
    Dialog.shown[0].respond("cancel")
    w.service.stage_bnf_loan_acsm.assert_not_called()
    w.service.import_bnf_loan_acsm.assert_not_called()
    assert actions == []


def test_retrieval_does_not_automatically_import_without_second_consent():
    w, actions = window()
    loan = start(w)
    Dialog.shown[0].respond("get")
    w.service.stage_bnf_loan_acsm.assert_called_once_with(
        loan, confirmed=True, expected_identity_id="default")
    assert len(Dialog.shown) == 2
    w.service.import_bnf_loan_acsm.assert_not_called()
    Dialog.shown[1].respond("cancel")
    w.service.import_bnf_loan_acsm.assert_not_called()
    assert actions == [("Récupération du jeton ACSM…", "loan-acquisition")]


def test_double_consent_imports_via_manual_adept_without_creating_bnf_right():
    w, actions = window()
    start(w)
    Dialog.shown[0].respond("get")
    Dialog.shown[1].respond("import")
    kwargs = w.service.import_bnf_loan_acsm.call_args
    assert kwargs.args[0].name == "staged.acsm"
    assert kwargs.kwargs["expected_identity_id"] == "default"
    assert kwargs.kwargs["title"] == "Fiction"
    assert kwargs.kwargs["loan"].id == "loan-a"
    assert actions == [
        ("Récupération du jeton ACSM…", "loan-acquisition"),
        ("Import ADEPT du prêt…", "acsm-import"),
    ]
    w.refresh_library.assert_called_once_with(False)


def test_switching_identity_revokes_pending_first_and_second_consent():
    w, _ = window()
    start(w)
    w.service.current_identity = NS(identity_id="another")
    Dialog.shown[0].respond("get")
    w.service.stage_bnf_loan_acsm.assert_not_called()
    w, _ = window()
    start(w)
    Dialog.shown[0].respond("get")
    w._identity_context_generation += 1
    Dialog.shown[1].respond("import")
    w.service.import_bnf_loan_acsm.assert_not_called()


def test_refuse_wrong_active_identity_before_presenting_action():
    w, actions = window()
    w.service.current_identity=NS(identity_id="other")
    start(w)
    assert not Dialog.shown
    w.show_error.assert_called_once()
    assert not actions


def test_confirmation_is_bound_to_original_loan_even_after_mutable_list_refresh():
    w, actions = window()
    selection = start(w)
    selection.id = "loan-other"
    selection.record_id = "book-other"
    selection.title = "Un autre titre"
    Dialog.shown[0].respond("get")
    submitted = w.service.stage_bnf_loan_acsm.call_args
    assert submitted.kwargs == {
        "confirmed": True, "expected_identity_id": "default",
    }
    assert submitted.args[0].id == "loan-a"
    assert submitted.args[0].record_id == "book-a"
    Dialog.shown[1].respond("import")
    assert w.service.import_bnf_loan_acsm.call_args.kwargs["title"] == "Fiction"


def test_both_dialogs_reject_replayed_positive_responses():
    w, actions = window()
    start(w)
    Dialog.shown[0].respond("get")
    Dialog.shown[0].respond("get")
    assert w.service.stage_bnf_loan_acsm.call_count == 1
    assert len(Dialog.shown) == 2
    Dialog.shown[1].respond("import")
    Dialog.shown[1].respond("import")
    assert w.service.import_bnf_loan_acsm.call_count == 1
    assert actions == [
        ("Récupération du jeton ACSM…", "loan-acquisition"),
        ("Import ADEPT du prêt…", "acsm-import"),
    ]


def test_stage_error_explains_safe_code_without_starting_adept():
    from cifereca.core.diagnostic_explicit_loan_action import LoanActionError
    w,_=window()
    w.service.stage_bnf_loan_acsm.side_effect=LoanActionError('bnf_access_denied')
    start(w)
    Dialog.shown[0].respond('get')
    message,title=w.show_error.call_args.args
    assert "L'accès au jeton est refusé" in message and 'Code : bnf_access_denied' in message
    assert '\n' not in message  # Keep the shared activity banner on one paragraph.
    assert title=='Téléchargement ACSM'
    w.service.import_bnf_loan_acsm.assert_not_called()
    assert len(Dialog.shown)==1


def test_failed_activity_message_says_no_get_was_attempted():
    from cifereca.gui.explicit_loan_acsm_controller import action_error_message
    from cifereca.core.diagnostic_explicit_loan_action import LoanActionError
    for code in ('bnf_activity_auth_required',
                 'bnf_activity_http_unavailable',
                 'bnf_activity_unrecognized_loans',
                 'bnf_activity_unavailable'):
        message=action_error_message(LoanActionError(code))
        assert 'Aucun GET ACSM' in message
        assert 'Code : '+code in message
        assert '\\n' not in message


def test_import_error_is_distinct_and_never_formats_private_exception():
    from cifereca.gui.explicit_loan_acsm_controller import action_error_message
    w,_=window()
    w.service.import_bnf_loan_acsm.side_effect=RuntimeError('SECRET token=private')
    start(w)
    Dialog.shown[0].respond('get')
    Dialog.shown[1].respond('import')
    message,title=w.show_error.call_args.args
    assert 'ADEPT a échoué' in message and 'jeton est conservé' in message
    assert 'SECRET' not in message and title=='Import ADEPT'
    class Hostile:
        def __str__(self):raise AssertionError('private exception must not be formatted')
    assert 'Code : loan_acsm_action_failed' in action_error_message(Hostile())


def test_late_failure_does_not_pop_up_in_another_identity():
    w,_=window()
    pending=[]
    w.runner=NS(submit=lambda *args,**kwargs:pending.append(args))
    start(w)
    Dialog.shown[0].respond('get')
    w._identity_context_generation+=1
    pending[0][3](RuntimeError('private error'))
    w.show_error.assert_not_called()


def test_synthetic_lender_uses_same_two_consent_contract_without_supplier_dispatch():
    w, actions = window()
    Dialog.shown.clear()
    loan = NS(
        source_provider="synthetic-lender", identity_id="default",
        source_credential="card", source_account_id="borrower",
        id="synthetic-loan", record_id="synthetic-edition",
        state="active", medium="digital", title="Synthetic",
    )
    prepare_explicit_loan_acsm(
        w, loan, None, Adw=NS(MessageDialog=Dialog),
        TaskPriority=NS(USER_ACTION=1),
        stage=w.service.stage_bnf_loan_acsm,
        importer=w.service.import_bnf_loan_acsm,
    )
    assert len(Dialog.shown) == 1
    Dialog.shown[0].respond("get")
    assert w.service.stage_bnf_loan_acsm.call_args.args[0].source_provider == "synthetic-lender"
    assert len(Dialog.shown) == 2
    Dialog.shown[1].respond("import")
    assert w.service.import_bnf_loan_acsm.call_count == 1
    assert actions == [
        ("Récupération du jeton ACSM…", "loan-acquisition"),
        ("Import ADEPT du prêt…", "acsm-import"),
    ]


def test_service_replacement_between_consent_and_work_cannot_dispatch():
    w, _ = window()
    start(w)
    previous = w.service
    w.service = NS(**vars(previous))
    Dialog.shown[0].respond("get")
    previous.stage_bnf_loan_acsm.assert_not_called()
    previous.import_bnf_loan_acsm.assert_not_called()


def test_missing_actions_and_malformed_loan_fail_closed_before_any_dialog():
    w, _ = window()
    Dialog.shown.clear()
    loan = NS(identity_id="default", source_provider="synthetic")
    prepare_explicit_loan_acsm(
        w, loan, None, Adw=NS(MessageDialog=Dialog),
        TaskPriority=NS(USER_ACTION=1),
        stage=w.service.stage_bnf_loan_acsm,
        importer=w.service.import_bnf_loan_acsm,
    )
    assert not Dialog.shown
    assert not w.service.stage_bnf_loan_acsm.called
    assert not w.service.import_bnf_loan_acsm.called
    prepare_explicit_loan_acsm(
        w, loan, None, Adw=NS(MessageDialog=Dialog),
        TaskPriority=NS(USER_ACTION=1),
        stage=None, importer=None,
    )
    assert not Dialog.shown
