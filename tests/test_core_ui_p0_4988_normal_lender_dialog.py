"""Normal lender choice requires distinct consent and a current UI scope."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.cross_profile_normal_lender_dialog import present_normal_lender_choice
from tests.test_core_ui_p1_4988_possible_lender_detail import view


class Dialog:
    def __init__(self, parent, heading, body):
        self.heading, self.body = heading, body
        self.responses = {}

    def add_response(self, key, label):
        self.responses[key] = label

    def connect(self, event, callback):
        assert event == "response"
        self.callback = callback

    def present(self):
        pass

    def respond(self, key):
        self.callback(self, key)


def setup():
    w, candidate, service, panel, _, _ = view()
    window = panel.window
    dialogs = []
    window.runner = NS(submit=Mock())
    window.show_error = Mock()
    window._activity_result = Mock()
    service.execute_normal_lender_borrow_for_current_identity = Mock(return_value={
        "status": "confirmed", "provider_verified": True, "delivery": "unavailable",
        "transfer": "blocked", "retry_borrow": False,
    })
    current = Mock(return_value=True)

    def new(*args):
        dialog = Dialog(*args)
        dialogs.append(dialog)
        return dialog

    args = dict(item=w.notice, rows=(candidate,), current=current,
                service=service, identity=service.current_identity, identity_id="bob",
                account_epoch=3, source_epoch=5, Adw=NS(MessageDialog=NS(new=new)),
                TaskPriority=NS(USER_ACTION="user"))
    return panel, args, dialogs


def test_choice_and_confirmation_are_separate_and_submit_only_once():
    panel, args, dialogs = setup()
    window = panel.window
    assert present_normal_lender_choice(panel, **args)
    assert not present_normal_lender_choice(panel, **args)
    assert dialogs[0].responses == {"cancel": "Annuler", "choice-0": "Alice"}
    dialogs[0].respond("choice-0")
    assert "Aucune copie" in dialogs[1].body
    window.runner.submit.assert_not_called()
    dialogs[1].respond("borrow")
    dialogs[1].respond("borrow")
    window.runner.submit.assert_called_once()
    _, work, done, _ = window.runner.submit.call_args.args
    result = work()
    execute = args["service"].execute_normal_lender_borrow_for_current_identity
    execute.assert_called_once_with(
        captured_candidate=args["rows"][0], exact_notice=args["item"],
        expected_requesting_identity_id="bob", expected_requesting_identity=args["identity"],
        expected_context_epoch=3, expected_source_epoch=5, confirmed=True,
    )
    done(result)
    done(result)
    window._activity_result.assert_called_once()
    assert "aucune copie" in window._activity_result.call_args.args[0]
    assert window._cross_profile_borrow_pending is None


@pytest.mark.parametrize("where", ["choose", "confirm", "stale", "invalid"])
def test_cancel_or_stale_never_submits(where):
    panel, args, dialogs = setup()
    assert present_normal_lender_choice(panel, **args)
    if where in ("choose", "invalid"):
        dialogs[0].respond("cancel" if where == "choose" else "choice-100")
    else:
        dialogs[0].respond("choice-0")
        if where == "stale":
            args["current"].return_value = False
        dialogs[1].respond("borrow" if where == "stale" else "cancel")
    panel.window.runner.submit.assert_not_called()
    args["service"].execute_normal_lender_borrow_for_current_identity.assert_not_called()
    assert panel.window._cross_profile_borrow_pending is None


def test_replaced_service_before_worker_blocks_and_stale_delivery_is_silent():
    panel, args, dialogs = setup()
    assert present_normal_lender_choice(panel, **args)
    dialogs[0].respond("choice-0")
    dialogs[1].respond("borrow")
    _, work, done, _ = panel.window.runner.submit.call_args.args
    panel.window.service = NS()
    args["current"].return_value = False
    assert work() == {"status": "blocked"}
    args["service"].execute_normal_lender_borrow_for_current_identity.assert_not_called()
    done({"status": "blocked"})
    panel.window.show_error.assert_not_called()
    assert panel.window._cross_profile_borrow_pending is None


def test_empty_choices_never_open_dialog():
    panel, args, dialogs = setup()
    args["rows"] = ()
    assert not present_normal_lender_choice(panel, **args)
    assert dialogs == []
