"""#1917: Kobo-Vault public search capabilities must reflect attested semantics.

Real session/catalogue/Vault with httpx MockTransport: no remote account or DRM.
"""
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_kobo import DESCRIPTOR
from tests.test_kobo_search_public_only_unbound_p1 import _case


def test_kobo_declares_unverified_fields_as_unsupported():
    cap = DESCRIPTOR.search_capabilities
    assert cap.declared
    assert cap.fields == ("all",)
    assert cap.scopes == ("all", "digital", "catalog")
    assert cap.pagination == "page"
    assert cap.page_size == 50
    assert cap.supports_field("all")
    assert not cap.supports_field("author")
    assert not cap.supports_field("isbn")
    assert not cap.supports_scope("physical")
    assert any("non attesté" in note for note in cap.notes)


@pytest.mark.parametrize("field", (
    "title", "author", "isbn", "subject", "language", "",
    "Bearer PRIVATE_SIGNED_LINK", None, False, ["all"],
))
def test_unverified_field_rejected_before_any_public_or_private_http(field):
    session, paths = _case(library_status=200)
    try:
        with pytest.raises(ProviderCapabilityError, match="unsupported_field") as error:
            session.search("book", field=field)
        assert not paths
        assert "PRIVATE" not in str(error.value)
    finally:
        session.close()


def test_physical_scope_is_unsupported_not_a_false_empty_catalogue():
    session, paths = _case(library_status=200)
    try:
        with pytest.raises(ProviderCapabilityError, match="unsupported_scope"):
            session.search("book", scope="physical")
        assert paths == []
    finally:
        session.close()


def test_public_general_catalogue_never_falls_back_to_private_discovery():
    session, paths = _case(library_status=503)
    try:
        rows = session.search("book", scope="catalog", field="all", page=2)
        assert paths == ["/api/v2/catalog/search"]
        assert [row.record_id for row in rows] == ["public-edition"]
        assert rows[0].owned is False
        assert rows[0].revision_id is None
    finally:
        session.close()


def test_attested_general_search_retains_verified_account_scoping():
    session, paths = _case(library_status=200)
    try:
        results = session.search("book", scope="all", field="all")
        assert paths == ["/api/v2/catalog/search", "/api/v2/library/search"]
        assert [row.record_id for row in results] == [
            "public-edition", "owned-edition",
        ]
        assert results[0].owned is False
        assert results[1].owned is True
    finally:
        session.close()


@pytest.mark.parametrize("status", (401, 403))
def test_private_denial_reports_partial_results_without_private_metadata(status):
    session, paths = _case(library_status=status)
    try:
        rows = session.search("book", scope="all", field="all")
        assert paths == [
            "/api/v2/catalog/search", "/api/v2/library/search",
            "/api/v2/catalog/search",
        ]
        assert [row.record_id for row in rows] == ["public-edition"]
        assert rows[0].owned is False
        assert rows[0].revision_id is None
        assert session.last_search_warnings == [
            "partial_results : recherche personnelle refusée ; "
            "catalogue public seulement"
        ]
        assert "PRIVATE" not in repr(session.last_search_warnings)
        # A subsequent explicit public-only query is complete within its
        # declared scope and must revoke the former personal-denial warning.
        paths.clear()
        public = session.search("book", scope="catalog")
        assert paths == ["/api/v2/catalog/search"]
        assert len(public) == 1
        assert session.last_search_warnings == []
        # An unbound session also must never claim a personal search failure.
        paths.clear()
        session.connected = False
        assert len(session.search("book", scope="all")) == 1
        assert paths == ["/api/v2/catalog/search"]
        assert session.last_search_warnings == []
    finally:
        session.close()


def test_successful_personal_search_has_no_false_partial_warning():
    session, paths = _case(library_status=200)
    try:
        rows = session.search("book", scope="digital", field="all")
        assert [row.record_id for row in rows] == [
            "public-edition", "owned-edition",
        ]
        assert session.last_search_warnings == []
    finally:
        session.close()



@pytest.mark.parametrize("status", (401, 403))
def test_denied_private_search_discloses_public_only_partial_results(status):
    session, paths = _case(library_status=status)
    try:
        results = session.search("book", scope="all", field="all")
        assert paths == [
            "/api/v2/catalog/search", "/api/v2/library/search",
            "/api/v2/catalog/search",
        ]
        assert [row.record_id for row in results] == ["public-edition"]
        assert results[0].owned is False
        assert session.last_search_warnings == [
            "partial_results : recherche personnelle refusée ; "
            "catalogue public seulement",
        ]

        # A subsequent explicitly public query cannot retain the private
        # denial of an unrelated search or misrepresent catalogue coverage.
        paths.clear()
        session.search("book", scope="catalog", field="all")
        assert paths == ["/api/v2/catalog/search"]
        assert session.last_search_warnings == []
    finally:
        session.close()


def test_public_only_unbound_search_does_not_claim_personal_denial():
    session, paths = _case(library_status=403)
    try:
        session.connected = False
        results = session.search("book", scope="all", field="all")
        assert [row.record_id for row in results] == ["public-edition"]
        assert paths == ["/api/v2/catalog/search"]
        assert session.last_search_warnings == []
    finally:
        session.close()


@pytest.mark.parametrize("status", (429, 503))
def test_private_service_failures_are_not_downgraded_to_partial_success(status):
    from cifereca_kobo_vault import KoboVaultError

    session, paths = _case(library_status=status)
    try:
        with pytest.raises(KoboVaultError) as caught:
            session.search("book", scope="all", field="all")
        assert caught.value.status_code == status
        assert paths == ["/api/v2/catalog/search", "/api/v2/library/search"]
        assert session.last_search_warnings == []
    finally:
        session.close()
