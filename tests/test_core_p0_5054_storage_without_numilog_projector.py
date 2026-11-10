"""P0 #5054: SQLite Numilog preflight cannot depend on ownership projector.

The SQL and historical rows remain untouched until the corresponding feature
is retired. The scoped-record validation is generic structural provenance.
"""
import ast
from pathlib import Path

from cifereca.core.credential_scoped_record import exact_credential_scoped_record


def test_storage_does_not_import_numilog_manifest_rights():
    source = (Path(__file__).resolve().parents[1] / "src/cifereca/storage.py").read_text(
        encoding="utf-8",
    )
    tree = ast.parse(source)
    imports = [
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    ]
    assert not any(
        module is not None and "numilog_manifest_rights" in module
        for module in imports
    )
    assert source.count("exact_credential_scoped_record(") >= 2


def test_record_scope_validation_does_not_admit_foreign_or_malformed_ids():
    import hashlib

    digest = hashlib.sha256(b"credential-a").hexdigest()[:20]
    valid = f"numilog:{digest}:edition-a:0123456789abcdef"
    assert exact_credential_scoped_record(
        valid, provider_id="numilog", credential_id="credential-a",
    ) == valid
    for item_id in (
        valid.replace("numilog:", "kobo:", 1),
        valid.replace(":0123456789abcdef", ":0123456789abcdeg"),
        valid.replace("edition-a", "../private"),
        valid + "?auth=PRIVATE",
    ):
        assert exact_credential_scoped_record(
            item_id, provider_id="numilog", credential_id="credential-a",
        ) is None
    assert exact_credential_scoped_record(
        valid, provider_id="numilog", credential_id="credential-b",
    ) is None
