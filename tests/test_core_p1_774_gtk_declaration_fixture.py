"""Check offline inputs of the mandatory Maintenance GTK declaration route.

No GTK, network, provider HTTP or private filesystem data is published. The
real E2E recipe builds these same Core/SQLite fixtures, then XTEST drives UI.
"""
import pytest

from e2e.library_declaration_acceptance import _fixture
from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection


@pytest.mark.parametrize("attested", [False, True])
def test_gtk_maintenance_declaration_fixture_has_one_real_core_right(
    tmp_path, attested,
):
    service, book, directory, digest = _fixture(
        tmp_path, "kobo" if attested else "numilog", attested=attested,
    )
    try:
        service.storage.close()
        audit = audit_library(service.storage.path, {"default": (directory,)})
        assert audit.redacted()["status"] == "complete"
        session = LocalInspection(
            audit, lambda: True, "default",
            roots=(directory,), service=service,
        )
        cases = session.page(state="rights_without_verified_file").cases
        assert len(cases) == 1
        assert cases[0].provider in {"kobo", "numilog"}
        if attested:
            assert service.storage.backup_list("kobo") == []
            rights = service.storage.entitlements(provider_id="kobo")
            assert len(rights) == 1
            assert rights[0]["metadata"]["source"] == "synthetic.e2e_fixture"
            assert service.storage.acquisitions(provider_id="kobo") == []
        assert book.exists() and len(digest) == 64
    finally:
        service.close()
