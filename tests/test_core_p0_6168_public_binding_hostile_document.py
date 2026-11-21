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

        class Hostile:
            def __eq__(self, other):
                raise AssertionError("foreign equality on GTK")

            def __ne__(self, other):
                raise AssertionError("foreign inequality on GTK")

        assert public_c_binding_revision(window, Hostile(), receipt) is None
        assert public_c_binding_revision(
            window, document, replace(receipt, document=Hostile()),
        ) is None
        # Keep the actual original token usable; no one can mint a C revision
        # from an unrelated user object or lose a legitimate public notice.
        assert public_c_binding_revision(window, document, receipt) == 1
        assert db.entitlements() == db.acquisitions() == db.placements() == []
    finally:
        db.close()
