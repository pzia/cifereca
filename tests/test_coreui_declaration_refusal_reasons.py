"""#2842: private declaration refusal UX never exposes unchecked exception text."""
from __future__ import annotations

import json
import pytest

from cifereca.core import library_declared_links as engine
from cifereca.gui.library_declaration_actions import (
    declaration_refusal_message,
)
from tests.test_library_declared_links import rig
from tests.test_library_declared_link_inspection import selected


@pytest.mark.parametrize("reason,fragment", [
    ("declaration_conflict", "autre déclaration"),
    ("file_revalidation_failed", "fichier a changé"),
    ("right_scope_changed", "droit"),
    ("placement_conflict", "placement SQLite"),
    ("catalogue_conflict", "catalogue"),
    ("preview_changed", "prévisualisation"),
    ("account_scope_changed", "compte"),
    ("declaration_store_invalid", "historique"),
])
def test_closed_reason_yields_useful_guidance_without_private_details(reason, fragment):
    text = declaration_refusal_message(reason)
    assert fragment.lower() in text.lower()
    assert "PRIVATE" not in text


@pytest.mark.parametrize("unknown", [
    None, 17, {"code": "declaration_conflict"},
    "PRIVATE /home/reader/secret.epub",
    "declaration_conflict; password=PRIVATE",
])
def test_unknown_failure_is_generic_and_never_interpolates_input(unknown):
    text = declaration_refusal_message(unknown)
    assert "PRIVATE" not in text
    assert "Déclaration refusée" in text


@pytest.mark.parametrize("code,expected", [
    ("declaration_conflict", "declaration_conflict"),
    ("file_revalidation_failed", "file_revalidation_failed"),
    ("right_scope_changed", "right_scope_changed"),
    ("PRIVATE personal/path&account", "unavailable"),
])
def test_core_preview_publishes_only_reviewed_reason_code(
        selected, monkeypatch, code, expected):
    session, right, file, service, _rig = selected

    def refusal(*_args, **_kwargs):
        raise engine.DeclarationRejected(code)

    monkeypatch.setattr(engine, "declare_local_link", refusal)
    result = session.preview_declared_link(right, file)
    assert result == {"status": "blocked", "read_only": True,
                      "reason_code": expected}
    assert "PRIVATE" not in json.dumps(result)
    with service.storage._lock, service.storage._connect() as db:
        assert not db.execute(
            "SELECT 1 FROM metadata WHERE key LIKE "
            "'core.library.user-declaration.v1.%' LIMIT 1"
        ).fetchone()


def test_core_confirm_failure_is_precise_but_does_not_commit(selected, monkeypatch):
    session, right, file, service, _rig = selected
    preview = session.preview_declared_link(right, file)
    assert preview["status"] == "preview"

    def stale(*_args, **_kwargs):
        raise engine.DeclarationRejected("preview_changed")

    monkeypatch.setattr(engine, "declare_local_link", stale)
    result = session.confirm_declared_link(right, file, user_confirmed=True)
    assert result == {"status": "blocked", "read_only": True,
                      "reason_code": "preview_changed"}
    assert session.confirm_declared_link(
        right, file, user_confirmed=True
    )["status"] == "unavailable"
    with service.storage._lock, service.storage._connect() as db:
        assert not db.execute(
            "SELECT 1 FROM metadata WHERE key LIKE "
            "'core.library.user-declaration.v1.%' LIMIT 1"
        ).fetchone()
