"""P0 #2797: shared PDF review is not an EPUB CRC check or a PDF attestation.

Only a format-scoped, private, read-only Kobo preflight is exposed. Neither
the catalogue nor a PDF signature grants acquisition, DRM or placement rights.
"""
from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace
import sqlite3

import pytest

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection, _right_explanation
from tests.test_library_reconciliation import epub, filesystem


def _persistent_files(root):
    """Keep strict EPUB/DB/WAL immutability; SQLite readers can alter -shm.

    WAL shared memory is an ephemeral locking/index structure, not a durable
    book or SQL mutation. Comparing -shm bytes after legitimate read-only SQL
    transactions gives a false positive even when the DB and WAL are intact.
    """
    return {name: value for name, value in filesystem(root).items()
            if not name.endswith("-shm")}


def _cause(media_format, validation, structure):
    observed = SimpleNamespace(
        media_format=media_format,
        validation=validation,
        structure_validation=structure,
        sha256="a" * 64,
        size=100,
    )
    return _right_explanation(
        {"provider_id": "paris", "entitlement_type": "loan", "state": "active"},
        [{"status": "complete", "artifact_id": "artifact"}],
        [{"path": "/private/book." + media_format, "artifact_id": "artifact",
          "state": "present"}],
        {"artifact": [{"sha256": "a" * 64, "size": 100}]},
        {"/private/book." + media_format: (observed,)},
        False,
    )


@pytest.mark.parametrize(("format", "validation", "structure", "expected"), [
    ("pdf", "pdf_signature_only", "pdf_structure_minimal", False),
    ("pdf", "pdf_signature_only", "pdf_structure_invalid", True),
    ("epub", "ok", "epub_structure_crc_ok", False),
    ("epub", "ok", "epub_structure_invalid", True),
])
def test_right_explanation_uses_exact_format_validation(
    format, validation, structure, expected,
):
    message = _cause(format, validation, structure)
    if format == "pdf":
        assert "Structure EPUB" not in message
        assert ("Signature et fin de fichier PDF non vérifiées" in message) is expected
        assert "conformité PDF" in message if expected else "conformité PDF" not in message
    else:
        assert ("Structure EPUB et CRC non validés" in message) is expected
        assert "Signature et fin de fichier PDF" not in message
    assert "ne prouvent ni l’absence physique" in message

