"""P1 #4664: shared Search/Recommendations local actions fail closed after a lease change.

The actual Core action controller and real callbacks are executed headlessly:
no GTK, local file launch, provider fetch or storage transfer is performed.
"""
from __future__ import annotations

import pytest

from cifereca.gui.local_book_action_controller import add_local_book_actions
from cifereca.plugin_api import Cancelled
from tests.test_core_ui_1129_local_book_actions import _environment, Button, RouteRequest
from types import SimpleNamespace as NS


def test_old_open_button_is_revoked_after_a_b_a_and_source_reconnect():
    w, item, panel, actions, events, submissions, launched, deps = _environment(
        identities=("alice",),
    )
    w._identity_context_generation = 4
    w.service._source_context_epoch = 10
    add_local_book_actions(w, panel, item, **deps)
    assert len(actions) == 1
    actions[0].click()
    assert launched == ["file:///library/book.epub"]

    w.service.current_identity = NS(identity_id="bob")
    actions[0].click()
    w.service.current_identity = NS(identity_id="alice")
    w._identity_context_generation += 2  # A -> B -> A
    actions[0].click()
    assert len(launched) == 1

    # A newly built button works, but the prior one remains irrevocable.
    add_local_book_actions(w, panel, item, **deps)
    actions[-1].click()
    assert len(launched) == 2
    w.service._source_context_epoch += 1  # credential/session replaced
    actions[-1].click()
    assert len(launched) == 2
    assert submissions == []


def test_deferred_send_and_late_success_cannot_cross_a_b_a():
    w, item, panel, actions, events, submissions, launched, deps = _environment()
    w._identity_context_generation = 1
    add_local_book_actions(w, panel, item, **deps)
    actions[1].click()
    assert len(submissions) == 1
    args, kwargs = submissions[0]
    w.service.current_identity = NS(identity_id="bob")
    w.service.current_identity = NS(identity_id="alice")
    w._identity_context_generation += 2
    with pytest.raises(Cancelled):
        args[1]()
    before = list(events)
    args[2]("complete")
    assert events == before
    assert not any(entry[0] == "send" for entry in events)

    actions[1].click()
    assert len(submissions) == 1


def test_deferred_route_cannot_transfer_after_account_source_epoch_change():
    operations = []
    routing = NS(
        targets=lambda _req: [NS(target=NS(label="Device", target_id="d"))],
        plan=lambda req: operations.append(("plan", req.target_id)) or "plan",
        transfer=lambda plan: operations.append(("transfer", plan)) or "ok",
    )
    manager = NS(broker=lambda kind: routing if kind == "artifact.routing" else None)
    w, item, panel, actions, events, submissions, launched, deps = _environment(
        manager=manager,
    )
    w.service._source_context_epoch = 5
    add_local_book_actions(w, panel, item, **deps)
    button = actions[-1].popover.child.children[0]
    button.click()
    assert len(submissions) == 1
    args, kwargs = submissions[0]
    w.service._source_context_epoch = 6
    with pytest.raises(Cancelled):
        args[1]()
    before = list(events)
    args[2]("ok")
    args[3](ValueError("unavailable"))
    assert events == before and operations == []
    button.click()
    assert len(submissions) == 1


def test_foreign_profile_placement_never_builds_local_action():
    w, item, panel, actions, events, submissions, launched, deps = _environment()
    w.service.current_identity = NS(identity_id="bob")
    add_local_book_actions(w, panel, item, **deps)
    assert actions == []
    assert not any(event[0] == "path" for event in events)
    assert submissions == [] and launched == []
