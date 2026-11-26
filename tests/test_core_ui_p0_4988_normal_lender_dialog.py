"""Normal lender choice requires distinct consent and a current UI scope."""
from dataclasses import replace
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


def setup(*, multiple=True):
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

    rows = (candidate,)
    if multiple:
        second_account = replace(candidate._account, identity_id="carol", unique_id="carol-account")
        rows += (replace(candidate, label="Carol", lender_identity_id="carol",
                         _account=second_account),)
    args = dict(item=w.notice, rows=rows, current=current,
                service=service, identity=service.current_identity, identity_id="bob",
                account_epoch=3, source_epoch=5, Adw=NS(MessageDialog=NS(new=new)),
                TaskPriority=NS(USER_ACTION="user"))
    return panel, args, dialogs


def test_choice_and_confirmation_are_separate_and_submit_only_once():
    panel, args, dialogs = setup()
    window = panel.window
    assert present_normal_lender_choice(panel, **args)
    assert not present_normal_lender_choice(panel, **args)
    assert dialogs[0].responses == {
        "cancel": "Annuler", "choice-0": "Alice", "choice-1": "Carol",
    }
    dialogs[0].respond("choice-1")
    assert "Aucune copie" in dialogs[1].body
    window.runner.submit.assert_not_called()
    dialogs[1].respond("borrow")
    dialogs[1].respond("borrow")
    window.runner.submit.assert_called_once()
    _, work, done, _ = window.runner.submit.call_args.args
    result = work()
    execute = args["service"].execute_normal_lender_borrow_for_current_identity
    execute.assert_called_once_with(
        captured_candidate=args["rows"][1], exact_notice=args["item"],
        expected_requesting_identity_id="bob", expected_requesting_identity=args["identity"],
        expected_context_epoch=3, expected_source_epoch=5, confirmed=True,
        captured_roster=args["rows"],
    )
    done(result)
    done(result)
    window._activity_result.assert_called_once()
    assert "aucune copie" in window._activity_result.call_args.args[0]
    assert window._cross_profile_borrow_pending is None


@pytest.mark.parametrize("decision", ["borrow", "cancel", "obsolete"])
def test_single_lender_opens_confirmation_directly_and_still_requires_consent(decision):
    panel, args, dialogs = setup(multiple=False)
    window = panel.window
    execute = args["service"].execute_normal_lender_borrow_for_current_identity

    assert present_normal_lender_choice(panel, **args)
    assert len(dialogs) == 1
    assert dialogs[0].responses == {"cancel": "Annuler", "borrow": "Emprunter sur ce profil"}
    assert "Alice" in dialogs[0].body
    window.runner.submit.assert_not_called()
    execute.assert_not_called()

    if decision == "obsolete":
        args["item"].record_id += "-other"
    dialogs[0].respond("cancel" if decision == "cancel" else "borrow")
    if decision == "borrow":
        window.runner.submit.assert_called_once()
        _, work, done, _ = window.runner.submit.call_args.args
        done(work())
        assert execute.call_args.kwargs["captured_candidate"] is args["rows"][0]
    else:
        window.runner.submit.assert_not_called()
        execute.assert_not_called()
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


@pytest.mark.parametrize("phase", ["before_dialog", "before_choice", "before_confirmation", "queued"])
def test_in_place_notice_change_revokes_lender_choice(phase):
    panel, args, dialogs = setup()
    window = panel.window
    notice = args["item"]

    def mutate():
        notice.record_id += "-other"

    if phase == "before_dialog":
        mutate()
        assert not present_normal_lender_choice(panel, **args)
        assert dialogs == []
    else:
        assert present_normal_lender_choice(panel, **args)
        if phase == "before_choice":
            mutate()
        dialogs[0].respond("choice-0")
        if phase == "before_choice":
            assert len(dialogs) == 1
        else:
            if phase == "before_confirmation":
                mutate()
            dialogs[1].respond("borrow")
            if phase == "queued":
                _, work, done, _ = window.runner.submit.call_args.args
                mutate()
                assert work() == {"status": "blocked"}
                done({"status": "blocked"})
        assert window._cross_profile_borrow_pending is None
    if phase != "queued":
        window.runner.submit.assert_not_called()
    args["service"].execute_normal_lender_borrow_for_current_identity.assert_not_called()


def test_worker_retained_after_submit_exception_cannot_execute():
    panel, args, dialogs = setup(multiple=False)
    window = panel.window
    retained = []

    def refuse(_label, work, done, fail, **kwargs):
        retained.append((work, done, fail))
        raise RuntimeError("submission refused")

    window.runner.submit.side_effect = refuse
    assert present_normal_lender_choice(panel, **args)
    dialogs[0].respond("borrow")
    assert window._cross_profile_borrow_pending is None
    work, done, fail = retained[0]
    # A new consent dialog does not renew ownership of old queued work.
    assert present_normal_lender_choice(panel, **args)
    assert work() == {"status": "blocked"}
    done(None)
    fail(None)
    args["service"].execute_normal_lender_borrow_for_current_identity.assert_not_called()
    assert window._cross_profile_borrow_pending is not None
    dialogs[-1].respond("cancel")
    assert window._cross_profile_borrow_pending is None


@pytest.mark.parametrize("completion_first", [False, True])
def test_confirmed_worker_dispatch_is_one_shot(completion_first):
    panel, args, dialogs = setup(multiple=False)
    window = panel.window
    assert present_normal_lender_choice(panel, **args)
    dialogs[0].respond("borrow")
    _, work, done, _ = window.runner.submit.call_args.args
    result = work()
    if completion_first:
        done(result)
    assert work() == {"status": "blocked"}
    args["service"].execute_normal_lender_borrow_for_current_identity.assert_called_once()
    done(result)
    window._activity_result.assert_called_once()
    assert window._cross_profile_borrow_pending is None


@pytest.mark.parametrize("replace_detail", [False, True])
def test_homonymous_lender_choices_and_exact_detail_worker_fence(replace_detail):
    panel, args, dialogs = setup()
    first, second = args["rows"]
    args["rows"] = (first, replace(second, label=first.label))
    assert present_normal_lender_choice(panel, **args)
    labels = dialogs[0].responses
    assert labels["choice-0"] != labels["choice-1"]
    dialogs[0].respond("choice-1")
    assert labels["choice-1"] in dialogs[1].body
    dialogs[1].respond("borrow")
    _, work, done, _ = panel.window.runner.submit.call_args.args
    if replace_detail:
        panel._rendered_search_detail = (args["item"], object())
    result = work()
    execute = args["service"].execute_normal_lender_borrow_for_current_identity
    if replace_detail:
        assert result == {"status": "blocked"}
        execute.assert_not_called()
    else:
        assert execute.call_args.kwargs["captured_candidate"] is args["rows"][1]
    done(result)


@pytest.mark.parametrize("tamper", ["account", "label", "session"])
def test_unselected_lender_change_revokes_entire_n_way_chooser(tamper):
    panel, args, dialogs = setup(multiple=True)
    assert present_normal_lender_choice(panel, **args)
    assert len(dialogs) == 1
    second = args["rows"][1]
    if tamper == "account":
        second._account.unique_id += "-rebound"
    elif tamper == "label":
        object.__setattr__(second, "label", "Changed without new receipt")
    else:
        # A presenter must not silently accept a swapped original session.
        object.__setattr__(second, "_session", object())
    # The chosen first lender still matches, but the N-way receipt no longer does.
    dialogs[0].respond("choice-0")
    assert len(dialogs) == 1
    assert panel.window._cross_profile_borrow_pending is None
    panel.window.runner.submit.assert_not_called()
    args["service"].execute_normal_lender_borrow_for_current_identity.assert_not_called()


@pytest.mark.parametrize("phase", ["confirmation", "queued_worker"])
@pytest.mark.parametrize("tamper", ["account", "label", "session"])
def test_n_way_roster_revocation_after_selection_blocks_consent_or_dispatch(phase, tamper):
    """A still-valid selected lender must not outlive a changed N-way receipt."""
    panel, args, dialogs = setup(multiple=True)
    window = panel.window
    assert present_normal_lender_choice(panel, **args)
    dialogs[0].respond("choice-0")
    assert len(dialogs) == 2

    if phase == "queued_worker":
        dialogs[1].respond("borrow")
        window.runner.submit.assert_called_once()
        _, work, done, _ = window.runner.submit.call_args.args

    unselected = args["rows"][1]
    if tamper == "account":
        unselected._account.unique_id += "-revoked"
    elif tamper == "label":
        object.__setattr__(unselected, "label", "Reassigned")
    else:
        object.__setattr__(unselected, "_session", object())

    if phase == "confirmation":
        dialogs[1].respond("borrow")
        window.runner.submit.assert_not_called()
    else:
        assert work() == {"status": "blocked"}
        done({"status": "blocked"})

    args["service"].execute_normal_lender_borrow_for_current_identity.assert_not_called()
    assert window._cross_profile_borrow_pending is None
