"""BnF loan action opt-in UX: two independently confirmed operations."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.gui.bnf_acsm_action_controller import prepare_bnf_acsm


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
        import_acsm=Mock(return_value={"status": "complete"}),
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
    loan = NS(source_provider="bnf-pret", identity_id="default", title="Fiction")
    prepare_bnf_acsm(w, loan, None, Adw=NS(MessageDialog=Dialog),
                     TaskPriority=NS(USER_ACTION=1))
    return loan


def test_cancel_retrieval_never_starts_http_or_adept():
    w, actions = window()
    start(w)
    assert len(Dialog.shown) == 1
    Dialog.shown[0].respond("cancel")
    w.service.stage_bnf_loan_acsm.assert_not_called()
    w.service.import_acsm.assert_not_called()
    assert actions == []


def test_retrieval_does_not_automatically_import_without_second_consent():
    w, actions = window()
    loan = start(w)
    Dialog.shown[0].respond("get")
    w.service.stage_bnf_loan_acsm.assert_called_once_with(
        loan, confirmed=True, expected_identity_id="default")
    assert len(Dialog.shown) == 2
    w.service.import_acsm.assert_not_called()
    Dialog.shown[1].respond("cancel")
    w.service.import_acsm.assert_not_called()
    assert actions == [("Récupération du jeton BnF…", "loan-acquisition")]


def test_double_consent_imports_via_manual_adept_without_creating_bnf_right():
    w, actions = window()
    start(w)
    Dialog.shown[0].respond("get")
    Dialog.shown[1].respond("import")
    kwargs = w.service.import_acsm.call_args
    assert kwargs.args[0].name == "staged.acsm"
    assert kwargs.kwargs == {
        "identity_id":"default", "provider_id":"manual", "title":"Fiction",
    }
    assert actions == [
        ("Récupération du jeton BnF…", "loan-acquisition"),
        ("Import ADEPT du prêt BnF…", "acsm-import"),
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
    w.service.import_acsm.assert_not_called()


def test_refuse_wrong_active_identity_before_presenting_action():
    w, actions = window()
    w.service.current_identity=NS(identity_id="other")
    start(w)
    assert not Dialog.shown
    w.show_error.assert_called_once()
    assert not actions
