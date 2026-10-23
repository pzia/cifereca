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
from tests.test_core_1345_confirmed_local_reconciliation import case as kobo_case
from tests.test_library_reconciliation import epub, filesystem


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


def test_kobo_pdf_needs_minimal_structure_and_exact_attested_sha(kobo_case, tmp_path):
    case = kobo_case
    # Keep the existing synthetic EPUBs valid: unrelated scan failures must
    # not manufacture a PDF authorization or muddy the evidence budget.
    epub(case.original, title="PRIVATE existing")
    case.target.write_bytes(case.original.read_bytes())

    good = case.target.with_suffix(".pdf")
    data = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
    good.write_bytes(data)
    bad = case.target.with_name("PRIVATE-bad.pdf")
    bad.write_bytes(b"%PDF-1.4\nno trailer")
    digest = sha256(data).hexdigest()
    with sqlite3.connect(case.storage.path) as db:
        db.execute(
            "UPDATE backup_items SET sha256=?, size=?, format='pdf' "
            "WHERE provider_id='kobo' AND credential_id='vault-1' "
            "AND item_id='PRIVATE-product'",
            (digest, len(data)),
        )

    before = filesystem(tmp_path)
    audit = audit_library(case.storage.path, {"default": (case.target.parent,)})
    session = LocalInspection(
        audit, lambda: True, "default", roots=(case.target.parent,),
        service=case.service,
    )
    try:
        rights = session.page(provider="kobo", state="rights_without_verified_file").cases
        assert len(rights) == 1
        right = rights[0]
        pdfs = session.unconfirmed_files(media_format="pdf", limit=20)
        assert pdfs.total == 2
        good_file = next(row for row in pdfs.cases if "PRIVATE-copy" in row.details)
        bad_file = next(row for row in pdfs.cases if "PRIVATE-bad" in row.details)
        good_review = session.preflight_manual_link(right.key, good_file.key)
        assert good_review.status == "eligible_for_fresh_validation"
        assert good_review.read_only and not good_review.attachment_authorized
        assert "kobo_fresh_proof_required" in good_review.codes
        bad_review = session.preflight_manual_link(right.key, bad_file.key)
        assert bad_review.status == "blocked"
        assert "file_unverified" in bad_review.codes
        assert "kobo_backup_signature_mismatch" in bad_review.codes
        assert filesystem(tmp_path) == before
    finally:
        session.close()
