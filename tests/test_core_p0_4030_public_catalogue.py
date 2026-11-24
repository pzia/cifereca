"""#4030: one explicit public catalogue comparison, no supplier right."""
from dataclasses import replace
from types import SimpleNamespace as NS
from xml.sax.saxutils import escape

import pytest

from cifereca.core.local_origin_public_catalogue import search_local_public_catalogue
from cifereca.core.local_origin_suggestion_operator import admit_local_origin_suggestion
from cifereca.models import SearchResult
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from cifereca.providers import ProviderSearchCapabilities
from tests.test_core_p0_3171_atomic_publication_v14 import store, rows
from tests.test_core_p0_4030_epub_evidence import _local_epub, META
from tests.test_core_p0_4030_suggestion_operator import Config


def _row(record="one", **changes):
    return replace(SearchResult(
        id="public:" + record, record_id=record, docbase="CATALOGUE",
        medium="digital", title="Roman commun", author="Autrice", type="ebook",
        format="EPUB", publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario="PUBLIC",
        source_provider="new-catalogue", source_credential="__public__",
        source_collection="public_catalog", owned=False, downloadable=False,
        metadata={"isbn": "9780306406157"},
    ), **changes)


@pytest.fixture
def rig(store, tmp_path, request):
    opf_extra = getattr(request, "param", "")
    root, path, pid = _local_epub(store, tmp_path, META +
        '<dc:identifier xmlns:dc="http://purl.org/dc/elements/1.1/">'
        'urn:isbn:9780306406157</dc:identifier>' + opf_extra)
    owner = NS(identity_id="A", directory_name="A")
    service = NS(storage=store, config=Config(root), current_identity=owner,
                 identity_by_id=lambda i: owner if i == "A" else None,
                 _source_context_epoch=1, _account_context_epoch=1,
                 _provider_enabled_for_identity=lambda p, i: p == "new-catalogue" and i == "A")
    calls = []
    exact = _row()
    ambiguous = _row("two", metadata={"isbns": ["9780306406157", "9780140328721"]})
    private = _row("private", owned=True, source_account_id="PRIVATE")
    unclaimed = _row("unclaimed")
    envelope = ProviderSearchResponse(
        [exact, ambiguous, private, unclaimed],
        public_notices=(PublicSearchNotice(exact), PublicSearchNotice(ambiguous)),
    )

    def search(query, scope, **kwargs):
        calls.append((query, scope, kwargs))
        return envelope

    session = NS(provider_id="new-catalogue", credential_id="__public__", connected=True,
                 credentials={}, _accounts=[], capabilities=NS(search=True),
                 search_capabilities=ProviderSearchCapabilities(fields=("title",), scopes=("digital",)),
                 search_with_response_facets=search)
    service._sessions = {"public": session}
    request = admit_local_origin_suggestion(service, identity_id="A", placement_id=pid, confirmed=True)
    assert request is not None
    return service, session, request, calls, path


def _run(service, request, **kwargs):
    return search_local_public_catalogue(service, request, provider_id="new-catalogue",
                                         **{"confirmed": True, **kwargs})


_OPF_URL = "https://catalogue.example.org/books/source-edition"


def _opf_claim(field, value):
    return (f'<dc:{field} xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'{escape(value)}</dc:{field}>')


@pytest.mark.parametrize("rig,case", [
    (_opf_claim("source", _OPF_URL), "source"),
    (_opf_claim("identifier", _OPF_URL), "identifier"),
    (_opf_claim("source", _OPF_URL), "unattested"),
    (_opf_claim("source", _OPF_URL), "duplicate"),
    (_opf_claim("source", _OPF_URL), "title_conflict"),
    (_opf_claim("source", _OPF_URL + "/"), "different_literal"),
    (_opf_claim("source", _OPF_URL) + _opf_claim("source", _OPF_URL + "-2"), "multiple_claims"),
    (_opf_claim("source", _OPF_URL + "?token=synthetic"), "delivery_token"),
], indirect=["rig"])
def test_explicit_catalogue_compares_only_literal_corroborated_opf_url(rig, case):
    service, session, request, calls, path = rig
    before, data = rows(service.storage), path.read_bytes()
    url = _OPF_URL + "?token=synthetic" if case == "delivery_token" else _OPF_URL
    marker = "new-catalogue.public_search_product_card"
    exact = _row("source", friendly_url=url, metadata=(
        {} if case == "unattested" else {"public_url_evidence": marker}
    ), title="Autre titre" if case == "title_conflict" else "Roman commun")
    other = _row("isbn")
    if case == "duplicate":
        other = replace(other, friendly_url=url,
                        metadata={**other.metadata, "public_url_evidence": marker})
    supplied = [other, exact]

    def search(query, scope, **kwargs):
        calls.append((query, scope, kwargs))
        return ProviderSearchResponse(
            supplied, public_notices=tuple(PublicSearchNotice(row) for row in supplied),
        )

    session.search_with_response_facets = search
    result = _run(service, request)
    assert result["status"] == "partial"
    assert calls == [("Roman commun", "digital", {"field": "title", "content_types": ()})]
    if case in ("source", "identifier"):
        assert [(r.record_id, r.comparison) for r in result["candidates"]] == [
            ("source", "opf_source_url_concordant"), ("isbn", "isbn_title_author_concordant"),
        ]
    else:
        assert len(result["candidates"]) == 2
        assert all(r.comparison != "opf_source_url_concordant" for r in result["candidates"])
    assert url not in repr(result)
    assert not result["association_authorized"] and not result["may_write_origin_or_right"]
    assert rows(service.storage) == before and path.read_bytes() == data


@pytest.mark.parametrize("rig", [_opf_claim("source", _OPF_URL)], indirect=True)
@pytest.mark.parametrize("other_kind", ["unattested", "excluded", "unclaimed"])
def test_opf_url_upgrade_refuses_other_returned_occurrence(rig, other_kind):
    service, session, request, calls, path = rig
    marker = "new-catalogue.public_search_product_card"
    exact = _row("source", friendly_url=_OPF_URL,
                 metadata={"public_url_evidence": marker})
    other = _row("other", friendly_url=_OPF_URL, metadata={})
    if other_kind == "excluded":
        other = replace(other, owned=True, source_account_id="PRIVATE")
    supplied = [exact, other]
    claims = (PublicSearchNotice(exact),)
    if other_kind != "unclaimed":
        claims += (PublicSearchNotice(other),)

    def search(query, scope, **kwargs):
        calls.append((query, scope, kwargs))
        return ProviderSearchResponse(supplied, public_notices=claims)

    session.search_with_response_facets = search
    before, contents = rows(service.storage), path.read_bytes()
    result = _run(service, request)
    assert result["status"] == "partial"
    assert any(row.record_id == "source" for row in result["candidates"])
    assert all(row.comparison != "opf_source_url_concordant"
               for row in result["candidates"])
    assert len(calls) == 1 and result["provider_access_performed"]
    assert not result["association_authorized"] and not result["may_write_origin_or_right"]
    assert _OPF_URL not in repr(result)
    assert rows(service.storage) == before and path.read_bytes() == contents


def test_explicit_public_lookup_keeps_isbn_ambiguity_without_persistence(rig):
    service, session, request, calls, path = rig
    before, data = rows(service.storage), path.read_bytes()
    result = _run(service, request)
    assert result["status"] == "partial"
    assert calls == [("Roman commun", "digital", {"field": "title", "content_types": ()})]
    assert [(r.record_id, r.comparison) for r in result["candidates"]] == [
        ("one", "isbn_claim_ambiguous"), ("two", "isbn_claim_ambiguous"),
    ]
    assert result["provider_access_performed"]
    assert not any(result[k] for k in ("coverage_complete", "fresh_network_verified",
                                     "association_authorized", "may_write_origin_or_right"))
    assert "PRIVATE" not in repr(result)
    assert rows(service.storage) == before and path.read_bytes() == data


@pytest.mark.parametrize("change", ["confirmation", "private", "credentials", "accounts",
    "capabilities", "disabled", "duplicate", "navigation", "changed_bytes"])
def test_unadmitted_public_lookup_never_dispatches(rig, change):
    service, session, request, calls, path = rig
    options = {}
    if change == "confirmation":
        options["confirmed"] = False
    elif change == "private":
        session.credential_id = "private-card"
    elif change == "credentials":
        session.credentials = {"secret": "private"}
    elif change == "accounts":
        session._accounts.append(object())
    elif change == "capabilities":
        session.search_capabilities = ProviderSearchCapabilities()
    elif change == "disabled":
        service._provider_enabled_for_identity = lambda *a: False
    elif change == "duplicate":
        service._sessions["other"] = session
    elif change == "navigation":
        service._account_context_epoch += 2
    else:
        path.write_bytes(b"changed")
    result = _run(service, request, **options)
    assert result["status"] == "excluded"
    assert not result["provider_access_performed"] and calls == []


@pytest.mark.parametrize("registry", ["over_budget", "untyped"])
def test_confirmed_public_search_refuses_unbounded_or_untrusted_registry(rig, registry):
    service, session, request, calls, path = rig
    before, contents = rows(service.storage), path.read_bytes()
    if registry == "over_budget":
        service._sessions.update({f"extra-{i}": object() for i in range(256)})
    else:
        service._sessions = [session]
    result = _run(service, request)
    assert result["status"] == "excluded"
    assert result["provider_access_performed"] is False
    assert calls == []
    assert rows(service.storage) == before and path.read_bytes() == contents


def test_public_discovery_caps_sessions_before_enumerating_sources(rig):
    from cifereca.core.local_origin_public_catalogue import available_local_public_catalogues
    from cifereca.core.local_origin_suggestion_preview import _PublicComparisonEvidence

    service, _session, request, calls, path = rig
    evidence = _PublicComparisonEvidence(
        request.storage, request.root, request.identity_id,
        request.placement_id, (), (),
    )
    class UntrustedRegistry(dict):
        def values(self):
            pytest.fail("non-native registry must never be enumerated")

    service._sessions = UntrustedRegistry(service._sessions)
    assert available_local_public_catalogues(service, request, evidence=evidence) == ()
    service._sessions = {f"extra-{i}": object() for i in range(257)}
    assert available_local_public_catalogues(service, request, evidence=evidence) == ()
    assert calls == [] and path.is_file()


@pytest.mark.parametrize("change", ["navigation", "source", "bytes", "failure", "overflow"])
def test_changed_or_invalid_response_is_not_a_candidate(rig, change):
    service, session, request, calls, path = rig
    original = session.search_with_response_facets

    def changed(*args, **kwargs):
        reply = original(*args, **kwargs)
        if change == "navigation":
            service._account_context_epoch += 2
        elif change == "source":
            service._sessions = {}
        elif change == "bytes":
            path.write_bytes(b"changed")
        elif change == "failure":
            raise RuntimeError("PRIVATE supplier error")
        else:
            return ProviderSearchResponse([_row(str(i)) for i in range(51)])
        return reply

    session.search_with_response_facets = changed
    result = _run(service, request)
    assert result["status"] in ("scope_changed", "unavailable", "response_unverified")
    assert len(calls) == 1 and result["provider_access_performed"]
    assert result["candidates"] == () and "PRIVATE" not in repr(result)


@pytest.mark.parametrize("other_format", ["EPUB", "PDF"])
def test_public_same_isbn_distinct_editions_remain_ambiguous(rig, other_format):
    service, session, request, calls, path = rig
    first, second = _row("one"), _row("two", format=other_format)
    session.search_with_response_facets = lambda *a, **kw: ProviderSearchResponse(
        [first, second], public_notices=(PublicSearchNotice(first), PublicSearchNotice(second)))
    before, data = rows(service.storage), path.read_bytes()
    result = _run(service, request)
    assert result["status"] == "partial"
    assert [(x.record_id, x.comparison) for x in result["candidates"]] == [
        ("one", "isbn_claim_ambiguous"), ("two", "isbn_claim_ambiguous")]
    assert not result["association_authorized"] and not result["may_write_origin_or_right"]
    assert rows(service.storage) == before and path.read_bytes() == data


def test_public_evidence_ranks_exact_concordance_before_weak_descriptive_hits(rig):
    """Provider order is not evidence order; a result is still only a notice."""
    service, session, request, calls, path = rig
    weak = _row("weak", title="Different work", author="Other",
                metadata={})
    descriptive = _row("descriptive", metadata={})
    exact = _row("exact", metadata={"isbn": "9780306406157"})
    result_rows = [weak, descriptive, exact]
    session.search_with_response_facets = lambda *_a, **_kw: ProviderSearchResponse(
        result_rows, public_notices=tuple(PublicSearchNotice(x) for x in result_rows))
    before, contents = rows(service.storage), path.read_bytes()
    result = _run(service, request)
    assert result["status"] == "partial"
    assert [(x.record_id, x.comparison) for x in result["candidates"]] == [
        ("exact", "isbn_title_author_concordant"),
        ("descriptive", "title_author_concordant"),
        ("weak", "title_related"),
    ]
    assert result["provider_access_performed"]
    assert not result["coverage_complete"] and not result["fresh_network_verified"]
    assert not result["association_authorized"] and not result["may_write_origin_or_right"]
    assert rows(service.storage) == before and path.read_bytes() == contents


def test_public_rank_keeps_ambiguous_editions_and_supplier_tie_order(rig):
    service, session, request, calls, path = rig
    first, second = _row("z"), _row("a", format="PDF")
    session.search_with_response_facets = lambda *_a, **_kw: ProviderSearchResponse(
        [first, second], public_notices=(PublicSearchNotice(first), PublicSearchNotice(second)))
    result = _run(service, request)
    assert [(c.record_id, c.comparison) for c in result["candidates"]] == [
        ("z", "isbn_claim_ambiguous"), ("a", "isbn_claim_ambiguous"),
    ]
    assert not result["association_authorized"] and not result["may_write_origin_or_right"]


@pytest.mark.parametrize("field", ["title", "author"])
@pytest.mark.parametrize("spoof", ["\x85", "\u202e", "\u2028", "\u200d", "\u2066"])
def test_public_catalogue_rejects_unprintable_comparison_labels_without_losing_safe_hits(
    rig, field, spoof,
):
    service, session, request, calls, path = rig
    safe = _row("safe")
    forged = _row("forged", **{field: "Roman" + spoof + "FORGED"})
    session.search_with_response_facets = lambda *_a, **_kw: ProviderSearchResponse(
        [safe, forged],
        public_notices=(PublicSearchNotice(safe), PublicSearchNotice(forged)),
    )
    before, data = rows(service.storage), path.read_bytes()
    result = _run(service, request)
    assert result["status"] == "partial"
    assert tuple(candidate.record_id for candidate in result["candidates"]) == ("safe",)
    assert len(calls) == 0  # This custom response is purely local to the fixture.
    assert not result["association_authorized"] and not result["may_write_origin_or_right"]
    assert not result["fresh_network_verified"] and not result["coverage_complete"]
    assert (rows(service.storage), path.read_bytes()) == (before, data)


@pytest.mark.parametrize("field", ["record_id", "docbase", "format"])
@pytest.mark.parametrize("spoof", ["\x85", "\u202e", "\u2028", "\u200d", "\u2066"])
def test_public_comparison_drops_spoofed_source_coordinates_but_keeps_valid_hits(
    rig, field, spoof,
):
    service, session, request, calls, path = rig
    safe = _row("safe")
    forged = _row("forged", **{field: "misleading" + spoof + "SOURCE"})
    session.search_with_response_facets = lambda *_a, **_kw: ProviderSearchResponse(
        [safe, forged],
        public_notices=(PublicSearchNotice(safe), PublicSearchNotice(forged)),
    )
    before, original = rows(service.storage), path.read_bytes()
    report = _run(service, request)
    assert report["status"] == "partial"
    assert tuple(item.record_id for item in report["candidates"]) == ("safe",)
    assert report["provider_access_performed"]
    assert not report["association_authorized"] and not report["may_write_origin_or_right"]
    assert rows(service.storage) == before and path.read_bytes() == original


def test_same_isbn_same_public_coordinates_but_two_rows_stays_ambiguous(rig):
    from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
    service, session, request, calls, path = rig
    first = _row("same")
    second = replace(_row("same"), id="public:same:other-row")
    def search(query, scope, **kwargs):
        calls.append((query, scope, kwargs))
        return ProviderSearchResponse(
            [first, second],
            public_notices=(PublicSearchNotice(first), PublicSearchNotice(second)),
        )
    session.search_with_response_facets = search
    before, data = rows(service.storage), path.read_bytes()
    report = _run(service, request)
    assert report["status"] == "partial"
    assert [(m.record_id, m.media_format, m.comparison) for m in report["candidates"]] == [
        ("same", "EPUB", "isbn_claim_ambiguous"),
        ("same", "EPUB", "isbn_claim_ambiguous"),
    ]
    assert len(calls) == 1 and report["provider_access_performed"]
    assert not report["coverage_complete"] and not report["association_authorized"]
    assert rows(service.storage) == before and path.read_bytes() == data


def test_single_unique_public_source_preserves_corroborated_isbn_strength(rig):
    from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
    service, session, request, calls, path = rig
    one = _row("unique")
    session.search_with_response_facets = lambda *_args, **_kw: ProviderSearchResponse(
        [one], public_notices=(PublicSearchNotice(one),),
    )
    before, data = rows(service.storage), path.read_bytes()
    report = _run(service, request)
    assert report["status"] == "partial"
    hit, = report["candidates"]
    assert hit.record_id == "unique" and hit.comparison == "isbn_title_author_concordant"
    assert not report["association_authorized"]
    assert rows(service.storage) == before and path.read_bytes() == data
