"""P0 #5054: acquisition source identifier validation must be structural.

An exact source record ID is not an entitlement, acquisition consent,
purchase history or a download permission. Preserve the stronger live binding
checks until the legacy action is physically retired.
"""
import ast
from pathlib import Path

import pytest


@pytest.mark.parametrize("name", [
    "numilog_manual_acquisition.py",
    "numilog_owned_download.py",
])
def test_numilog_admission_identifier_is_not_validated_by_rights_projector(name):
    path = Path(__file__).resolve().parents[1] / "src/cifereca/core" / name
    source = path.read_text(encoding="utf-8")
    modules = [
        node.module for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    ]
    # Only the old live binding is still allowed in manual acquisition,
    # pending its separate removal. The ID validator itself is generic.
    if name == "numilog_owned_download.py":
        assert not any(
            module is not None and "numilog_manifest_rights" in module
            for module in modules
        )
    else:
        assert "from .numilog_manifest_rights import numilog_inventory_binding_valid" in source
        assert "_scoped_record_id" not in source
    assert "exact_credential_scoped_record(" in source
    assert "provider_id=\"numilog\"" in source


def test_credential_scope_check_does_not_infer_current_download_offer():
    from cifereca.core.credential_scoped_record import exact_credential_scoped_record
    import hashlib

    credential = "fictional-account"
    digest = hashlib.sha256(credential.encode("utf-8")).hexdigest()[:20]
    valid = f"numilog:{digest}:lid-1:0123456789abcdef"
    assert exact_credential_scoped_record(
        valid, provider_id="numilog", credential_id=credential,
    ) == valid
    assert exact_credential_scoped_record(
        valid, provider_id="numilog", credential_id="another-account",
    ) is None
