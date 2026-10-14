from __future__ import annotations

import ast
from pathlib import Path

from cifereca.gui.book_actions import identity_has_attested_possession
from cifereca.plugin_api import ArtifactPossessionRef


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"


def _possession(
    identity: str,
    *,
    provider: str = "kobo",
    item: str = "book-1",
    kind: str = "owned",
    state: str = "active",
    credential: str = "vault-a",
):
    return ArtifactPossessionRef(
        identity_id=identity,
        provider_id=provider,
        source_item_id=item,
        kind=kind,
        state=state,
        facts={"credential_id": credential} if credential else {},
    )


def test_active_remote_possession_hides_only_matching_destination():
    possessions = (_possession("alice"), _possession("bob", item="other"))
    assert identity_has_attested_possession(
        possessions,
        identity_id="alice",
        provider_id="kobo",
        source_item_id="book-1",
        credential_id="vault-a",
    )
    assert not identity_has_attested_possession(
        possessions,
        identity_id="bob",
        provider_id="kobo",
        source_item_id="book-1",
        credential_id="vault-a",
    )


def test_remote_possession_requires_matching_known_credential():
    possessions = (_possession("alice", credential="vault-b"),)
    assert not identity_has_attested_possession(
        possessions,
        identity_id="alice",
        provider_id="kobo",
        source_item_id="book-1",
        credential_id="vault-a",
    )
    no_credential = (_possession("alice", credential=""),)
    assert not identity_has_attested_possession(
        no_credential,
        identity_id="alice",
        provider_id="kobo",
        source_item_id="book-1",
        credential_id="vault-a",
    )


def test_realized_or_expired_right_does_not_hide_transfer():
    for state in ("realized", "expired", "returned"):
        assert not identity_has_attested_possession(
            (_possession("alice", state=state),),
            identity_id="alice",
            provider_id="kobo",
            source_item_id="book-1",
            credential_id="vault-a",
        )


def test_present_local_or_storage_possession_is_authoritative():
    for kind in ("local", "storage"):
        possession = _possession("alice", kind=kind, state="present", credential="")
        assert identity_has_attested_possession(
            (possession,),
            identity_id="alice",
            provider_id="kobo",
            source_item_id="book-1",
            credential_id="vault-a",
        )


def test_send_actions_consume_fresh_library_read_projection():
    tree = ast.parse(MAIN.with_name("local_book_action_controller.py").read_text(encoding="utf-8"))
    method = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "add_local_book_actions")
    source = ast.unparse(method)
    assert "manager.broker('library.read')" in source
    assert "library_read.book(source_item_id)" in source
    assert "library_read.for_artifact" in source
    assert "identity_has_attested_possession(" in source
    assert "local_placement_for_source(" in source
