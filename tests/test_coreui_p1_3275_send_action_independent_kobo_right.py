"""P1 #3275: an independent Kobo purchase at B is not local A→B materialization.

Exercise the actual CoreUI action controller + the actual public possession
predicate; a remote right is not proof of already copied bytes. Synthetic
logical library snapshots, never network or provider acquisition.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.gui.book_actions import identity_has_attested_possession
from cifereca.gui.local_book_action_controller import add_local_book_actions
from cifereca.plugin_api import ArtifactPossessionRef
from tests.test_core_ui_1129_local_book_actions import _environment


def _poss(
    identity, credential, *, kind="owned", state="active", item="book-1",
):
    return ArtifactPossessionRef(
        identity_id=identity, provider_id="kobo", source_item_id=item,
        kind=kind, state=state,
        facts={"credential_id": credential},
    )


def _actions_for(possessions):
    reads = []
    read = NS(book=lambda item_id: (
        reads.append(item_id) or NS(possessions=possessions)
    ))
    manager = NS(broker=lambda name: read if name == "library.read" else None)
    window, item, detail, actions, events, submitted, _launched, deps = (
        _environment(manager=manager, identities=("alice", "bob"))
    )
    item["source_credential"] = "vault-a"
    deps["identity_has_attested_possession"] = identity_has_attested_possession
    add_local_book_actions(window, detail, item, **deps)
    return window, actions, events, submitted, reads


def test_independent_b_purchase_same_product_does_not_hide_explicit_send():
    window, actions, events, submitted, reads = _actions_for((
        _poss("alice", "vault-a"),
        _poss("bob", "vault-b"),  # exact product, DISTINCT B credential
    ))
    assert reads == ["book-1"]
    assert [x._cifereca_action_id for x in actions] == [
        "open-local", "send-local:bob",
    ]
    assert not submitted
    actions[1].click()
    assert len(submitted) == 1
    args, kw = submitted[0]
    assert kw["kind"] == "materialize"
    assert args[0] == "Envoi du fichier local…"
    assert args[1]() == "complete"
    assert events[-2:] == [
        ("path", "placement", "alice"), ("send", "placement", "bob"),
    ]
    # No Kobo service/rights broker call was made by the send action. This
    # simulated Core service contains only materialize_to_identity().


def test_b_local_copy_or_own_local_file_hides_redundant_send():
    for possession in (
        _poss("bob", "vault-b", kind="local", state="present"),
        _poss("bob", "vault-b", kind="storage", state="present"),
    ):
        _window, actions, _events, submitted, _reads = _actions_for((
            _poss("bob", "vault-b", kind="owned"), possession,
        ))
        assert [x._cifereca_action_id for x in actions] == ["open-local"]
        assert not submitted


def test_b_own_remote_right_unverified_or_other_edition_never_hides_send():
    for right in (
        _poss("bob", "vault-b", state="archived"),
        _poss("bob", "vault-b", state="active", item="book-2"),
    ):
        _, actions, _, _, _ = _actions_for((right,))
        assert [x._cifereca_action_id for x in actions] == [
            "open-local", "send-local:bob",
        ]


def test_identical_shared_credential_keeps_existing_redundant_right_policy():
    _, actions, _, submitted, _ = _actions_for((
        _poss("bob", "vault-a"),
    ))
    assert [x._cifereca_action_id for x in actions] == ["open-local"]
    assert submitted == []
