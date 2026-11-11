"""P0 #5054 — manual Numilog admission cannot depend on a rights projector.

The scoped ID is provenance structure only, never authorization to acquire.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

@pytest.mark.parametrize("module", ["numilog_manual_acquisition.py", "numilog_owned_download.py"])
def test_numilog_acquisition_no_import_from_legacy_projector(module):
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/cifereca/core" / module).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert all(
        not isinstance(node, ast.ImportFrom)
        or node.module != "numilog_manifest_rights"
        for node in ast.walk(tree)
    )
    assert "exact_credential_scoped_record(" in source


def test_scoped_id_is_passive_and_credential_exact():
    from cifereca.core.credential_scoped_record import exact_credential_scoped_record

    cred = "synthetic-credential"
    digest = hashlib.sha256(cred.encode("utf-8")).hexdigest()[:20]
    value = f"numilog:{digest}:edition:0123456789abcdef"
    assert exact_credential_scoped_record(
        value, provider_id="numilog", credential_id=cred,
    ) == value
    assert exact_credential_scoped_record(
        value, provider_id="numilog", credential_id="other",
    ) is None
