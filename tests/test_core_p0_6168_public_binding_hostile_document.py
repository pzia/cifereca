"""The plugin-facing public C receipt refuses foreign DocumentRef equality.

A real Core-minted public binding is used; no supplier I/O or rights.
"""
from dataclasses import replace

from cifereca.gui.plugin_public_c_binding import public_c_binding_revision
from cifereca.storage import Storage
from tests.test_core_p0_6366_plugin_public_c_n3_revocation import _view
from tests.test_core_p0_6366_same_call_public_cover_writer import witnessed, mirror
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row
from tests.test_plugin_ui_host_boundary import host_type


def test_native_document_type_precedes_public_plugin_binding_equality(tmp_path):
    db = Storage(tmp_path / "typed-document.sqlite")
    _, window, _ = _view()
    try:
        row = witnessed(public_row())
        assert mirror(db, [row]) == 1
        document, receipt = host_type()(window).public_notice_cover_binding(
            row, identity_id="alice",
        )
        assert public_c_binding_revision(window, document, receipt) == 1
        from cifereca.core.notice_c_live_revision import persisted_live_c_revision
        assert persisted_live_c_revision(row, document) == 1

        class Hostile:
            def __eq__(self, other):
                raise AssertionError("foreign equality on GTK")

            def __ne__(self, other):
                raise AssertionError("foreign inequality on GTK")

        assert public_c_binding_revision(window, Hostile(), receipt) is None
        assert public_c_binding_revision(
            window, document, replace(receipt, document=Hostile()),
        ) is None
        assert persisted_live_c_revision(row, Hostile()) is None
        # Even an exact dataclass instance may be tampered after creation.
        # The Core witness must not compare its foreign nested members.
        altered = replace(document)
        object.__setattr__(altered, "artifact_id", Hostile())
        assert persisted_live_c_revision(row, altered) is None
        original_proof = row._core_persisted_c_revision_witness
        original_ref = row._core_notice_canonical_cover_document_ref
        try:
            row._core_persisted_c_revision_witness = (
                original_proof[0], original_proof[1], altered, original_proof[3],
            )
            assert persisted_live_c_revision(row, document) is None
            row._core_persisted_c_revision_witness = original_proof
            row._core_notice_canonical_cover_document_ref = altered
            assert persisted_live_c_revision(row, document) is None
        finally:
            row._core_persisted_c_revision_witness = original_proof
            row._core_notice_canonical_cover_document_ref = original_ref
        # Keep the actual original token usable; no one can mint a C revision
        # from an unrelated user object or lose a legitimate public notice.
        assert public_c_binding_revision(window, document, receipt) == 1
        assert persisted_live_c_revision(row, document) == 1
        assert db.entitlements() == db.acquisitions() == db.placements() == []
    finally:
        db.close()
