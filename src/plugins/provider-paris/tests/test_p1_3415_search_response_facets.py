"""P1 #3415: descriptive Syracuse facets on the exact response page."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread

import httpx
import pytest

from cifereca_paris_bibliography import project_search_response_facets
from cifereca_paris_client import ParisClient, ParisSearchUnavailable
from cifereca_paris import ParisProviderSession


def _facet(field="Subj", label="Sujets", *, values=None, **extra):
    return {
        "FacetField": field, "FacetLabel": label,
        "FacetList": [] if values is None else values,
        "IsSearchable": True, "IsMultiselectable": False,
        **extra,
    }


def _response(scenario, *facets):
    return {
        "success": True,
        "d": {
            "Results": [{
                "Resource": {
                    "RscId": "synthetic-" + scenario,
                    "RscBase": "DILICOM" if scenario == "EBOOKS" else "SYRACUSE",
                    # The response descriptors are page-scoped; positive
                    # medium evidence belongs to each fixture record.
                    "Type": "Livre numérique" if scenario == "EBOOKS" else "Livre",
                    "Frmt": "MONE" if scenario == "EBOOKS" else "MONO",
                    "Ttl": "Synthetic book",
                },
            }],
            "FacetCollectionList": list(facets),
        },
    }


def _client(responder):
    client = ParisClient("https://example.test")
    client.client.close()
    client.client = httpx.Client(
        transport=httpx.MockTransport(responder), trust_env=False,
    )
    return client


@pytest.mark.parametrize("partial", [False, True])
def test_concurrent_searches_return_their_own_page_facets_and_coverage(partial):
    def respond(request):
        query = json.loads(request.content)["query"]
        text = query["QueryString"]
        scenario = query["ScenarioCode"]
        if partial and text == "Première" and scenario == "EBOOKS":
            return httpx.Response(503)
        result = _response(scenario, _facet(label=f"{text} {scenario}", values=[
            {"Label": text, "Count": 7, "IsSelected": False},
        ]))
        result["d"]["Results"][0]["Resource"]["Ttl"] = text
        return httpx.Response(200, json=result)

    client = _client(respond)
    session = ParisProviderSession(
        credential_id="synthetic", label="Paris", credentials={},
        options={}, config={}, diagnostics=None,
    )
    session.client.close()
    session.client = client
    paused = Event()
    resume = Event()
    original_timed = client._timed

    def timed(category, operation, action):
        result = original_timed(category, operation, action)
        if current_thread().name.startswith("first-search") and operation == "search CATALOGUE":
            paused.set()
            assert resume.wait(5)
        return result

    client._timed = timed
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="first-search") as executor:
            first = executor.submit(session.search_with_response_facets, "Première")
            try:
                assert paused.wait(5)
                second = session.search_with_response_facets("Seconde", "digital")
            finally:
                resume.set()
            first = first.result(timeout=5)
        assert [item.title for item in first.results] == ["Première"] * (1 if partial else 2)
        assert [item.title for item in second.results] == ["Seconde"]
        expected = [("CATALOGUE", 0, "Première CATALOGUE")]
        if not partial:
            expected.append(("EBOOKS", 0, "Première EBOOKS"))
        assert [(facet.scenario, facet.page, facet.label) for facet in first.facets] == expected
        assert [facet.label for facet in second.facets] == ["Seconde EBOOKS"]
        assert first.partial is partial and second.partial is False
        assert first.facets[0].values[0].count == 7
        assert second.facets[0].filter_capability == "unverified"
        assert all("search_response_facets" not in item.metadata for item in first.results)
    finally:
        session.close()


@pytest.mark.parametrize("scenario", ("CATALOGUE", "EBOOKS"))
def test_observed_response_descriptor_is_page_scoped_not_per_book(scenario):
    captures = []
    def respond(request):
        captures.append(json.loads(request.content))
        return httpx.Response(200, json=_response(scenario, _facet(
            values=[{"shape": "not_attested"}], IsMultiselectable=True,
        )))
    client = _client(respond)
    try:
        rows = client.search("science", scope="digital" if scenario == "EBOOKS" else "physical", page=2)
        assert len(rows) == 1
        assert "search_response_facets" not in rows[0].metadata
        assert "search_facets" not in rows[0].metadata
        assert "filter_capabilities" not in rows[0].metadata
        assert captures[0]["query"]["Page"] == 2
        assert client.last_search_response_facets == [{
            "scenario": scenario, "page": 2,
            "field": "Subj", "label": "Sujets",
            "upstream_is_searchable": True,
            "upstream_is_multiselectable": True,
            "observed_entry_count": 1,
            "source": "SearchService.d.FacetCollectionList",
            "scope": "response_page_only",
            "filter_capability": "unverified",
            "values_schema": "unverified",
        }]
    finally:
        client.close()


def test_all_scopes_keep_facets_in_their_own_branch_and_reset_between_queries():
    calls = []
    def respond(request):
        body = json.loads(request.content)
        scenario = body["query"]["ScenarioCode"]
        calls.append(scenario)
        facets = [_facet(label="Physique" if scenario == "CATALOGUE" else "Numérique")]
        return httpx.Response(200, json=_response(scenario, *facets))
    client = _client(respond)
    try:
        assert len(client.search("first", scope="all")) == 2
        assert [x["scenario"] for x in client.last_search_response_facets] == ["CATALOGUE", "EBOOKS"]
        assert [x["label"] for x in client.last_search_response_facets] == ["Physique", "Numérique"]
        before = list(client.last_search_response_facets)
        assert len(client.search_catalog("cover", scenario="CATALOGUE")) == 1
        assert client.last_search_response_facets == before
        assert len(client.search("second", scope="digital")) == 1
        assert [x["scenario"] for x in client.last_search_response_facets] == ["EBOOKS"]
        assert calls == ["CATALOGUE", "EBOOKS", "CATALOGUE", "EBOOKS"]
    finally:
        client.close()


@pytest.mark.parametrize("failed", ("CATALOGUE", "EBOOKS"))
def test_failed_branch_does_not_contribute_stale_facets(failed):
    def respond(request):
        scenario = json.loads(request.content)["query"]["ScenarioCode"]
        if scenario == failed:
            return httpx.Response(403, json={"success": False, "message": "denied"})
        return httpx.Response(200, json=_response(scenario, _facet()))
    client = _client(respond)
    try:
        rows = client.search("mixed", scope="all")
        assert len(rows) == 1
        other = "CATALOGUE" if failed == "EBOOKS" else "EBOOKS"
        assert [x["scenario"] for x in client.last_search_response_facets] == [other]
        assert client.last_search_warnings[0].startswith("partial_results")
        with pytest.raises(ParisSearchUnavailable):
            client.search("denied", scope="digital" if failed == "EBOOKS" else "physical")
        assert client.last_search_response_facets == []
    finally:
        client.close()


def test_broad_query_retry_only_admits_facets_from_successful_smaller_page():
    sent = []
    def respond(request):
        body = json.loads(request.content)
        size = body["query"]["ResultSize"]
        sent.append(size)
        if size == 50:
            return httpx.Response(400, json={"success": False, "message": "too broad"})
        return httpx.Response(200, json=_response("EBOOKS", _facet(label="After retry")))
    client = _client(respond)
    try:
        client.search("broad", scope="digital", page=1)
        assert sent == [50, 20]
        assert [x["label"] for x in client.last_search_response_facets] == ["After retry"]
        assert client.last_search_response_facets[0]["page"] == 1
    finally:
        client.close()


def test_untrusted_nested_shapes_do_not_imply_facet_values_or_server_filtering():
    response = {"d": {"FacetCollectionList": [
        _facet(values=[{"FacetValue": "Unproven server format"}]),
        _facet(field="Subj", label="duplicate"),
        _facet(field="<script>", label="bad", values=[]),
        _facet(field="Subject", label="&#60;img onerror=alert(1)&#62;", values=[]),
        _facet(field="Audience", label="Audience", values="not-a-list"),
        _facet(field="Type", label="Type", IsSearchable="true"),
        _facet(field="Numerique", label="Numérique", IsMultiselectable=1),
    ]}}
    projected = project_search_response_facets(response, "CATALOGUE", 0)
    assert [x["field"] for x in projected] == ["Subj", "Type", "Numerique"]
    assert projected[0]["observed_entry_count"] == 1
    assert "Unproven server format" not in str(projected)
    assert projected[1]["upstream_is_searchable"] is None
    assert projected[2]["upstream_is_multiselectable"] is None
    assert all(row["filter_capability"] == "unverified" for row in projected)
    assert project_search_response_facets(response, "UNKNOWN", 0) == []
    assert project_search_response_facets(response, "CATALOGUE", -1) == []
    assert project_search_response_facets({"d": {"FacetCollectionList": _facet()}}, "CATALOGUE", 0) == []
    assert project_search_response_facets({"d": {"FacetCollectionList": [None] * 65}}, "CATALOGUE", 0) == []


@pytest.mark.parametrize("scenario", ("CATALOGUE", "EBOOKS"))
def test_age_like_syracuse_facet_is_page_scope_not_book_age(scenario):
    """#4530: a youth facet describes a response page, not an exact edition."""
    response = _response(scenario, _facet(
        field="Audience", label="Public / âge",
        values=[
            {"Label": "Dès 8 ans", "Count": 3, "IsSelected": False},
            {"Label": "13-99", "Count": 2, "IsSelected": True},
        ],
    ))
    # Subject and category labels are bibliographic facts, not numeric age.
    response["d"]["Results"][0]["Resource"]["Subj"] = ["Jeunesse", "Dès 8 ans"]
    response["d"]["Results"][0]["Resource"]["Tags"] = ["13-99"]
    client = _client(lambda _request: httpx.Response(200, json=response))
    try:
        rows = client.search(
            "synthetic", scope="digital" if scenario == "EBOOKS" else "physical",
        )
        assert len(rows) == 1
        metadata = rows[0].metadata
        for key in ("recommended_age", "youth_age_ranges",
                    "attested_age_ranges", "attested_age_status",
                    "public_age_range_label", "search_response_facets"):
            assert key not in metadata, key
        descriptors = client.last_search_response_facets
        assert len(descriptors) == 1
        facet = descriptors[0]
        assert facet["field"] == "Audience"
        assert facet["scope"] == "response_page_only"
        assert facet["filter_capability"] == "unverified"
        assert facet["values_schema"] == "label_count_selected_v1"
        assert [v["label"] for v in facet["values"]] == [
            "Dès 8 ans", "13-99",
        ]
        assert facet["page"] == 0 and facet["scenario"] == scenario
    finally:
        client.close()


def test_age_like_syracuse_facet_is_not_carried_to_following_query():
    first = _response("EBOOKS", _facet(
        field="Audience", label="Âge", values=[
            {"Label": "12-15", "Count": 7, "IsSelected": False},
        ],
    ))
    second = _response("EBOOKS")
    call_count = 0

    def respond(_request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=first if call_count == 1 else second)

    client = _client(respond)
    try:
        assert len(client.search("first", scope="digital")) == 1
        assert len(client.last_search_response_facets) == 1
        assert len(client.search("second", scope="digital")) == 1
        assert client.last_search_response_facets == []
    finally:
        client.close()


def test_response_with_no_facets_is_not_false_zero_server_facet_claim():
    client = _client(lambda request: httpx.Response(200, json=_response("CATALOGUE")))
    try:
        rows = client.search("q", scope="physical")
        assert len(rows) == 1
        assert client.last_search_warnings == []
        assert client.last_search_response_facets == []
    finally:
        client.close()


@pytest.mark.parametrize("scenario,scope", [
    ("CATALOGUE", "physical"), ("EBOOKS", "digital"),
])
def test_paris_public_notices_from_same_anonymous_response(scenario, scope):
    """The real Paris session attests only current SearchService catalogue rows."""
    from cifereca.core.public_search_projection import project_public_search_result

    payload = _response(scenario, _facet())
    payload["d"]["Results"][0]["FriendlyUrl"] = "https://books.example.org/item"
    payload["d"]["Results"][0]["Resource"]["Desc"] = "Private token=never-publish"
    client = _client(lambda _req: httpx.Response(200, json=payload))
    session = ParisProviderSession(
        credential_id="synthetic", label="Paris", credentials={},
        options={}, config={}, diagnostics=None,
    )
    session.client.close()
    session.client = client
    try:
        response = session.search_with_response_facets("synthetic", scope)
        assert len(response.results) == len(response.public_notices) == 1
        row = response.results[0]
        declaration = response.public_notices[0]
        assert declaration.result is not row
        assert declaration.result.id == row.id
        assert declaration.result.download_url is None
        assert declaration.result.thumbnail_url is None
        assert declaration.result.friendly_url is None
        assert declaration.result.description is None
        assert declaration.result.metadata == {}
        assert declaration.result.source_account_id is None
        assert declaration.result.owned is False
        assert declaration.result.downloadable is False
        public = project_public_search_result(
            response, row, provider_id="paris", credential_id="synthetic",
        )
        assert public is not None
        assert public.metadata == {"catalogue_access": "shared_read_only"}
        assert public.download_url is public.thumbnail_url is public.friendly_url is None
        assert public.owned is public.downloadable is False
        assert len(response.facets) == 1
        assert response.partial is False
    finally:
        session.close()


def test_paris_failed_anonymous_search_never_reuses_previous_public_notice():
    """Failed and successful queries cannot inherit old cross-account proof."""
    from cifereca_paris_client import ParisSearchUnavailable

    state = {"fail": False}
    def respond(_request):
        if state["fail"]:
            return httpx.Response(403)
        return httpx.Response(200, json=_response("EBOOKS"))

    client = _client(respond)
    session = ParisProviderSession(
        credential_id="synthetic", label="Paris", credentials={},
        options={}, config={}, diagnostics=None,
    )
    session.client.close()
    session.client = client
    try:
        first = session.search_with_response_facets("first", "digital")
        assert len(first.public_notices) == 1
        state["fail"] = True
        with pytest.raises((ParisSearchUnavailable, httpx.HTTPError)):
            session.search_with_response_facets("second", "digital")
        assert first.public_notices[0].result is not first.results[0]
    finally:
        session.close()


def test_foreign_catalog_scope_uses_only_public_searchservice_routes():
    """#5869/#5806: Core calls foreign sessions using scope=catalog."""
    paths = []
    def respond(request):
        paths.append(request.url.path)
        assert "Search.svc/Search" in request.url.path
        scenario = json.loads(request.content)["query"]["ScenarioCode"]
        return httpx.Response(200, json=_response(scenario))

    client = _client(respond)
    session = ParisProviderSession(
        credential_id="synthetic", label="Paris", credentials={},
        options={}, config={}, diagnostics=None,
    )
    session.client.close()
    session.client = client
    try:
        assert "catalog" in session.descriptor.search_capabilities.scopes
        response = session.search_with_response_facets("public", "catalog")
        assert len(response.results) == len(response.public_notices) == 2
        assert {row.scenario for row in response.results} == {"CATALOGUE", "EBOOKS"}
        assert all(n.result.download_url is None for n in response.public_notices)
        assert len(paths) == 2
        assert all("Search.svc/Search" in path for path in paths)
    finally:
        session.close()


@pytest.mark.parametrize("scenario,scope", [
    ("CATALOGUE", "physical"), ("EBOOKS", "digital"),
])
def test_public_notice_series_is_exact_per_result_and_not_a_page_facet(scenario, scope):
    """#1078/#6366: parser -> exact same-call public declaration; no new I/O."""
    from copy import deepcopy
    from cifereca.core.public_search_projection import project_public_search_results

    payload = _response(scenario, _facet(
        field="SeriesTitle_exact", label="Série",
        values=[{"Label": "Global facet only", "Count": 7, "IsSelected": False}],
    ))
    good = payload["d"]["Results"][0]
    good["Resource"]["Subj"] = ["Subject not selected by this declaration"]
    good["CustomResult"] = '<a href="/Search?field=SeriesTitle_idx">Cycle A</a>'
    other = deepcopy(good)
    other["Resource"]["RscId"] += "-other"
    other["CustomResult"] = '<a href="/Search?field=SeriesTitle_exact">Cycle B</a>'
    other["CompactResult"] = '<a href="/Search?field=SeriesTitle_idx">Conflicting</a>'
    facet_only = deepcopy(good)
    facet_only["Resource"]["RscId"] += "-facet"
    del facet_only["CustomResult"]
    local = deepcopy(good)
    local["Resource"]["RscId"] += "-local"
    local["CustomResult"] = '<a href="/Search?field=SeriesTitle_idx">/home/reader/private</a>'
    payload["d"]["Results"] = [good, other, facet_only, local]
    calls = []

    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=payload)

    session = ParisProviderSession(
        credential_id="synthetic", label="Paris", credentials={},
        options={}, config={}, diagnostics=None,
    )
    session.client.close()
    session.client = _client(respond)
    try:
        response = session.search_with_response_facets("synthetic", scope)
        assert len(calls) == 1
        assert len(response.results) == len(response.public_notices) == 4
        rows = {row.record_id: row for row in response.results}
        declared = {claim.result.record_id: claim.result for claim in response.public_notices}
        source_id = good["Resource"]["RscId"]
        first = rows[source_id]
        public = declared[source_id]
        assert public.metadata == {
            "series": ["Cycle A"],
            "bibliography_evidence": {"series": {
                "source": "paris.syracuse.search",
                "source_field": "Search.CustomResult",
                "scope": scenario,
                "confidence": "linked_search_relation",
                "freshness": "search_response",
            }},
        }
        assert public.metadata["series"] is not first.metadata["series"]
        assert public.metadata["bibliography_evidence"]["series"] is not (
            first.metadata["bibliography_evidence"]["series"]
        )
        for suffix in ("-other", "-facet", "-local"):
            assert declared[source_id + suffix].metadata == {}
        projected = project_public_search_results(response)
        assert projected is not None and len(projected) == 4
        # Core's bounded structured-evidence projection is an independent
        # migration. If present it must match the declaration exactly.
        projected_metadata = dict(projected[id(first)].metadata)
        evidence = projected_metadata.pop("bibliography_evidence", None)
        if evidence is not None:
            assert evidence == public.metadata["bibliography_evidence"]
        assert projected_metadata == {
            "catalogue_access": "shared_read_only", "series": ["Cycle A"],
        }
        for suffix in ("-other", "-facet", "-local"):
            assert projected[id(rows[source_id + suffix])].metadata == {
                "catalogue_access": "shared_read_only",
            }
        assert all(not row.owned and not row.downloadable and row.download_url is None
                   for row in projected.values())
    finally:
        session.close()


@pytest.mark.parametrize("scenario,scope", [
    ("CATALOGUE", "physical"), ("EBOOKS", "digital"),
])
def test_exact_anonymous_series_reaches_durable_c_from_paris_parser(
    tmp_path, scenario, scope,
):
    """Synthetic HTTP, actual Paris parser and Core same-call/SQLite path."""
    from types import SimpleNamespace
    from cifereca.core.search_orchestration import (
        _public_rows_from_response, _mirror_remote_results,
    )
    from cifereca.core.notice_cover_repository import read_notice_cover, evict_notice_cover
    from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
    from cifereca.core.notice_c_public_search import search_public_notice_c
    from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
    from cifereca.storage import Storage

    payload = _response(scenario, _facet(field="SeriesTitle_exact", label="Série"))
    record = payload["d"]["Results"][0]
    record["CustomResult"] = '<a href="/Search?field=SeriesTitle_idx">Cycle astral</a>'
    record["Resource"]["Desc"] = "token=not-for-publication"
    calls = []
    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=payload)

    session = ParisProviderSession(
        credential_id="__public__", label="Paris", credentials={},
        options={}, config={}, diagnostics=None,
    )
    session.client.close()
    session.client = _client(respond)
    path = tmp_path / "paris-notice.sqlite"
    db = Storage(path)
    try:
        response = session.search_with_response_facets("Synthetic book", scope)
        raw, = response.results
        declaration, = response.public_notices
        assert declaration.result is raw
        assert raw.source_collection == "public_catalog"
        assert raw.description is raw.thumbnail_url is raw.friendly_url is None
        assert raw.metadata["series"] == ["Cycle astral"]
        row, = _public_rows_from_response(response, session)
        assert _mirror_remote_results(
            SimpleNamespace(storage=db), [row], identity_id="alice",
            require_public_cover_witness=True,
        ) == 1
        exact = row._core_notice_cover_document_ref
        canonical = row._core_notice_canonical_cover_document_ref
        source = read_notice_cover(db, exact)
        assert source.attribute.state == "pending"
        relation = read_consolidated_notice_cover(
            db, canonical,
        ).sources[0].public_bibliography.series_relation
        assert relation.label == "Cycle astral"
        assert relation.scope == scenario
        assert relation.source_field == "Search.CustomResult"
        assert evict_notice_cover(db, exact, expected_epoch=source.epoch)
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        session.close()
        db.close()

    db = Storage(path)
    try:
        page = search_public_notice_c(db, "Synthetic book", group_by_document=True)
        known, = merge_public_notice_c_known_rows([], page, identity_id="bob")
        assert known["metadata"]["series"] == ["Cycle astral"]
        assert known["metadata"]["bibliography_evidence"]["series"]["scope"] == scenario
        assert known["_cover_document_ref"] == canonical
        assert db.entitlements() == [] and db.acquisitions() == []
        assert len(calls) == 1
    finally:
        db.close()


@pytest.mark.parametrize("mode", ["direct", "sync", "progressive", "headless"])
@pytest.mark.parametrize("scenario,scope", [
    ("CATALOGUE", "physical"), ("EBOOKS", "digital"),
])
def test_credential_catalogue_series_reaches_private_c_without_borrower_or_web(
    tmp_path, monkeypatch, scenario, scope, mode,
):
    from dataclasses import replace
    from cifereca.config import Config
    from cifereca.diagnostics import Diagnostics
    from cifereca.services import LibraryService
    from cifereca.storage import Storage
    from cifereca.models import Account
    from cifereca.core.notice_c_private_series import (
        capture_series_call, stamp_series_rows, persist_series_rows,
        read_series_for_search, _key,
    )
    from cifereca.core.notice_c_cover_index import (
        canonical_cover_document_ref, read_consolidated_notice_cover,
    )
    from cifereca.core.notice_cover_projection import notice_cover_document_ref
    from cifereca.core.notice_cover_repository import read_notice_cover, evict_notice_cover
    from cifereca.core.notice_c_public_search import search_public_notice_c
    from cifereca.core.search_orchestration import capture_search_read_scope

    payload = _response(scenario, _facet(field="SeriesTitle_exact", label="Page facet"))
    payload["d"]["Results"][0]["CustomResult"] = (
        '<a href="/Search?field=SeriesTitle_idx">Cycle astral</a>'
    )
    calls = []
    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=payload)

    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "library"))
    config.set("identities", [{"id": "alice", "label": "Alice"},
                              {"id": "bob", "label": "Bob"}])
    config.set("providers.accounts", [])
    path = tmp_path / "private-series.sqlite"
    service = LibraryService(config, Storage(path), Diagnostics())
    session = ParisProviderSession(
        credential_id="synthetic", label="Paris",
        credentials={"username": "fixture", "password": "fixture"},
        options={}, config={}, diagnostics=None,
    )
    session.client.close()
    session.client = _client(respond)
    session.connected = True
    session._accounts = [
        Account(identity_id="alice", provider_id=session.provider_id,
                credential_id=session.credential_id, unique_id=identifier)
        for identifier in ("", "secondary")
    ]
    service.accounts = list(session._accounts)
    service._sessions = {service._session_key(session.provider_id, session.credential_id): session}
    monkeypatch.setattr(service, "_provider_enabled_for_identity", lambda p, i=None: True)
    db = service.storage
    db.provider_connection_put({
        "identity_id": "alice", "provider_id": session.provider_id,
        "credential_id": session.credential_id, "enabled": True,
    })
    try:
        assert capture_series_call(service, session, "bob") is None
        assert service.current_identity.identity_id == "alice"
        lease = capture_search_read_scope(service, "alice")
        if mode == "direct":
            call = capture_series_call(service, session, "alice")
            assert call is not None
            response = session.search_with_response_facets("Synthetic book", scope)
            row, = response.results
            assert response.public_notices[0].result is not row
            stamp_series_rows(service, call, response.results)
            assert persist_series_rows(service, [replace(row)], identity_id="alice") == 0
            assert persist_series_rows(service, response.results, identity_id="alice") == 1
        elif mode == "sync":
            row, = service.search(
                "Synthetic book", scope=scope, provider_id=session.provider_id, force=True,
            )
        elif mode == "progressive":
            batches = []

            def receive(batch):
                actual, = batch.results
                assert read_series_for_search(service, actual, reader_scope=lease).label == "Cycle astral"
                batches.append(actual)

            summary = service.search_progressive(
                "Synthetic book", scope=scope, provider_id=session.provider_id,
                force=True, on_batch=receive,
            )
            assert not summary.cancelled and summary.result_count == 1
            row, = batches
        else:
            summary, rows = service.search_progressive_result_for_scope(
                lease, "Synthetic book", scope=scope, provider_id=session.provider_id, force=True,
            )
            assert not summary.cancelled and summary.result_count == 1
            row, = rows
        assert row.source_account_id is None
        assert persist_series_rows(service, [replace(row)], identity_id="alice") == 0
        key = _key(row, "alice")
        assert key.account_id is None and key.visibility == "private"
        document = notice_cover_document_ref(key)
        canonical = canonical_cover_document_ref(key.edition_id)
        source = read_notice_cover(db, document)
        member, = read_consolidated_notice_cover(db, canonical).sources
        assert member.private_series_digest is not None
        assert member.public_bibliography is None and member.private_web_digest is None
        lease = capture_search_read_scope(service, "alice")
        assert read_series_for_search(service, row, reader_scope=lease).label == "Cycle astral"
        assert read_series_for_search(service, row, reader_scope=capture_search_read_scope(
            service, "bob",
        )) is None
        row.metadata["series"] = ["Other"]
        assert persist_series_rows(service, [row], identity_id="alice") == 0
        assert read_series_for_search(service, row, reader_scope=lease).label == "Cycle astral"
        assert search_public_notice_c(db, "Cycle astral").hits == ()
        assert evict_notice_cover(db, document, expected_epoch=source.epoch)
        assert db.entitlements() == db.acquisitions() == db.placements() == []
        assert len(calls) == 1
    finally:
        service.close()

    service = LibraryService(config, Storage(path), Diagnostics())
    try:
        from cifereca.core.source_context import revoke_source_context

        assert not service._sessions
        monkeypatch.setattr(service, "_provider_enabled_for_identity", lambda p, i=None: True)
        lease = capture_search_read_scope(service, "alice")
        relation = read_series_for_search(service, row, reader_scope=lease)
        assert relation.label == "Cycle astral"
        assert relation.scope == scenario
        assert relation.source_field == "Search.CustomResult"

        # A disabled provider is not an offline authority even though the
        # durable connection is still enabled. Exercise both entry and exit
        # policy checks; no session or mocked supplier request is involved.
        monkeypatch.setattr(service, "_provider_enabled_for_identity", lambda p, i=None: False)
        assert read_series_for_search(service, row, reader_scope=lease) is None
        policy = iter((True, False))
        monkeypatch.setattr(service, "_provider_enabled_for_identity", lambda p, i=None: next(policy))
        assert read_series_for_search(service, row, reader_scope=lease) is None
        revoke_source_context(service)
        monkeypatch.setattr(service, "_provider_enabled_for_identity", lambda p, i=None: True)
        assert read_series_for_search(service, row, reader_scope=lease) is None
        lease = capture_search_read_scope(service, "alice")
        assert read_series_for_search(service, row, reader_scope=lease) == relation

        # Durable connection disable/re-enable is a separate ABA fence.
        # A fresh read lease is still not evidence of the old source epoch.
        connection = {
            "identity_id": "alice", "provider_id": row.source_provider,
            "credential_id": row.source_credential,
        }
        service.storage.provider_connection_put(dict(connection, enabled=False))
        assert read_series_for_search(service, row, reader_scope=lease) is None
        service.storage.provider_connection_put(dict(connection, enabled=True))
        assert read_series_for_search(service, row, reader_scope=capture_search_read_scope(
            service, "alice",
        )) is None
        assert search_public_notice_c(service.storage, "Cycle astral").hits == ()
        assert service.storage.entitlements() == service.storage.acquisitions() == service.storage.placements() == []
        assert len(calls) == 1
    finally:
        service.close()


@pytest.mark.parametrize("subject,expected", [
    (["Astronomie", "Sciences"], ["Astronomie", "Sciences"]),
    ([], None),
    (["https://private.example/?token=fixture"], None),
    (["file:/home/private"], None),
])
def test_exact_anonymous_subject_reaches_durable_c(tmp_path, subject, expected):
    from types import SimpleNamespace
    from contextlib import closing
    from cifereca.core.search_orchestration import (
        _public_rows_from_response, _mirror_remote_results,
    )
    from cifereca.core.notice_cover_repository import read_notice_cover, evict_notice_cover
    from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
    from cifereca.core.notice_c_public_search import search_public_notice_c
    from cifereca.storage import Storage

    payload = _response("EBOOKS", _facet(values=[
        {"Label": "Page seulement", "Count": 9, "IsSelected": False},
    ]))
    resource = payload["d"]["Results"][0]["Resource"]
    resource.update(Subj=subject, Tags=["Jeunesse"], BlogPostCategories=["Blog"])
    calls = []

    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=payload)

    session = ParisProviderSession(
        credential_id="__public__", label="Paris", credentials={},
        options={}, config={}, diagnostics=None,
    )
    session.client.close()
    session.client = _client(respond)
    path = tmp_path / "paris-subject.sqlite"
    try:
        response = session.search_with_response_facets("Synthetic book", "digital")
        raw, = response.results
        assert response.public_notices[0].result is raw
        assert raw.metadata.get("subjects") == expected
        assert not {"tags", "paris_blog_categories", "recommended_age"} & raw.metadata.keys()
        assert raw.owned is raw.downloadable is False and raw.download_url is None
        with closing(Storage(path)) as db:
            row, = _public_rows_from_response(response, session)
            assert _mirror_remote_results(SimpleNamespace(storage=db), [row],
                                          identity_id="alice", require_public_cover_witness=True) == 1
            exact = row._core_notice_cover_document_ref
            canonical = row._core_notice_canonical_cover_document_ref
            classification = read_consolidated_notice_cover(db, canonical).sources[0].public_bibliography.classification
            assert (classification.wire() if classification else None) == (
                {"subjects": expected} if expected else None)
            assert evict_notice_cover(db, exact, expected_epoch=read_notice_cover(db, exact).epoch)
        with closing(Storage(path)) as db:
            hits = search_public_notice_c(db, "astronomie", field="subject").hits
            assert len(hits) == int(expected is not None)
            if hits:
                assert hits[0].document == canonical
            for needle in ("Jeunesse", "Blog", "Page seulement"):
                assert not search_public_notice_c(db, needle, field="subject").hits
            assert db.entitlements() == db.acquisitions() == db.placements() == []
        assert len(calls) == 1
    finally:
        session.close()


@pytest.mark.parametrize("mode", ["sync", "progressive", "foreign"])
def test_declared_dilicom_branch_retains_actual_native_empty_primary_for_c(
    tmp_path, monkeypatch, mode,
):
    """Real Paris parser and ordinary Search -> original row -> exact public C.

    HTTP is controlled; this proves the installed parser's shared record
    namespace, not live supplier eligibility or the right to borrow.
    """
    from types import SimpleNamespace
    from cifereca.config import Config
    from cifereca.diagnostics import Diagnostics
    from cifereca.models import Account
    from cifereca.services import LibraryService
    from cifereca.storage import Storage
    from cifereca.core.borrow_for_identity_native_occurrence import retained_native_borrow_occurrences
    from cifereca.core.borrow_for_identity_public_branch import public_branch_occurrence_document
    from cifereca.core.search_orchestration import _public_rows_from_response, _mirror_remote_results

    calls = []

    def respond(request):
        scenario = json.loads(request.content)["query"]["ScenarioCode"]
        calls.append(scenario)
        return httpx.Response(200, json=_response(scenario, _facet()))

    cfg = Config(tmp_path / "config.json")
    cfg.set("library.directory", str(tmp_path / "library"))
    cfg.set("identities", [{"id": "alice", "label": "Alice"}, {"id": "bob", "label": "Bob"}])
    cfg.set("providers.accounts", [])
    service = LibraryService(cfg, Storage(tmp_path / "native-branch.sqlite"), Diagnostics())
    anonymous = ParisProviderSession(
        credential_id="__public__", label="Paris", credentials={},
        options={}, config={}, diagnostics=None,
    )
    native = ParisProviderSession(
        credential_id="card-alice", label="Paris",
        credentials={"username": "fixture", "password": "fixture"},
        options={}, config={}, diagnostics=None,
    )
    for session in (anonymous, native):
        session.client.close()
        session.client = _client(respond)
    account = Account(identity_id="alice", provider_id=native.provider_id,
                      credential_id=native.credential_id, unique_id="", is_primary=True)
    native._accounts = [account]
    native.connected = True
    service.accounts = [account]
    service._sessions = {service._session_key(native.provider_id, native.credential_id): native}
    monkeypatch.setattr(service, "_provider_enabled_for_identity", lambda *_: True)
    connection = service.storage.provider_connection_put({
        "identity_id": "alice", "provider_id": native.provider_id,
        "credential_id": native.credential_id, "enabled": True,
    })
    service.storage.provider_account_put({
        "connection_id": connection, "identity_id": "alice", "provider_id": native.provider_id,
        "credential_id": native.credential_id, "account_id": "", "is_primary": True,
    })
    try:
        envelope = anonymous.search_with_response_facets("Synthetic book", "digital")
        public, = _public_rows_from_response(envelope, anonymous)
        assert _mirror_remote_results(
            SimpleNamespace(storage=service.storage), [public], identity_id="alice",
            require_public_cover_witness=True,
        ) == 1
        exact = public._core_notice_cover_document_ref
        refs = frozenset((exact.source_key,))
        assert retained_native_borrow_occurrences(service, source_refs=refs) == ()
        if mode == "foreign":
            service.select_identity("bob")
        if mode == "progressive":
            batches = []
            summary = service.search_progressive(
                "Synthetic book", scope="digital", provider_id="paris", force=True,
                on_batch=lambda batch: batches.extend(batch.results),
            )
            assert not summary.cancelled
            rows = batches
        else:
            rows = service.search("Synthetic book", scope="digital", provider_id="paris", force=True)
        visible, = [r for r in rows if r.record_id == "synthetic-EBOOKS"]
        count = len(calls)
        original, = retained_native_borrow_occurrences(service, source_refs=refs)
        assert original.source_account_id is None and account.unique_id == ""
        assert public_branch_occurrence_document(service, original, owner="alice") == exact
        assert public_branch_occurrence_document(service, original, owner="bob") is None
        if mode == "foreign":
            assert original is not visible
            assert visible.metadata["catalogue_access"] == "shared_read_only"
            assert public_branch_occurrence_document(service, visible, owner="alice") is None
        else:
            assert original is visible
        assert len(calls) == count
        assert service.storage.entitlements() == service.storage.acquisitions() == service.storage.placements() == []
    finally:
        anonymous.close()
        service.close()
