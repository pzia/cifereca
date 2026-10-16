"""#1917: Virtual provider search contract is different for fixture and HTTP modes.

Uses only in-memory constructions, no server, user credentials or download.
"""
import pytest

from cifereca_cifericaopds import CifericaProvider, ContractUnavailable


def provider(mode, tmp_path):
    return CifericaProvider(
        "public-source", ("catalog.search",), client=None,
        mode=mode, directory=tmp_path, source_searchable=lambda: True,
    )


def test_fixture_declares_local_post_filters_but_no_pagination(tmp_path):
    contract = provider("fixture", tmp_path).descriptor.search_capabilities
    assert contract.declared
    assert contract.fields == ("all", "title", "author", "isbn", "language")
    assert contract.scopes == ("all",)
    assert contract.pagination == "none"
    assert not contract.supports_scope("digital")
    assert contract.supports_field("isbn")
    assert any("localement" in note for note in contract.notes)


def test_http_mode_does_not_claim_fixture_only_field_filtering(tmp_path):
    contract = provider("candidate_v02", tmp_path).descriptor.search_capabilities
    assert contract.fields == ("all",)
    assert contract.scopes == ("all",)
    assert contract.pagination == "none"
    assert not contract.supports_field("title")
    assert not contract.supports_field("isbn")
    assert any("HTTP" in note for note in contract.notes)


@pytest.mark.parametrize("mode", ("fixture", "candidate_v02"))
@pytest.mark.parametrize("scope", ("digital", "physical", "private", ""))
def test_unsupported_scope_is_rejected_without_client_or_file_io(mode, scope, tmp_path):
    session = provider(mode, tmp_path).create_session(
        credential_id="public", label="", credentials={}, options={},
        config={}, storage=None, diagnostics=None,
    )
    with pytest.raises(ContractUnavailable, match="unsupported candidate search scope"):
        session.search("public book", scope=scope)


def test_http_rejects_specialized_field_before_candidate_network(tmp_path):
    session = provider("candidate_v02", tmp_path).create_session(
        credential_id="public", label="", credentials={}, options={},
        config={}, storage=None, diagnostics=None,
    )
    with pytest.raises(ContractUnavailable, match="field unverified"):
        session.search("public book", field="author")
    with pytest.raises(ContractUnavailable, match="pagination is unverified"):
        session.search("public book", page=1)
