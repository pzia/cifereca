"""#3196: UI refusal projection is independent of retired W4 runtime."""
from __future__ import annotations

import ast
from pathlib import Path

from cifereca.core.local_attachment_refusal_types import ReconciliationRejected
from cifereca.core.kobo_local_reconciliation import (
    ReconciliationRejected as LegacyReconciliationRejected,
)
from cifereca.core.library_attachment_refusal import public_refusal_code


def test_w4_reexport_preserves_exception_type_identity():
    assert ReconciliationRejected is LegacyReconciliationRejected
    assert public_refusal_code(
        "kobo", LegacyReconciliationRejected("revision_unproven")
    ) == "revision_unproven"


def test_attachment_reason_projector_never_imports_w4_facade():
    source = (
        Path(__file__).resolve().parents[1] /
        "src/cifereca/core/library_attachment_refusal.py"
    ).read_text(encoding="utf-8")
    imports = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    }
    assert "kobo_local_reconciliation" not in imports
    assert "local_attachment_refusal_types" in imports


def test_unknown_and_forged_refusal_stays_private():
    class Forged(ReconciliationRejected):
        pass

    for error in (Forged("revision_unproven"),
                  ReconciliationRejected("private account /home/user/book.epub"),
                  ValueError("revision_unproven")):
        assert public_refusal_code("kobo", error) is None
    assert public_refusal_code(
        "paris", ReconciliationRejected("revision_unproven")
    ) is None
