"""#1917: Distinguish unsupported fields, absent HTML selectors, true zero and partial coverage.

Offline only: requests are synthetic and no user/private account is consulted.
"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from cifereca.plugin_api import ProviderCapabilityError, ProviderError

PLUGIN = Path(__file__).resolve().parents[1] / "cifereca_ebooksgratuits.py"
spec = importlib.util.spec_from_file_location("ebooksgratuits_p1_contract", PLUGIN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

FORM = '''<select name="id_auteur"><option value="0">Tous</option>
<option value="51">Doyle, Arthur Conan</option></select>
<select name="id_categorie"><option value="1">Toutes</option>
<option value="7">Poésie</option></select>
<select name="id_genre"><option value="1">Tous</option>
<option value="9">Policier &amp; Mystère</option></select>'''
CATALOGUE = '''<ul><li><span class="listeb">Livre test</span>
<span class="auteur">Autrice</span>
<a href="details.php?book=1">Fiche</a><a href="newsendbook.php?id=1&amp;format=epub">EPUB</a></li></ul>'''


class FakeClient:
    def __init__(self, form=FORM, catalogue=CATALOGUE, error=None):
        self.form, self.catalogue, self.error = form, catalogue, error
        self.get_calls, self.post_calls = [], []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.form if not kwargs else self.catalogue, raise_for_status=lambda: None)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.catalogue, raise_for_status=lambda: None)

    def close(self):
        pass


def make_session(client):
    session = mod.EbooksSession(
        credential_id="public", label="", credentials={}, options={},
        config={}, diagnostics=None, transport=None,
    )
    session.client.close()
    session.client = client
    session.connected = True
    return session


def test_public_search_contract_is_explicit_and_conservative():
    cap = mod.DESCRIPTOR.search_capabilities
    assert cap.fields == ("all", "title", "author", "subject", "category", "genre")
    assert cap.scopes == ("all", "digital")
    assert cap.pagination == "page" and cap.page_size == 50
    assert not cap.supports_field("isbn")
    assert not cap.supports_scope("physical")


@pytest.mark.parametrize("field,scope", (
    ("isbn", "all"), ("language", "all"), ("", "all"), ("all", "physical"),
))
def test_unsupported_request_never_calls_network(field, scope):
    client = FakeClient()
    session = make_session(client)
    try:
        with pytest.raises(ProviderCapabilityError, match="unsupported_"):
            session.search("Poésie", field=field, scope=scope)
        assert not client.get_calls and not client.post_calls
    finally:
        session.close()


def test_missing_author_selector_is_not_a_false_zero():
    client = FakeClient(form='<select name="id_genre"><option value="1">Tous</option></select>')
    session = make_session(client)
    try:
        with pytest.raises(ProviderCapabilityError, match="field_route_unavailable"):
            session.search("Doyle", field="author")
        assert len(client.get_calls) == 1
        assert not client.post_calls
    finally:
        session.close()


def test_available_author_selector_with_no_matching_choice_returns_zero():
    client = FakeClient()
    session = make_session(client)
    try:
        assert session.search("Inconnu", field="author") == []
        assert len(client.get_calls) == 1
        assert not session.last_search_warnings
    finally:
        session.close()


def test_subject_single_selector_warns_partial_but_returns_attested_category():
    form = '<select name="id_categorie"><option value="7">Poésie</option></select>'
    client = FakeClient(form=form)
    session = make_session(client)
    try:
        rows = session.search("poesie", field="subject")
        assert len(rows) == 1
        assert rows[0].owned is False
        assert rows[0].metadata["categories"] == ["Poésie"]
        assert "genres" not in rows[0].metadata
        assert session.last_search_warnings and "partial_results" in session.last_search_warnings[0]
        assert len(client.get_calls) == 2
    finally:
        session.close()


def test_general_title_search_paginates_and_zero_is_real():
    client = FakeClient(catalogue="<ul></ul>")
    session = make_session(client)
    try:
        assert session.search("Livre absent", field="title", page=2) == []
        assert client.post_calls[0][1]["data"]["offset"] == "100"
        assert session.last_search_warnings == []
    finally:
        session.close()


def test_http_failure_is_typed_without_leaking_exception_text():
    client = FakeClient(error=httpx.ConnectError("PRIVATE_TOKEN"))
    session = make_session(client)
    try:
        with pytest.raises(ProviderError, match="réseau indisponible") as caught:
            session.search("Doyle", field="author")
        assert "PRIVATE_TOKEN" not in str(caught.value)
    finally:
        session.close()



def test_general_all_search_declares_title_only_coverage_even_if_empty():
    client = FakeClient(catalogue="<ul></ul>")
    session = make_session(client)
    try:
        assert session.search("absent auteur", field="all") == []
        assert len(client.post_calls) == 1
        assert client.post_calls[0][1]["data"]["titre"] == "absent auteur"
        assert session.last_search_warnings == [
            "partial_results : recherche générale limitée au titre"
        ]
        # Explicit title queries and public browsing have no fake warning.
        session.search("absent auteur", field="title")
        assert session.last_search_warnings == []
        session.search("", field="all", page=0)
        assert session.last_search_warnings == []
    finally:
        session.close()


def test_general_all_warning_cannot_survive_unsupported_field():
    client = FakeClient()
    session = make_session(client)
    try:
        assert session.search("livre", field="all")
        assert session.last_search_warnings == [
            "partial_results : recherche générale limitée au titre"
        ]
        before = len(client.post_calls)
        with pytest.raises(ProviderCapabilityError, match="unsupported_field"):
            session.search("livre", field="isbn")
        # The broker avoids unsupported fields; a rejected query must not
        # leak an old warning into later results.
        assert len(client.post_calls) == before
        assert session.last_search_warnings == []
    finally:
        session.close()
