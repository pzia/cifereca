"""#3909: staging cleanup must honor JSON path references with escaped Unicode.

Synthetic SQLite v14 / verified PDF. Never sends a second supplier GET.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cifereca.core.canonical_promotion import cleanup_published_staging
from tests.test_core_p0_3909_atomic_promotion import rig, _isolate_delivery_lock
from tests.test_core_p0_3909_postcommit_staging_reconciliation import (
    _published_with_deferred_cleanup,
)
from tests.test_core_p0_3909_postcommit_cleanup_dependencies import _checkpoint
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES


def _accented(rig, monkeypatch):
    service, db, root = rig
    # Path contains a non-ASCII parent, as on real French-language libraries.
    root = root.with_name("Étagère")
    service.root = root
    return _published_with_deferred_cleanup(
        (service, db, root), monkeypatch,
    )


def _cleanup(db, service, root, staged):
    return cleanup_published_staging(
        db, source=service.source,
        completed=_checkpoint(db, service, staged),
        library_root=root,
    )


@pytest.mark.parametrize("variant", [
    "catalogue", "other_receipt", "escaped_dot", "escaped_slashes",
    "duplicate_field", "invalid_json",
])
def test_postcommit_retains_stage_if_unicode_json_references_it(
    rig, monkeypatch, variant,
):
    service, db, root, gets, staged = _accented(rig, monkeypatch)
    assert staged.read_bytes() == BYTES
    final = Path(db.placements()[0]["path"])
    marker = str(staged.parent)
    assert "É" in marker
    raw = json.dumps({"nested": {"path": str(staged)}})
    assert r"\u00c9" in raw  # Python's default ensure_ascii=True

    if variant == "escaped_dot":
        raw = raw.replace(".canonical-delivery", r"\u002ecanonical-delivery")
    elif variant == "escaped_slashes":
        raw = raw.replace("/", r"\/")
    elif variant == "duplicate_field":
        raw = '{"path":"unrelated","path":' + json.dumps(str(staged)) + "}"
    elif variant == "invalid_json":
        raw = '{"path":' + json.dumps(str(staged))  # fail closed

    with db._lock, db._connect() as sql:
        if variant == "catalogue":
            catalog_id, original_metadata = sql.execute(
                "SELECT catalog_entry_id,metadata_json FROM catalog_entries"
            ).fetchone()
            sql.execute(
                "UPDATE catalog_entries SET metadata_json=? WHERE catalog_entry_id=?",
                (raw, catalog_id),
            )
        else:
            sql.execute(
                "INSERT INTO acquisition_ledger("
                "acquisition_id,provider_id,credential_id,account_id,item_id,"
                "identity_id,status,metadata_json,created_at,updated_at) "
                "SELECT 'unicode-external-reference',provider_id,credential_id,"
                "account_id,item_id,identity_id,'retryable_error',?,1,1 "
                "FROM acquisition_ledger "
                "WHERE acquisition_id LIKE 'canonical-intake:%'",
                (raw,),
            )

    # In particular, Unicode escaping must not allow the run directory to be
    # destroyed while a separate SQLite operation still points into it.
    assert _cleanup(db, service, root, staged) is False
    assert staged.read_bytes() == BYTES
    assert final.read_bytes() == BYTES

    with db._lock, db._connect() as sql:
        if variant == "catalogue":
            sql.execute(
                "UPDATE catalog_entries SET metadata_json=? WHERE catalog_entry_id=?",
                (original_metadata, catalog_id),
            )
        else:
            sql.execute(
                "DELETE FROM acquisition_ledger "
                "WHERE acquisition_id='unicode-external-reference'",
            )

    assert _cleanup(db, service, root, staged) is True
    assert not staged.exists()
    assert final.read_bytes() == BYTES
    assert gets == ["signed-right-offer"]


def test_cleanup_with_accented_root_and_no_other_references(rig, monkeypatch):
    service, db, root, gets, staged = _accented(rig, monkeypatch)
    assert _cleanup(db, service, root, staged) is True
    assert not staged.exists()
    assert gets == ["signed-right-offer"]
