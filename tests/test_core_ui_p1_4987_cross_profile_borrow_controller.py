"""#4987: GTK lender selection is opt-in, explicit and scope-revocable.

Executes the actual controller with a fake dialog and deferred TaskRunner.
No GI, network, provider methods, profile switch or licensed transfer.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.borrow_for_identity_live import ObservedBorrowOffer
from cifereca.gui.cross_profile_borrow_controller import (
    present_cross_profile_lender_choice as present,
)
from tests.test_core_p1_4662_cross_profile_borrow_preview import (
    project, witness,
)
from tests.test_core_p1_4662_live_lender_preview import _candidate


class Dialog:
    made = []

    def __init__(self, title, description):
        self.title = title
        self.description = description
        self.responses = {}
        self.handler = None
        self.shown = False
        Dialog.made.append(self)

    @classmethod
    def new(cls, _window, title, description):
        return cls(title, description)

    def add_response(self, code, label):
        self.responses[code] = label

    def connect(self, event, callback):
        assert event == "response"
        self.handler = callback

    def present(self):
        self.shown = True

    def respond(self, response):
        assert self.shown
        self.handler(self, response)


class Runner:
    def __init__(self):
        self.jobs = []

    def submit(self, title, work, done, failed, **kw):
        self.jobs.append((title, work, done, failed, kw))


def setup(*, multiple=False):
    Dialog.made.clear()
    item = _candidate().notice
    evidence = (_candidate(),)
    identity = NS(identity_id="bob")
    preview = project(
        witness("alice"),
        *((witness("charlie", credential="card-C") ,) if multiple else ()),
    )
    logs, calls, attempts = [], [], []
    service = NS(
        current_identity=identity, _account_context_epoch=4,
        _source_context_epoch=9,
        preview_borrow_for_current_identity=lambda **kw: preview,
        revalidate_borrow_lender_choice=lambda **kw: (
            calls.append(("revalidate", kw["selected_option_id"]))
            or NS(status="current", executable=False)
        ),
        execute_cross_profile_borrow=lambda **kw: (
            attempts.append(kw)
            or {
                "status": "confirmed", "provider_verified": True,
                "transfer": "blocked", "delivery": "unavailable",
                "retry_borrow": False,
            }
        ),
    )
    panel = NS(_rendered_search_detail=(item, object()))
    window = NS(
        service=service, search_page=NS(detail=panel),
        _closed=False,
        _cross_profile_borrow_pending=None,
        _identity_context_generation=2, _account_context_generation=3,
        _selected_search_id="selected", _search_detail_request_generation=8,
        runner=Runner(), _activity_result=lambda msg: logs.append(("info", msg)),
        show_error=lambda msg, title: logs.append(("error", msg, title)),
    )
    def show():
        return present(
            window, expected_item=item, expected_edition_id="edition-1",
            observations=evidence, Adw=NS(MessageDialog=Dialog),
            TaskPriority=NS(USER_ACTION=1),
        )
    return NS(
        service=service, window=window, item=item, evidence=evidence,
        preview=preview, show=show, logs=logs, calls=calls,
        attempts=attempts, identity=identity,
    )


def test_explicit_choice_then_separate_confirmation_then_worker_only():
    h = setup(multiple=True)
    assert h.show() is True
    first = Dialog.made[0]
    assert "choice-0" in first.responses
    assert "choice-1" in first.responses
    assert "card-" not in " ".join(first.responses.values())
    assert h.attempts == [] and h.window.runner.jobs == []
    first.respond("choice-1")
    assert len(Dialog.made) == 2
    assert h.window.runner.jobs == [] and h.attempts == []
    dialog = Dialog.made[-1]
    assert "transfert" in dialog.description
    assert "borrow" in dialog.responses
    dialog.respond("borrow")
    assert len(h.window.runner.jobs) == 1
    assert h.attempts == []
    title, work, done, failed, kw = h.window.runner.jobs[0]
    assert kw["kind"] == "borrow"
    result = work()
    assert result["status"] == "confirmed"
    assert len(h.attempts) == 1
    assert h.attempts[0]["confirmed"] is True
    assert h.attempts[0]["selected_option_id"] == h.preview.options[1].option_id
    assert h.attempts[0]["observations"] == h.evidence
    done(result)
    assert h.window._cross_profile_borrow_pending is None
    assert len(h.logs) == 1 and "transfert" in h.logs[0][1]
    assert h.service.current_identity is h.identity


@pytest.mark.parametrize("cancel_at", ["choose", "confirm"])
def test_cancel_never_schedules_mutator(cancel_at):
    h = setup()
    assert h.show()
    if cancel_at == "confirm":
        Dialog.made[0].respond("choice-0")
    Dialog.made[-1].respond("cancel")
    assert h.window.runner.jobs == []
    assert h.attempts == []
    assert h.window._cross_profile_borrow_pending is None
    assert h.logs == []


@pytest.mark.parametrize("change", [
    "identity_object", "identity_epoch", "account_epoch",
    "source_epoch", "selection", "detail_epoch", "different_item",
])
def test_rebound_ui_between_choice_and_confirm_never_schedules(change):
    h = setup()
    assert h.show()
    if change == "identity_object":
        h.service.current_identity = NS(identity_id=h.identity.identity_id)
    elif change == "identity_epoch":
        h.window._identity_context_generation += 1
    elif change == "account_epoch":
        h.window._account_context_generation += 1
    elif change == "source_epoch":
        h.service._source_context_epoch += 1
    elif change == "selection":
        h.window._selected_search_id = "new-search"
    elif change == "detail_epoch":
        h.window._search_detail_request_generation += 1
    else:
        h.window.search_page.detail._rendered_search_detail = (
            replace(h.item), object(),
        )
    Dialog.made[0].respond("choice-0")
    assert len(Dialog.made) == 1
    assert h.attempts == []
    assert h.window.runner.jobs == []
    assert h.window._cross_profile_borrow_pending is None


def test_rebinding_after_confirmation_but_before_worker_fails_closed():
    h = setup()
    assert h.show()
    Dialog.made[0].respond("choice-0")
    Dialog.made[1].respond("borrow")
    job = h.window.runner.jobs[0]
    h.service.current_identity = NS(identity_id="bob")
    result = job[1]()
    assert result["status"] == "blocked"
    assert h.attempts == []
    job[2](result)
    assert h.logs == []
    assert h.window._cross_profile_borrow_pending is None


def test_stale_choice_or_second_click_cannot_call_mutator():
    h = setup()
    assert h.show()
    assert h.show() is False
    h.service.revalidate_borrow_lender_choice = lambda **_kw: NS(
        status="revoked", executable=False,
    )
    Dialog.made[0].respond("choice-0")
    assert h.window.runner.jobs == [] and h.attempts == []
    assert h.window._cross_profile_borrow_pending is None


def test_no_observation_or_core_executor_never_displays_choice():
    h = setup()
    delattr(h.service, "execute_cross_profile_borrow")
    assert h.show() is False and Dialog.made == []
    h = setup()
    assert present(
        h.window, expected_item=h.item, expected_edition_id="edition-1",
        observations=(), Adw=NS(MessageDialog=Dialog),
        TaskPriority=NS(USER_ACTION=1),
    ) is False
    assert Dialog.made == []


def test_unknown_provider_result_displays_only_public_status_without_transfer():
    h = setup()
    h.service.execute_cross_profile_borrow = lambda **kw: (
        h.attempts.append(kw)
        or {"status": "verification_unavailable",
            "reason_code": "/private/credential?token=SECRET"}
    )
    assert h.show()
    Dialog.made[0].respond("choice-0")
    Dialog.made[1].respond("borrow")
    job = h.window.runner.jobs[0]
    job[2](job[1]())
    assert len(h.attempts) == 1
    assert len(h.logs) == 1
    assert h.logs[0][0] == "error"
    assert "SECRET" not in repr(h.logs)
    assert "incertain" in h.logs[0][1]
    assert h.window._cross_profile_borrow_pending is None
