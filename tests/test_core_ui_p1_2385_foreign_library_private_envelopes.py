"""#2385 — shared Library is business-readable, not raw transport-readable.

No provider/SQLite/FS or GTK startup. Canonical owner rows stay intact;
unknown, future and internal proof fields must not silently become public.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.gui.library_global_projection import global_library_display_rows
from cifereca.gui.library_rows import project_library_rows


def _book():
    return {
        "key": "artifact:verified", "identity_id": "alice",
        "title": "Bibliographie partagée", "author": "Autrice A",
        "provider_id": "paris", "providers": ["paris"], "local": True,
        "artifact_id": "sha256:synthetic", "placement_id": "placed-alice",
        "placements": [{"placement_id": "placed-alice", "identity_id": "alice"}],
        "source_refs": [{"provider_id": "paris", "item_id": "edition-1",
                         "identity_id": "alice"}],
        "possessions": [{"identity_id": "alice", "provider_id": "paris",
                         "item_id": "edition-1", "kind": "loan"}],
        "metadata": {
            "subjects": ["histoire"], "description": "Description publique",
            "raw_response": {"cookie": "DONT_EXPOSE"},
        },
        "thumbnail_url": "file:///home/alice/.cache/private-cover.jpg",
        "_exact_loan_cover_proofs": [{
            "url": "file:///home/alice/.cache/private-cover.jpg",
            "credential_id": "PRIVATE_CREDENTIAL",
        }],
        "raw": {"cookie": "DONT_EXPOSE"},
        "provider_payload": {"Authorization": "Bearer SECRET"},
        "session_headers": {"X-Auth": "SECRET"},
        "credentials": {"password": "SECRET"},
        "future_unknown_key": "https://private.example/book?token=SECRET",
        "download_url": "https://private.example/book?token=SECRET",
    }


def test_foreign_library_default_closed_fields_keep_business_canonical():
    raw = _book()
    foreign, = global_library_display_rows(
        [{"identity_id": "alice", "entry": raw}], viewing_identity_id="bob",
    )
    # Business data and original owner are *shared*, not hidden by ACL.
    for key in (
        "key", "title", "author", "provider_id", "providers",
        "artifact_id", "placement_id", "placements", "source_refs",
        "possessions", "local",
    ):
        assert foreign[key] == raw[key], key
    assert foreign["metadata"] == {
        "subjects": ["histoire"], "description": "Description publique",
    }
    assert foreign["thumbnail_url"] is None
    for private in (
        "_exact_loan_cover_proofs", "raw", "provider_payload",
        "session_headers", "credentials", "future_unknown_key", "download_url",
    ):
        assert private not in foreign
    assert "_exact_loan_cover_proofs" in raw
    assert raw["metadata"]["raw_response"]["cookie"] == "DONT_EXPOSE"

    # The *owner* view is unchanged, retaining Core's exact internal rows.
    owner, = global_library_display_rows(
        [{"identity_id": "alice", "entry": raw}], viewing_identity_id="alice",
    )
    for private in ("raw", "credentials", "_exact_loan_cover_proofs"):
        assert owner[private] is raw[private]


def test_foreign_private_fields_do_not_reenter_library_row_object_cache():
    rows = global_library_display_rows(
        [{"identity_id": "alice", "entry": _book()}],
        viewing_identity_id="bob",
    )
    widgets, objects = project_library_rows(
        rows, row_factory=lambda *args: NS(
            key=args[0], metadata=args[3], cover=args[5],
        ),
        provider_display_name=lambda p: p,
        availability_label=lambda _row: "Consultable",
        backup_status_label=lambda state: state,
        format_date=lambda date: date,
    )
    assert len(widgets) == 1
    assert widgets[0].cover is None
    assert "Profil : alice" in widgets[0].metadata
    retained = objects[widgets[0].key]
    assert retained["possessions"][0]["identity_id"] == "alice"
    assert "_exact_loan_cover_proofs" not in retained
    assert "provider_payload" not in retained
    assert "future_unknown_key" not in retained
