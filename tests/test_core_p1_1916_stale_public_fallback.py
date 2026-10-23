"""P1 #1916: public search outages use only explicit stale catalogue facts."""
from __future__ import annotations

from dataclasses import replace
import time

import httpx
import pytest

from cifereca.core.known_catalog_fallback import (
    known_public_fallback, public_service_unavailable,
)
from cifereca.core import known_catalog_fallback as fallback_module
from tests.test_core043_progress_cache_stats import _service, _FakeSession


def _known(service, *, provider="good", identity="alice", key="one",
           title="Aventure maritime", author="Émile Auteur",
           state="remote_catalog", mirrored=None, collection="catalog"):
    return service.storage.catalog_entry_put({
        "catalog_entry_id": "known-" + identity + "-" + key,
        "title": title, "author": author,
        "identity_id": identity, "provider_id": provider,
        "item_id": "public-" + key, "local_state": state,
        "media_format": "epub", "metadata": {
            "mirrored_at": int(time.time()) if mirrored is None else mirrored,
            "medium": "digital", "source_collection": collection,
            "description": "Description publique",
            "source_credential": "PRIVATE-CREDENTIAL-NOT-REEXPOSED",
            "rights": "https://private.invalid/?auth=SIGNED_SECRET",
        },
    })


def test_foreign_catalogues_cannot_exhaust_one_sources_fallback_budget(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        monkeypatch.setattr(fallback_module, "_MAX_CATALOG_ROWS", 2)
        _known(service, key="matching")
        for index in range(4):
            _known(service, provider="other", key=f"other-{index}")
            _known(service, identity="bob", key=f"bob-{index}")
        rows = known_public_fallback(
            service.storage, provider_id="good", identity_id="alice",
            query="maritime", scope="digital", field="title", content_types=(),
        )
        assert [row.record_id for row in rows] == ["public-matching"]
        # The source's own budget remains fail-closed, not a false complete page.
        _known(service, key="second")
        _known(service, key="third")
        assert known_public_fallback(
            service.storage, provider_id="good", identity_id="alice",
            query="maritime", scope="digital", field="title", content_types=(),
        ) == ()
    finally:
        service.close()


def test_catalogue_scopes_and_limit_are_applied_before_metadata_decode(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        _known(service, key="first")
        _known(service, key="second")
        _known(service, identity="bob", key="foreign")
        _known(service, provider="other", key="other")
        assert len(service.storage.catalog_entries()) == 4
        assert len(service.storage.catalog_entries(state="remote_catalog")) == 4
        decoded = []
        original = service.storage._json_load
        def decode(raw):
            decoded.append(raw)
            return original(raw)
        monkeypatch.setattr(service.storage, "_json_load", decode)
        rows = service.storage.catalog_entries(
            identity_id="alice", provider_id="good", state="remote_catalog", limit=1,
        )
        assert len(rows) == len(decoded) == 1
        assert rows[0]["identity_id"] == "alice" and rows[0]["provider_id"] == "good"
        assert service.storage.catalog_entries(identity_id="", provider_id="good") == []
        assert service.storage.catalog_entries(provider_id="good' OR 1=1 --") == []
        for bad in (True, 0, -1, "1", 100_002):
            with pytest.raises(ValueError):
                service.storage.catalog_entries(limit=bad)
    finally:
        service.close()


def test_offline_stale_fallback_is_scoped_and_never_grants_owned_rights(tmp_path):
    service = _service(tmp_path)
    try:
        _known(service, key="matching")
        _known(service, identity="bob", key="foreign")
        _known(service, key="owned", state="remote_owned")
        _known(service, key="library", collection="library")
        _known(service, key="local", state="local")
        _known(service, key="very-old", mirrored=int(time.time()) - 100 * 86400)
        _known(service, key="wrong-provider", provider="other")
        rows = known_public_fallback(
            service.storage, provider_id="good", identity_id="alice",
            query="maritime", scope="digital", field="title",
            content_types=("epub",),
        )
        assert len(rows) == 1
        row = rows[0]
        assert row.id == "known-stale:known-alice-matching"
        assert row.record_id == "public-matching"
        assert row.metadata["search_provenance"] == "known_catalog_stale"
        assert row.owned is False and row.downloadable is False
        assert row.friendly_url is None and row.thumbnail_url is None
        assert row.source_credential == "known-public"
        assert "PRIVATE-CREDENTIAL" not in repr(row)
        assert "rights" not in row.metadata
        assert "SIGNED_SECRET" not in str(row.metadata)
        assert known_public_fallback(
            service.storage, provider_id="good", identity_id="bob",
            query="maritime", scope="digital", field="title",
            content_types=("epub",),
        )[0].record_id == "public-foreign"
    finally:
        service.close()


def test_progressive_transport_outage_keeps_good_live_and_marks_old_as_stale(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        _known(service, provider="bad", key="old", title="Progressive Book")
        bad = _FakeSession("bad")
        good = _FakeSession("good")
        requests = []

        def unavailable(query, scope, **kwargs):
            requests.append((query, scope))
            raise httpx.ConnectError("sensitive-secret-URL")

        bad.search = unavailable
        service._sessions = {"good": good, "bad": bad}
        live_mirrors = []
        monkeypatch.setattr(
            service, "_mirror_remote_results",
            lambda rows, **kwargs: live_mirrors.extend(rows) or len(rows),
        )
        batches = []
        summary = service.search_progressive(
            "Progressive Book", force=True, on_batch=batches.append,
        )
        assert len(requests) == 1
        assert summary.result_count == 2
        assert summary.providers_failed == 1
        assert len(batches) == 2
        stale_batch = next(b for b in batches if b.provider_id == "bad")
        assert stale_batch.error_code == "SearchTemporarilyUnavailable"
        assert stale_batch.results[0].metadata["search_provenance"] == "known_catalog_stale"
        assert "résultats connus" in stale_batch.warnings[0]
        assert "sensitive-secret" not in str(summary.warnings)
        assert {r.source_provider for r in live_mirrors} == {"good"}
        assert all(r.metadata.get("search_provenance") != "known_catalog_stale" for r in live_mirrors)
    finally:
        service.close()


@pytest.mark.parametrize("failure", ["forbidden", "timeout", "decoding", "http_408", "rate_limited", "server_error", "legitimate_zero"])
def test_fallback_only_on_public_outage_not_rights_or_real_empty(
    tmp_path, failure,
):
    service = _service(tmp_path)
    try:
        _known(service, provider="bad", title="Maritime Book")
        session = _FakeSession("bad")
        attempts = []

        def search(query, scope, **kwargs):
            attempts.append(query)
            if failure == "legitimate_zero":
                return []
            if failure == "timeout":
                raise httpx.ReadTimeout("remote host private")
            if failure == "decoding":
                raise httpx.DecodingError("PRIVATE_TOKEN invalid Content-Encoding")
            if failure in {"forbidden", "http_408", "rate_limited", "server_error"}:
                status = {"forbidden": 403, "http_408": 408, "rate_limited": 429, "server_error": 503}[failure]
                request = httpx.Request("GET", "https://catalog.example.invalid/search")
                raise httpx.HTTPStatusError("not available", request=request,
                                            response=httpx.Response(status, request=request))
            raise AssertionError("unexpected")

        session.search = search
        service._sessions = {"bad": session}
        batches = []
        summary = service.search_progressive(
            "Maritime Book", force=True, on_batch=batches.append,
        )
        assert len(batches) == 1
        if failure in {"timeout", "decoding", "http_408", "rate_limited", "server_error"}:
            assert len(attempts) == 1
            assert batches[0].results[0].metadata["search_provenance"] == "known_catalog_stale"
            assert summary.providers_failed == 1
        else:
            assert not batches[0].results
            assert summary.result_count == 0
    finally:
        service.close()


def test_fallback_without_known_rows_is_error_not_zero_results(tmp_path):
    service = _service(tmp_path)
    try:
        bad = _FakeSession("bad")
        bad.search = lambda *a, **kw: (_ for _ in ()).throw(httpx.ConnectTimeout("offline"))
        service._sessions = {"bad": bad}
        batches = []
        summary = service.search_progressive("unseen", force=True, on_batch=batches.append)
        assert summary.result_count == 0
        assert summary.providers_failed == 1
        assert "aucun résultat connu" in batches[0].warnings[0]
        assert batches[0].error_code == "SearchTemporarilyUnavailable"
    finally:
        service.close()


def test_no_source_scope_or_future_timestamp_can_surface_foreign_book(tmp_path):
    service = _service(tmp_path)
    try:
        _known(service, key="future", mirrored=int(time.time()) + 3600)
        assert known_public_fallback(
            service.storage, provider_id="good", identity_id=None,
            query="maritime", scope="all", field="all", content_types=(),
        ) == ()
        assert known_public_fallback(
            service.storage, provider_id="good", identity_id="alice",
            query="maritime", scope="all", field="all", content_types=(),
        ) == ()
    finally:
        service.close()


@pytest.mark.parametrize("status,expected_code", [
    (401, "SearchAuthenticationRequired"),
    (403, "SearchAccessDenied"),
    (408, "SearchTemporarilyUnavailable"),
    (429, "SearchTemporarilyUnavailable"),
    (503, "SearchTemporarilyUnavailable"),
])
def test_http_failure_codes_never_expose_private_request_uri(
    tmp_path, status, expected_code,
):
    service = _service(tmp_path)
    try:
        _known(service, provider="bad", title="Maritime Book")
        session = _FakeSession("bad")
        private_url = "https://catalog.example.invalid/private?auth=SIGNED_SECRET"
        calls = []

        def failure(*args, **kwargs):
            calls.append("network")
            req = httpx.Request("GET", private_url)
            raise httpx.HTTPStatusError(private_url, request=req,
                                        response=httpx.Response(status, request=req))

        session.search = failure
        service._sessions = {"bad": session}
        batches = []
        summary = service.search_progressive(
            "Maritime Book", force=True, on_batch=batches.append,
        )
        assert len(calls) == 1
        assert batches[0].error_code == expected_code
        assert "SIGNED_SECRET" not in str(batches)
        assert "SIGNED_SECRET" not in str(summary)
        assert bool(batches[0].results) is (status in {408, 429, 503})
        assert summary.providers_failed == 1
    finally:
        service.close()


def test_partial_public_provider_warning_is_redacted_without_losing_live_rows(tmp_path):
    service = _service(tmp_path)
    try:
        provider = _FakeSession("public")
        provider.last_search_warnings = [
            "HTTP https://private.invalid/fulfillment?auth=SIGNED-PRIVATE-SECRET",
        ]
        service._sessions = {"public": provider}
        batches = []
        summary = service.search_progressive(
            "Progressive Book", force=True, on_batch=batches.append,
        )
        assert summary.result_count == 1
        assert len(batches[0].results) == 1
        assert batches[0].warnings == ("Public: résultats partiels",)
        assert "SIGNED-PRIVATE-SECRET" not in str(summary)
        assert "SIGNED-PRIVATE-SECRET" not in str(batches)
        assert summary.providers_failed == 0
    finally:
        service.close()


def test_synchronous_search_reuses_only_attested_public_mirror_on_outage(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        _known(service, provider="bad", key="old", title="Progressive Book")
        _known(service, provider="bad", key="private", title="Progressive Book",
               collection="library")
        _known(service, provider="bad", identity="bob", key="foreign",
               title="Progressive Book")
        bad, good = _FakeSession("bad"), _FakeSession("good")
        calls = []
        def denied(*_args, **_kwargs):
            calls.append("network")
            raise httpx.ConnectError("https://secret.invalid/?auth=TOKEN_PRIVATE")
        bad.search = denied
        service._sessions = {"bad": bad, "good": good}
        mirrored = []
        monkeypatch.setattr(
            service, "_mirror_remote_results",
            lambda rows, **kw: mirrored.extend(rows) or len(rows),
        )
        rows = service.search("Progressive Book", force=True, field="title")
        assert len(calls) == 1
        assert len(rows) == 2
        stale = next(row for row in rows if row.source_provider == "bad")
        assert stale.metadata["search_provenance"] == "known_catalog_stale"
        assert stale.owned is False and stale.downloadable is False
        assert stale.friendly_url is stale.thumbnail_url is None
        assert {row.source_provider for row in mirrored} == {"good"}
        assert "résultats connus" in str(service.last_search_warnings)
        assert "TOKEN_PRIVATE" not in str(service.last_search_warnings)
    finally:
        service.close()


@pytest.mark.parametrize("scenario,expected", [
    ("forbidden", False), ("rate_limited", True),
    ("real_zero", False), ("no_public", False),
])
def test_synchronous_fallback_rejects_rights_refusals_and_empty_public_cache(
    tmp_path, scenario,
    expected,
):
    service = _service(tmp_path)
    try:
        _known(service, provider="bad", title="Progressive Book",
               collection="library" if scenario == "no_public" else "catalog")
        session = _FakeSession("bad")
        calls = []
        def search(*_args, **_kwargs):
            calls.append("network")
            if scenario == "real_zero":
                return []
            if scenario == "no_public":
                raise httpx.ReadTimeout("SECRET")
            status = 403 if scenario == "forbidden" else 429
            request = httpx.Request("GET", "https://catalog.invalid/?token=SECRET")
            raise httpx.HTTPStatusError(
                "SECRET", request=request,
                response=httpx.Response(status, request=request),
            )
        session.search = search
        service._sessions = {"bad": session}
        rows = service.search("Progressive Book", force=True, field="title")
        assert len(calls) == 1 if scenario != "real_zero" else len(calls) >= 1
        assert bool(rows) == expected
        assert all(row.metadata.get("search_provenance") == "known_catalog_stale"
                   for row in rows)
        assert "SECRET" not in str(service.last_search_warnings)
        if scenario == "no_public":
            assert "aucun résultat connu" in str(service.last_search_warnings)
    finally:
        service.close()


def test_decoding_failure_keeps_healthy_source_and_private_messages_out(tmp_path):
    """One network attempt per failed provider; no retry of looser variants."""
    service = _service(tmp_path)
    try:
        _known(service, provider="bad", title="Maritime Book")
        bad, good = _FakeSession("bad"), _FakeSession("good")
        calls = []
        def invalid_encoding(*args, **kwargs):
            calls.append("bad-read")
            raise httpx.DecodingError(
                "https://private.invalid/?token=SECRET_DECODING"
            )
        bad.search = invalid_encoding
        service._sessions = {"bad": bad, "good": good}
        batches = []
        result = service.search_progressive(
            "Maritime Book", force=True, on_batch=batches.append,
        )
        assert len(calls) == 1
        assert result.providers_failed == 1
        assert any(batch.provider_id == "good" and batch.results
                   for batch in batches)
        stale = next(batch for batch in batches if batch.provider_id == "bad")
        assert stale.error_code == "SearchTemporarilyUnavailable"
        assert stale.results and stale.results[0].metadata[
            "search_provenance"] == "known_catalog_stale"
        assert "SECRET_DECODING" not in repr((result, batches))
    finally:
        service.close()
