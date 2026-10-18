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



@pytest.mark.parametrize("field,query", (
    ("author", "Tous"),
    ("category", "Toutes"),
    ("genre", "Tous"),
    ("subject", "Tous"),
))
def test_all_option_is_not_treated_as_a_matched_filter(field, query):
    # The upstream form's default option (id_auteur=0,
    # id_categorie/id_genre=1) is NOT a real filter. Otherwise a query
    # matching "Tous" sends a broad catalogue GET as if field-specific.
    client = FakeClient()
    session = make_session(client)
    try:
        assert session.search(query, field=field) == []
        assert len(client.get_calls) == 1
        assert client.get_calls[0][1] == {}
        assert not client.post_calls
        assert session.last_search_warnings == []
    finally:
        session.close()


@pytest.mark.parametrize("field,form", (
    ("author", '<select name="id_auteur"><option value="0">Tous</option></select>'),
    ("category", '<select name="id_categorie"><option value="1">Toutes</option></select>'),
    ("genre", '<select name="id_genre"><option value="1">Tous</option></select>'),
))
def test_selector_with_only_global_default_is_not_a_usable_field_route(field, form):
    # A select element with only a broad default is not evidence that a
    # usable author/category/genre filtering route exists.
    client = FakeClient(form=form)
    session = make_session(client)
    try:
        with pytest.raises(ProviderCapabilityError, match="field_route_unavailable"):
            session.search("Doyle", field=field)
        assert len(client.get_calls) == 1
        assert not client.post_calls
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
        with pytest.raises(ProviderError, match="service_unavailable") as caught:
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

def test_subject_two_matching_server_selectors_reports_partial_coverage():
    form = '''<select name="id_categorie">
    <option value="7">Science-fiction</option></select>
    <select name="id_genre">
    <option value="9">Science-fiction</option></select>'''
    client = FakeClient(form=form)
    session = make_session(client)
    try:
        rows = session.search("science-fiction", field="subject")
        assert len(rows) == 1
        assert rows[0].owned is False
        assert rows[0].metadata["categories"] == ["Science-fiction"]
        assert "genres" not in rows[0].metadata
        assert client.get_calls[1][1]["params"]["id_categorie"] == "7"
        assert client.get_calls[1][1]["params"]["id_genre"] == "1"
        assert session.last_search_warnings == [
            "partial_results : catégorie et genre correspondants ; un seul interrogé"
        ]
    finally:
        session.close()


def test_subject_two_available_selectors_one_match_needs_no_false_partial_warning():
    client = FakeClient(form=FORM)
    session = make_session(client)
    try:
        rows = session.search("mystere", field="subject")
        assert len(rows) == 1
        assert rows[0].metadata["genres"] == ["Policier & Mystère"]
        assert "categories" not in rows[0].metadata
        assert session.last_search_warnings == []
        assert len(client.get_calls) == 2
    finally:
        session.close()


def test_subject_without_matching_options_remains_true_zero():
    client = FakeClient(form=FORM)
    session = make_session(client)
    try:
        assert session.search("introuvable", field="subject") == []
        assert session.last_search_warnings == []
        assert len(client.get_calls) == 1
    finally:
        session.close()



@pytest.mark.parametrize("field,select_name,expected_key", (
    ("author", "id_auteur", "id_auteur"),
    ("category", "id_categorie", "id_categorie"),
    ("genre", "id_genre", "id_genre"),
))
def test_equally_ranked_server_options_warn_about_partial_selection(
    field, select_name, expected_key,
):
    # Query "jean" can select TWO public options on the same server route.
    # One GET is valid, but must never be presented as comprehensive.
    form = (
        f'<select name="{select_name}">'
        '<option value="51">Jean Alpha</option>'
        '<option value="52">Jean Beta</option>'
        '</select>'
    )
    client = FakeClient(form=form)
    session = make_session(client)
    try:
        rows = session.search("jean", field=field)
        assert len(rows) == 1
        assert len(client.get_calls) == 2
        # The existing selector deliberately prefers the shorter label.
        assert client.get_calls[1][1]["params"][expected_key] == "52"
        assert session.last_search_warnings == [
            "partial_results : plusieurs options du filtre ; une seule interrogée"
        ]
    finally:
        session.close()


def test_exact_option_beats_other_partial_labels_without_spurious_warning():
    form = ('<select name="id_auteur">'
            '<option value="12">Jean Dupont</option>'
            '<option value="11">Jean</option>'
            '</select>')
    client = FakeClient(form=form)
    session = make_session(client)
    try:
        assert len(session.search("jean", field="author")) == 1
        assert client.get_calls[1][1]["params"]["id_auteur"] == "11"
        assert session.last_search_warnings == []
    finally:
        session.close()


def test_subject_warnings_distinguish_cross_route_and_within_route_ambiguity():
    form = (
        '<select name="id_categorie">'
        '<option value="7">Science-fiction A</option>'
        '<option value="8">Science-fiction B</option></select>'
        '<select name="id_genre">'
        '<option value="9">Science-fiction</option></select>'
    )
    client = FakeClient(form=form)
    session = make_session(client)
    try:
        assert len(session.search("science-fiction", field="subject")) == 1
        # Exact genre match wins over two approximate category matches.
        assert client.get_calls[1][1]["params"]["id_genre"] == "9"
        assert session.last_search_warnings == [
            "partial_results : catégorie et genre correspondants ; un seul interrogé"
        ]
    finally:
        session.close()


def test_same_selector_ambiguity_rejects_stale_warning_on_next_search():
    form = ('<select name="id_auteur">'
            '<option value="51">Jean Alpha</option>'
            '<option value="52">Jean Beta</option>'
            '<option value="53">Bernard</option></select>')
    client = FakeClient(form=form)
    session = make_session(client)
    try:
        session.search("Jean", field="author")
        assert session.last_search_warnings
        session.search("Bernard", field="author")
        assert session.last_search_warnings == []
    finally:
        session.close()



@pytest.mark.parametrize("page", (-1, True, False, "1", "garbage", 1.9, None, 101, 10**100))
@pytest.mark.parametrize("field", ("all", "author"))
def test_invalid_public_page_never_triggers_get_or_post(field, page):
    client = FakeClient()
    session = make_session(client)
    try:
        with pytest.raises(ProviderCapabilityError, match="invalid_page"):
            session.search("Jean", field=field, page=page)
        assert client.get_calls == [] and client.post_calls == []
        assert session.last_search_warnings == []
    finally:
        session.close()


@pytest.mark.parametrize("page,expected_offset", ((0, "0"), (2, "100"), (100, "5000")))
def test_title_search_page_offsets_are_bounded_and_not_clamped(page, expected_offset):
    client = FakeClient()
    session = make_session(client)
    try:
        assert len(session.search("Jean", field="title", page=page)) == 1
        assert client.post_calls[0][1]["data"]["offset"] == expected_offset
    finally:
        session.close()


def test_invalid_page_does_not_retain_previous_partial_warning():
    client = FakeClient(catalogue="<ul></ul>")
    session = make_session(client)
    try:
        session.search("Doyle", field="all")
        assert session.last_search_warnings
        previous_calls = len(client.post_calls)
        with pytest.raises(ProviderCapabilityError, match="invalid_page"):
            session.search("Doyle", field="all", page=-1)
        assert len(client.post_calls) == previous_calls
        assert session.last_search_warnings == []
    finally:
        session.close()
