"""P0 #5054: Numilog private-row shape never imports an ownership projector.

A credential-scoped identifier is provenance structure, not evidence of a
purchase, a right or current download availability.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from dataclasses import replace

import pytest

from cifereca.core.numilog_private_rows import private_library_rows_valid
from cifereca.models import SearchResult


def _row(credential="private-a", variant="0123456789abcdef"):
    digest = hashlib.sha256(credential.encode("utf-8")).hexdigest()[:20]
    record = f"numilog:{digest}:lid-a:{variant}"
    return SearchResult(
        id=record, record_id=record, docbase="NUMILOG",
        medium="digital", title="Synthetic", author=None,
        type="Livre numérique", format=None, publisher=None,
        published_at=None, description=None, thumbnail_url=None,
        friendly_url=None, scenario="NUMILOG_LIBRARY",
        source_provider="numilog", source_credential=credential,
        source_collection="library", content_type="ebook",
        owned=True, downloadable=True,
    )


def test_scoped_private_metadata_batch_is_accepted_without_entitlement():
    assert private_library_rows_valid([_row()], "private-a") is True
    assert private_library_rows_valid([], "private-a") is True


@pytest.mark.parametrize("row", [
    lambda: _row("other-credential"),
    lambda: replace(_row(), source_provider="unrelated"),
    lambda: replace(_row(), scenario="NUMILOG_PUBLIC"),
    lambda: replace(_row(), source_collection="catalog"),
    lambda: replace(_row(), owned=False),
    lambda: replace(_row(), id="numilog:forged"),
    lambda: replace(_row(), record_id="numilog:forged"),
    lambda: object(),
])
def test_unsafe_or_cross_credential_private_row_fails_closed(row):
    assert private_library_rows_valid([_row(), row()], "private-a") is False


def test_private_row_validator_has_no_legacy_projector_dependency():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/cifereca/core/numilog_private_rows.py").read_text(
        encoding="utf-8",
    )
    tree = ast.parse(source)
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(
        module is not None and "numilog_manifest_rights" in module
        for module in modules
    )
    assert "exact_credential_scoped_record" in source
