"""#5048: legacy local right/file mutations remain provider-neutral and inert."""
from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace as NS

from cifereca.core.library_attachment_refusal import public_refusal_label
from cifereca.core.library_local_inspection import LocalInspection


def test_retired_manual_link_operations_have_no_supplier_dispatch():
    source = inspect.getsource(LocalInspection)
    tree = ast.parse(source)
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "LocalInspection")
    actions = {
        "_refuse_obsolete_manual_attachment", "preview_manual_attachment",
        "confirm_manual_attachment", "preview_manual_undo", "confirm_manual_undo",
    }
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in actions:
            literals = {
                item.value for item in ast.walk(node)
                if isinstance(item, ast.Constant) and type(item.value) is str
            }
            assert not literals.intersection({"kobo", "numilog", "bnf-pret", "paris"})
    assert "_kobo_transaction_inputs" not in source


def test_generic_refusal_does_not_expose_provider_data():
    refusal = "canonical_source_validation_required"
    for provider in ("kobo", "numilog", "paris", "other-provider"):
        label = public_refusal_label(provider, refusal)
        assert label and "canonique" in label
        assert "other-provider" not in label
    assert public_refusal_label("other-provider", "password=PRIVATE") is None


def test_all_four_retired_manual_operations_are_read_only_without_proofs():
    # The compatibility action surface still exists for GUI consumers.
    # A detached inspection may never authorize any attachment or undo.
    host = NS(
        revoke_manual_attachment_preview=lambda: None,
        _manual_pending=None,
        _manual_undo_pending=None,
        _manual_undo=None,
        _manual_service=None,
        preflight_manual_link=lambda *_: NS(status="unavailable"),
    )
    host._refuse_obsolete_manual_attachment = (
        lambda *args: LocalInspection._refuse_obsolete_manual_attachment(host, *args)
    )
    preview = LocalInspection.preview_manual_attachment(host, "right:1", "file:1")
    confirm = LocalInspection.confirm_manual_attachment(
        host, "right:1", "file:1", user_confirmed=True,
    )
    undo = LocalInspection.preview_manual_undo(host, "right:1", "file:1")
    undo_confirm = LocalInspection.confirm_manual_undo(
        host, "right:1", "file:1", user_confirmed=True,
    )
    assert all(result == {"status": "unavailable", "read_only": True}
               for result in (preview, confirm, undo, undo_confirm))
