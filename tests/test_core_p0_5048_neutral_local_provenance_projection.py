"""#5048: private bibliography source is descriptive, never purchase authority."""
from __future__ import annotations

from copy import deepcopy
import json
import sqlite3
import time

import pytest

from cifereca.core.library_reconciliation import _query_image, EvidenceSnapshot, reconcile
from tests.test_core_p0_2796_reconciliation_repeatability import _fixture


def _read_source(provider, source):
    with sqlite3.connect(":memory:") as db:
        db.execute(
            "CREATE TABLE entitlements ("
            "entitlement_id TEXT, provider_id TEXT, credential_id TEXT,"
            "account_id TEXT, item_id TEXT, identity_id TEXT, state TEXT,"
            "entitlement_type TEXT, loan_id TEXT, expires_at TEXT,"
            "metadata_json TEXT)"
        )
        db.execute(
            "INSERT INTO entitlements VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("right", provider, "credential", "borrower", "edition",
             "alice", "active", "owned", None, None,
             json.dumps({"source": source, "title": "Private book"})),
        )
        image = db.serialize()
    result = _query_image(image, max_rows=1000, deadline=time.monotonic() + 5,
                          identity_id="alice")
    return result.tables["entitlements"][0]


@pytest.mark.parametrize("provider,source,valid", [
    ("kobo", "kobo.personal_manifest", True),
    ("numilog", "numilog.authenticated_library", True),
    ("future-books", "future-books.authenticated_library", True),
    ("another-plugin", "another-plugin.personal_manifest", True),
    ("future-books", "kobo.personal_manifest", False),
    ("future-books", "future-books.unverified", False),
    (" future-books", " future-books.personal_manifest", False),
    ("future-books", ["future-books.personal_manifest"], False),
    ("future-books", None, False),
])
def test_private_sqlite_projection_has_exact_provider_provenance(provider, source, valid):
    right = _read_source(provider, source)
    assert right["attested_source"] == (source if valid else None)
    assert right["display_title"] == "Private book"
    assert "metadata_json" not in right


@pytest.mark.parametrize("source", [
    "future-books.authenticated_library", "future-books.personal_manifest",
])
def test_unknown_provider_descriptive_provenance_never_mints_a_file_right(source):
    inventory, tables = _fixture(1)
    tables = deepcopy(tables)
    right = tables["entitlements"][0]
    right["provider_id"] = "future-books"
    right["attested_source"] = source
    tables["placements"][0]["provider_id"] = "future-books"
    tables["acquisition_ledger"][0]["provider_id"] = "future-books"
    report = reconcile(inventory, EvidenceSnapshot(tables))
    # Metadata provenance alone is insufficient for an executable owner.
    assert "confirmed_link" not in report.files[0]
    assert not [edge for edge in report.edges if edge.certainty == "attested"]
    assert report.unverified_rights == [0]
