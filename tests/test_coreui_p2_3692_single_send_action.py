"""#3692 / P1 #3275: one contextual local-send action for 0/1/N recipients.

The selected placement is an already-verified local source. These tests
exercise the actual CoreUI action composition and deferred callbacks without
GTK, supplier I/O or filesystem mutations. GTK itself is checked by the
existing MainWindow E2E smoke triggered by GUI-path changes.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.gui.local_book_action_controller import add_local_book_actions
from tests.test_core_ui_1129_local_book_actions import _environment


def _display(*, identities=("alice", "bob", "charlie"), owned=(),
             attested=False):
    window, item, detail, actions, events, queued, launched, deps = (
        _environment(identities=identities, attested=attested)
    )
    window.service.owned.update(owned)
    add_local_book_actions(window, detail, item, **deps)
    return window, actions, events, queued


def _ids(actions):
    return [getattr(a, "_cifereca_action_id", None) for a in actions]


def test_no_eligible_destination_does_not_render_send_action():
    _, actions, _, queued = _display(identities=("alice",))
    assert _ids(actions) == ["open-local"]
    assert queued == []

    _, actions, _, queued = _display(owned=("bob", "charlie"))
    assert _ids(actions) == ["open-local"]
    assert queued == []


def test_one_eligible_destination_is_one_direct_button():
    _, actions, _, queued = _display(owned=("bob",))
    assert _ids(actions) == ["open-local", "send-local:charlie"]
    assert actions[1].label == "Envoyer à Charlie"
    assert not hasattr(actions[1], "popover")
    actions[1].click()
    args, options = queued.pop()
    assert options["control"] is actions[1]
    assert options["kind"] == "materialize"
    assert args[1]()["status"] == "complete"


def test_multiple_destinations_have_one_action_and_exact_menu_choices():
    window, actions, events, queued = _display()
    assert _ids(actions) == ["open-local", "send-local-menu"]
    menu = actions[1]
    assert menu.label == "Envoyer à"
    assert menu.tooltip == "Choisir le profil auquel envoyer ce livre"
    bob, charlie = menu.popover.child.children
    assert (bob.label, charlie.label) == ("Bob", "Charlie")
    assert _ids((bob, charlie)) == ["send-local:bob", "send-local:charlie"]
    assert not queued
    charlie.click()
    assert len(queued) == 1
    args, options = queued[0]
    assert options["control"] is charlie
    assert args[1]()["status"] == "complete"
    assert events[-2:] == [
        ("path", "placement", "alice"),
        ("send", "placement", "charlie"),
    ]
    # A pending copy blocks all additional recipient selections.
    bob.click()
    charlie.click()
    assert len(queued) == 1
    args[2]({
        "status": "complete", "target_identity_id": "charlie",
        "placement_id": "new-placement", "path": "/library/copied.epub",
    })
    assert events[-3:] == [
        ("activity", "Livre envoyé"), ("recent",), ("library", False),
    ]
    bob.click()
    assert len(queued) == 2
    assert queued[1][0][1]()["target_identity_id"] == "bob"
    assert events[-1] == ("send", "placement", "bob")


def test_identity_switch_and_revoked_menu_ignore_old_callbacks():
    window, actions, _, queued = _display()
    bob = actions[1].popover.child.children[0]
    window.service.current_identity = NS(identity_id="charlie")
    bob.click()
    assert queued == []
    window.service.current_identity = NS(identity_id="alice")
    actions[1]._cifereca_action_context_revoked = True
    bob.click()
    assert queued == []


def test_pending_worker_revalidates_context_and_result_lifecycle():
    window, actions, events, queued = _display()
    bob = actions[1].popover.child.children[0]
    bob.click()
    args, options = queued[0]
    window.service.current_identity = NS(identity_id="charlie")
    # The already-clicked filesystem A→B copy runs, even while the active
    # GTK identity is C. Its completion must not repaint C.
    receipt = args[1]()
    assert receipt["target_identity_id"] == "bob"
    assert ("send", "placement", "bob") in events
    args[2](receipt)
    assert not any(e[0] == "activity" for e in events)
    window.service.current_identity = NS(identity_id="alice")
    # The old widget callback cannot be resurrected after revocation.
    bob._cifereca_action_context_revoked = True
    bob.click()
    assert len(queued) == 1


def test_error_is_explicit_and_never_contains_private_exception_data():
    window, actions, events, queued = _display()
    bob = actions[1].popover.child.children[0]
    bob.click()
    failure = RuntimeError("https://private.invalid/borrow?token=secret")
    queued[0][0][3](failure)
    assert events[-1] == ("error", "Envoi du livre impossible")
    assert "private.invalid" not in repr(events)
    assert len(queued) == 1
    # Failure re-enables a later deliberate retry.
    bob.click()
    assert len(queued) == 2


def test_unchanged_core_result_is_not_presented_as_new_copy():
    window, actions, events, queued = _display()
    bob = actions[1].popover.child.children[0]
    bob.click()
    queued[0][0][2]({
        "status": "unchanged", "target_identity_id": "bob",
        "placement_id": "new-placement", "path": "/library/copied.epub",
    })
    assert ("activity", "Livre déjà présent dans ce profil") in events


def test_local_possession_filters_menu_without_conflating_other_edition():
    window, actions, events, queued = _display(owned=("bob",))
    assert _ids(actions) == ["open-local", "send-local:charlie"]
    window.service.owned.clear()
    add_local_book_actions(
        window, NS(add_actions=lambda buttons: actions.extend(buttons)),
        {"placement_id": "placement", "identity_id": "alice",
         "provider_id": "kobo", "source_credential": "credential-a",
         "item_id": "other-edition"},
        **_environment()[7],
    )
    assert _ids(actions[-2:]) == ["open-local", "send-local-menu"]
