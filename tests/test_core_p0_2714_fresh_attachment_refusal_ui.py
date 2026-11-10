"""#2714/#2797: exact Core refusals, never private exception prose in GTK.

The fixtures are synthetic; all supplier links, SQLite rights and EPUB bytes
are local. No request/download, plugin authority, or forged acquisition.
"""
from __future__ import annotations

import pytest

from cifereca.core.library_attachment_refusal import (
    public_refusal_code, public_refusal_label,
)
from cifereca.core import kobo_local_reconciliation as kobo
from cifereca.gui.library_inspection_view import _attachment_refusal_message


_PRIVATE = "PRIVATE-account password=SECRET /home/private/BOOK.epub"


@pytest.mark.parametrize("provider,exception,expected", [
    ("kobo", kobo.ReconciliationRejected("backup_evidence_missing"),
     "backup_evidence_missing"),
    ("kobo", kobo.ReconciliationRejected("revision_unproven"),
     "revision_unproven"),
    ("kobo", kobo.ReconciliationRejected("file_hash_mismatch"),
     "file_hash_mismatch"),
])
def test_core_refusal_codes_are_closed_and_explained(provider, exception, expected):
    assert public_refusal_code(provider, exception) == expected
    response = {"status": "blocked", "read_only": True, "reason_code": expected}
    message = _attachment_refusal_message(response, provider, "Refus")
    assert message.startswith("Refus\nMotif vérifié : ")
    assert "PRIVATE" not in message
    assert "SECRET" not in message
    assert len(message) < 350
    assert public_refusal_label(provider, expected) in message


@pytest.mark.parametrize("exception", [
    kobo.ReconciliationRejected(_PRIVATE),
    kobo.ReconciliationRejected(True),
    RuntimeError(_PRIVATE),
])
def test_unknown_private_exceptions_never_appear_in_ui(exception):
    for provider in ("numilog", "kobo", "paris", _PRIVATE):
        assert public_refusal_code(provider, exception) is None
        assert _attachment_refusal_message(
            {"status": "blocked", "read_only": True, "reason_code": _PRIVATE},
            provider, "Refus",
        ) == "Refus"
    assert _PRIVATE not in repr(public_refusal_label("kobo", _PRIVATE))


def test_a_plugin_or_subclass_cannot_forge_core_refusal():
    class PluginSpoof(kobo.ReconciliationRejected):
        pass
    assert public_refusal_code("kobo", PluginSpoof("backup_evidence_missing")) is None
    for malformed in (
        {"status": "blocked", "read_only": True, "reason_code": _PRIVATE},
        {"status": "blocked", "read_only": False,
         "reason_code": "numilog_canonical_redownload_required"},
        {"status": "preview", "read_only": True,
         "reason_code": "numilog_canonical_redownload_required"},
        {"status": "blocked", "read_only": True,
         "reason_code": ["numilog_canonical_redownload_required"]},
    ):
        assert _attachment_refusal_message(malformed, "numilog", "Refus") == "Refus"

