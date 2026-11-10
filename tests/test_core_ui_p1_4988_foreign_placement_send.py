"""#4988: headless GTK consent for a foreign present Library placement.

A local A placement can be sent to selected B without a new loan. This
tests the UI lease, exact Core arguments, cancellation and absent ABI.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.gui.foreign_placement_send_controller import (
    add_foreign_present_placement_send,
)
from cifereca.gui.local_book_action_controller import add_local_book_actions
from tests.test_core_ui_1129_local_book_actions import _environment


class Dialog:
    def __init__(self):
        self.callback = None
        self.responses = []
        self.shown = False

    def add_response(self, response_id, label):
        self.responses.append((response_id, label))

    def connect(self, name, callback):
        assert name == "response"
        self.callback = callback

    def present(self):
        self.shown = True

    def respond(self, response):
        assert self.callback is not None
        self.callback(self, response)


def setup(*, with_core=True, item=None):
    if item is None:
        item = {
            "placement_id": "exact-placement-A",
            "identity_id": "alice", "artifact_id": "sha256:exact-A",
            "provider_id": "kobo", "item_id": "book",
        }
    window, _row, detail, actions, events, queued, _launched, deps = (
        _environment(item=item)
    )
    window.service.current_identity = NS(identity_id="bob")
    window.service._account_context_epoch = 3
    window.service._source_context_epoch = 7
    window._identity_context_generation = 4
    window._account_context_generation = 8
    called = []
    if with_core:
        def send(**kw):
            called.append(kw)
            return {
                "status": "complete", "loan": "not_attempted",
                "delivery": "present", "transfer": "completed",
                "copy": "created", "retry_borrow": False,
                "reason_code": "independent_local_copy_completed",
            }
        window.service.send_present_placement_copy = send
    return window, item, detail, actions, events, queued, called, deps


def test_foreign_placement_shows_one_action_without_loan_or_provider_call():
    window, row, detail, actions, events, queued, calls, deps = setup()
    def forbidden_owned_lookup(_item):
        raise AssertionError("foreign rights must not be queried from B")
    window.service.owned_library_download_target = forbidden_owned_lookup
    add_local_book_actions(window, detail, row, **deps)
    assert [b._cifereca_action_id for b in actions] == [
        "send-present-foreign-placement",
    ]
    assert "Envoyer une copie" in actions[0].label
    assert calls == [] and queued == []
    assert not any(ev[0] == "path" for ev in events)


def test_confirmed_foreign_copy_executes_once_with_all_exact_scopes():
    window, row, detail, actions, events, queued, calls, deps = setup()
    dialog = Dialog()
    assert add_foreign_present_placement_send(
        window, detail, row, TaskPriority=deps["TaskPriority"],
        dialog_factory=lambda _window: dialog,
    )
    assert not calls and not queued
    actions[0].click()
    assert dialog.shown
    assert "Annuler" in [label for _id, label in dialog.responses]
    assert not queued
    dialog.respond("send")
    assert len(queued) == 1
    dialog.respond("send")  # stale GTK response cannot submit twice
    work, done, failed = queued[0][0][1:4]
    assert not calls
    result = work()
    assert calls == [{
        "source_placement_id": "exact-placement-A",
        "source_identity_id": "alice",
        "source_artifact_id": "sha256:exact-A",
        "requesting_identity_id": "bob",
        "expected_account_context_epoch": 3,
        "expected_source_context_epoch": 7,
        "confirmed": True,
    }]
    done(result)
    assert any(ev[0] == "activity" for ev in events)
    assert any(ev[0] == "library" for ev in events)
    assert not any(ev[0] == "error" for ev in events)


def test_refusing_dialog_never_calls_core_or_queues_worker():
    window, row, detail, actions, _events, queued, calls, deps = setup()
    dialog = Dialog()
    assert add_foreign_present_placement_send(
        window, detail, row, TaskPriority=deps["TaskPriority"],
        dialog_factory=lambda _window: dialog,
    )
    actions[0].click()
    dialog.respond("cancel")
    assert not queued and not calls


def test_aba_and_stale_button_revoke_before_launch_and_execution():
    window, row, detail, actions, _events, queued, calls, deps = setup()
    dialog = Dialog()
    assert add_foreign_present_placement_send(
        window, detail, row, TaskPriority=deps["TaskPriority"],
        dialog_factory=lambda _window: dialog,
    )
    actions[0].click()
    dialog.respond("send")
    work = queued[0][0][1]
    window.service._account_context_epoch += 2
    assert work() == {"status": "blocked"}
    assert calls == []

    other = Dialog()
    window._foreign_placement_send_pending = None
    window.service._account_context_epoch = 3
    more = []
    assert add_foreign_present_placement_send(
        window, NS(add_actions=lambda rows: more.extend(rows)), row,
        TaskPriority=deps["TaskPriority"], dialog_factory=lambda _w: other,
    )
    more[0]._cifereca_action_context_revoked = True
    more[0].click()
    assert not other.shown


def test_absent_core_or_artifact_never_creates_positive_action():
    window, row, detail, actions, _events, queued, called, deps = setup(with_core=False)
    add_local_book_actions(window, detail, row, **deps)
    assert actions == [] and queued == []
    window.service.send_present_placement_copy = lambda **kwargs: None
    row["artifact_id"] = None
    assert not add_foreign_present_placement_send(
        window, detail, row, TaskPriority=deps["TaskPriority"],
        dialog_factory=lambda _window: Dialog(),
    )
    assert actions == []


def test_changed_profile_during_confirmation_does_not_start_copy():
    window, row, detail, actions, _events, queued, calls, deps = setup()
    dialog = Dialog()
    assert add_foreign_present_placement_send(
        window, detail, row, TaskPriority=deps["TaskPriority"],
        dialog_factory=lambda _window: dialog,
    )
    actions[0].click()
    window.service.current_identity = NS(identity_id="alice")
    dialog.respond("send")
    assert not queued and not calls
