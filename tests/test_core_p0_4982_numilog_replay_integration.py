"""#4982 real recorded Numilog HTTP bytes -> parser -> C -> reparse -> C.

Only HTTP transport and (in the upgrade case) the changed parser output
are synthetic. No manually assigned Core witness; real Search admission,
cache lease, journal, TaskBrokerImpl and source/canonical writers execute.
No live provider request or actual production activation is claimed.
"""
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import Event
from time import monotonic

import httpx
import pytest

from cifereca.core import notice_c_job_journal as journal, search_orchestration
from cifereca.core.notice_c_cover_index import (
    persist_exact_public_search_cover, read_consolidated_notice_cover,
)
from cifereca.core.notice_c_public_search_replay import (
    make_retained_public_replay_port, public_replay_same_call_observation,
)
from cifereca.core.notice_c_raw_input_receipt import record_public_raw_input_receipt
from cifereca.core.notice_c_reparse_execution import PublicReplayRegistry, ReplayInput
from cifereca.core.notice_c_reparse_requests import request_public_reparse
from cifereca.core.notice_c_work_bridge import RetryableNoticeWork, UnavailableNoticeWork
from cifereca.core.notice_c_work_pump import PublicNoticeWorkPump
from cifereca.core.notice_cover_repository import persist_notice_cover, read_notice_cover
from cifereca.core.notice_search_cover_producer import admit_public_search_catalogue
from cifereca.plugin_api import ProviderError, ProviderSearchResponse
from cifereca.storage import Storage
from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_runtime import TaskBrokerImpl

PLUGIN = Path(__file__).resolve().parents[1] / "src/plugins/provider-numilog"
HTML = PLUGIN / "tests/fixtures/observed_search-loup-0.html"
CORRECTED = "Titre corrigé par le parseur de recette"


def make_session(monkeypatch):
    monkeypatch.syspath_prepend(str(PLUGIN))
    import cifereca_numilog_public_client as client
    import cifereca_numilog_public_provider as provider
    from cifereca_numilog_public_cache import PublicCatalogueCache

    calls = []
    body = HTML.read_bytes()

    def serve(request):
        calls.append((request.method, str(request.url)))
        assert request.method == "GET"
        assert request.url.host == "www.numilog.com"
        return httpx.Response(
            200, content=body, headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    monkeypatch.setattr(
        client.HTTPX_TRANSPORT, "client",
        lambda **_: httpx.Client(transport=httpx.MockTransport(serve), trust_env=False),
    )
    clock = [100.]
    session = provider.NumilogPublicSession(
        credential_id="__public__", label="Public", credentials={}, options={},
        _retain_public_search_inputs=True,
    )
    session._public_cache = PublicCatalogueCache(clock=lambda: clock[0])
    session.connect()
    return session, client, clock, calls, body


def normalize(raw):
    if raw is None:
        return None
    return ReplayInput(
        raw.token, raw.transport_digest, raw.parser_family_digest,
        raw.parser_implementation_digest, raw._body,
    )


def make_port(session, family, implementation):
    def read(token):
        return session._read_retained_public_input(token)

    @contextmanager
    def hold(token):
        try:
            with session._hold_retained_public_input(token) as raw:
                yield raw
        except ProviderError as exc:
            if str(exc) == "Numilog : public_catalog_replay_busy":
                raise RetryableNoticeWork("notice_reparse_provider_busy") from None
            raise UnavailableNoticeWork("notice_reparse_input_unavailable") from None

    # Provider-specific wiring lives here in the trusted test composition,
    # not in Core, a public plugin ABI, or the production registry.
    return make_retained_public_replay_port(
        session, provider_id=session.provider_id,
        parser_family_digest=family, implementation_digest=implementation,
        read_retained=read, reparse_response=session._reparse_retained_public_response,
        hold_retained=hold, normalize_input=normalize,
    )


@pytest.mark.parametrize("mode", ("unchanged", "upgrade", "expired",
                                  "coverless-unchanged", "coverless-upgrade",
                                  "coverless-recover", "reject-downgrade"))
def test_recorded_transport_to_real_reparse_and_c_without_new_get(tmp_path, monkeypatch, mode):
    session, client, cache_clock, calls, body = make_session(monkeypatch)
    db = Storage(tmp_path / "reparse.sqlite")
    tasks = TaskBrokerImpl(object(), max_workers=1, shared_admission=
                           SharedTaskAdmission(max_inflight=3, reserve_user_slots=1))
    try:
        coverless = mode.startswith("coverless-")
        upgrade = mode in ("upgrade", "coverless-upgrade", "coverless-recover")
        real_parse = client.parse_public_search

        def original_parse(text, url):
            page = real_parse(text, url)
            if coverless:
                return replace(page, items=tuple(
                    replace(row, thumbnail_url=None) for row in page.items))
            return page

        monkeypatch.setattr(client, "parse_public_search", original_parse)
        response, token = session._search_response_with_retained_input("loup", "catalog")
        assert token is not None and len(calls) == 1
        rows = search_orchestration._public_rows_from_response(response, session)
        row, = rows
        observation = admit_public_search_catalogue(
            row, identity_id="alice", observed_at=100,
            public_cover_url=row.thumbnail_url,
        )
        assert observation is not None and observation.parser_version == "2"
        original = persist_notice_cover(
            db, observation, field_path=("search_result", "thumbnail_url"),
            cover_inventory_complete=True,
        )
        assert observation.status == ("partial" if coverless else "complete")
        assert original.attribute.state == ("pending" if coverless else "available")
        canonical = persist_exact_public_search_cover(db, original)
        assert public_replay_same_call_observation(response, session, original) == observation
        with pytest.raises(UnavailableNoticeWork, match="original_response_changed"):
            public_replay_same_call_observation(
                ProviderSearchResponse(results=response.results), session, original,
            )

        raw, ttl = session._read_retained_public_input(token)
        assert raw._body == body and ttl > 0
        assert raw.input_digest == sha256(body).hexdigest()
        with session._hold_retained_public_input(token) as held:
            assert held == raw
            receipt = record_public_raw_input_receipt(
                db, original, held._body, transport_digest=held.transport_digest,
                parser_family_digest=held.parser_family_digest,
                parser_implementation_digest=held.parser_implementation_digest,
                now=100, ttl_seconds=ttl, producer_token=held.token,
            )
            implementation = held.parser_implementation_digest
            if upgrade or mode == "reject-downgrade":
                # Emulate a new parser implementation using the SAME recorded
                # input. First execute the actual parser, then model the one
                # intended field correction; no fabricated source witness.
                implementation = sha256(b"numilog.public.search.html.test-v2").hexdigest()
            ref = request_public_reparse(
                db, original, receipt, held._body, now=100,
                expected_generation=0, implementation_digest=implementation,
            )

        parses = []
        def counted(text, url):
            parses.append(sha256(text.encode("utf-8")).hexdigest())
            page = real_parse(text, url) if mode == "coverless-recover" else original_parse(text, url)
            if mode == "reject-downgrade":
                return replace(page, items=(replace(page.items[0], thumbnail_url=None),))
            if upgrade:
                return replace(page, items=(replace(page.items[0], title=CORRECTED),))
            return page

        monkeypatch.setattr(client, "parse_public_search", counted)
        port = make_port(session, raw.parser_family_digest, implementation)
        events = []
        pump = PublicNoticeWorkPump(
            db, tasks, clock=lambda: 100,
            replay_registry=PublicReplayRegistry((port,)),
            on_committed=lambda doc, epoch: events.append((doc, epoch)),
        )
        if mode == "expired":
            cache_clock[0] += 61.
        handle, = pump.tick().handles
        expected = "error" if mode in ("expired", "reject-downgrade") else "current"
        assert handle.result(timeout=10) == expected
        assert journal.read_job(db, ref).state == expected

        if upgrade:
            deadline = monotonic() + 10
            while monotonic() < deadline:
                step = pump.tick()
                for pending in step.handles:
                    assert pending.result(timeout=5) == "current"
                latest = read_consolidated_notice_cover(db, canonical.document)
                if latest.epoch > canonical.epoch:
                    break
                Event().wait(.01)
            assert latest.epoch == canonical.epoch + 1
            assert latest.sources[0].public_bibliography.title == CORRECTED
            assert latest.document == canonical.document
            corrected = read_notice_cover(db, original.document)
            assert corrected.epoch == original.epoch + 1
            assert corrected.observation.observed_at == original.observation.observed_at
            assert corrected.attribute.state == ("pending" if mode == "coverless-upgrade" else "available")
            assert events == [(canonical.document, latest.epoch)]
        else:
            assert read_notice_cover(db, original.document) == original
            assert read_consolidated_notice_cover(db, canonical.document) == canonical
            assert events == []
        assert len(parses) == (0 if mode == "expired" else 1)
        assert len(calls) == 1
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        tasks.close()
        session.close()
        db.close()


@pytest.mark.parametrize("version_change", (False, True))
def test_normal_search_records_exact_retained_receipt(tmp_path, monkeypatch, version_change):
    from types import SimpleNamespace
    from cifereca.core.notice_c_raw_input_receipt import read_public_raw_input_receipt
    from cifereca.core import notice_c_search_receipt
    session, client, cache_clock, calls, body = make_session(monkeypatch)
    db = Storage(tmp_path / "normal-receipt.sqlite")
    try:
        monkeypatch.setattr(notice_c_search_receipt, "time", lambda: 100)
        response = session.search_with_response_facets("loup", "catalog")
        assert response.notice_parse_input_token is not None
        rows = search_orchestration._public_rows_from_response(response, session)
        row, = rows
        search_orchestration._ingest_public_search_c(
            SimpleNamespace(storage=db), rows, identity_id="alice", now=100,
            require_public_cover_witness=True,
        )
        receipt = read_public_raw_input_receipt(
            db, row._core_notice_cover_document_ref, now=100,
        )
        assert receipt is not None
        assert receipt.producer_token == response.notice_parse_input_token
        assert receipt.input_digest == sha256(body).hexdigest()
        assert read_public_raw_input_receipt(
            db, row._core_notice_cover_document_ref, now=161,
        ) is None
        assert "_core_notice_retained_input" not in row.__dict__
        assert len(calls) == 1
        source = read_notice_cover(db, row._core_notice_cover_document_ref)
        raw, ttl = session._read_retained_public_input(receipt.producer_token)
        assert raw is not None and ttl > 0
        from cifereca.core.notice_c_work_runtime import NoticeWorkRuntime

        tasks = TaskBrokerImpl(object(), max_workers=3, shared_admission=
                               SharedTaskAdmission(max_inflight=3, reserve_user_slots=1))
        runtime = NoticeWorkRuntime(db, tasks, clock=lambda: 100)
        try:
            from cifereca.core.notice_c_public_search_replay import qualify_public_search_replay
            port = qualify_public_search_replay(session, session.public_search_replay_capability())
            registry = PublicReplayRegistry((port,))
            assert runtime.register_search_replay(source, receipt, port) is None
            if version_change:
                # Model a qualified new parser against the still-live old
                # input, without claiming retention through process restart.
                monkeypatch.setattr(client, "_PARSER_IMPLEMENTATION_DIGEST",
                                    sha256(b"numilog.public.search.test-upgrade").hexdigest())
                assert port.read_input(receipt.producer_token) is None
                with port.hold_input(receipt.producer_token) as old_live:
                    assert old_live is None
                upgraded = qualify_public_search_replay(
                    session, session.public_search_replay_capability(),
                )
                ref = runtime.register_search_replay(source, receipt, upgraded)
                assert ref is not None
                assert runtime.register_search_replay(source, receipt, upgraded) == ref
            else:
                ref = runtime.request_public_replay(
                    source, receipt,
                    implementation_digest=receipt.parser_implementation_digest,
                    expected_generation=0,
                )
            deadline = monotonic() + 10
            while monotonic() < deadline:
                job = journal.read_job(db, ref)
                if job.state in ("current", "error"):
                    break
                Event().wait(.01)
            assert journal.read_job(db, ref).state == "current"
            assert len(calls) == 1
        finally:
            runtime.close()
            tasks.close()
        assert runtime.install_replay_registry(registry) is False
    finally:
        session.close()
        db.close()


def test_retained_replay_adapter_refuses_revoked_session(tmp_path, monkeypatch):
    session, _client, _clock, calls, _body = make_session(monkeypatch)
    try:
        response = session.search_with_response_facets("loup", "catalog")
        token = response.notice_parse_input_token
        raw, ttl = session._read_retained_public_input(token)
        assert raw is not None and ttl > 0
        port = make_port(session, raw.parser_family_digest,
                         raw.parser_implementation_digest)
        assert port.read_input(token) == normalize(raw)
        with port.hold_input(token) as live:
            assert live == normalize(raw)
        session.close()
        assert port.read_input(token) is None
        with port.hold_input(token) as live:
            assert live is None
        assert len(calls) == 1
    finally:
        session.close()


def test_declared_replay_capability_does_not_survive_reconnect(monkeypatch):
    from cifereca.core.notice_c_public_search_replay import qualify_public_search_replay
    session, _client, _clock, calls, _body = make_session(monkeypatch)
    try:
        capability = session.public_search_replay_capability()
        port = qualify_public_search_replay(session, capability)
        token = session.search_with_response_facets("loup", "catalog").notice_parse_input_token
        assert port.read_input(token) is not None
        session.close()
        assert session.public_search_replay_capability() is None
        session.connect()
        new_token = session.search_with_response_facets("loup", "catalog").notice_parse_input_token
        assert new_token != token
        assert port.read_input(new_token) is None
        with port.hold_input(new_token) as live:
            assert live is None
        fresh = qualify_public_search_replay(session, session.public_search_replay_capability())
        assert fresh.read_input(new_token) is not None
        assert len(calls) == 2
    finally:
        session.close()


def test_declared_replay_lease_busy_is_nonblocking(monkeypatch):
    from cifereca.core.notice_c_public_search_replay import qualify_public_search_replay
    session, _client, _clock, calls, _body = make_session(monkeypatch)
    try:
        port = qualify_public_search_replay(session, session.public_search_replay_capability())
        token = session.search_with_response_facets("loup", "catalog").notice_parse_input_token
        assert port.read_input(token) is not None
        # A non-reentrant navigation guard on this thread reproduces busy
        # deterministically; the adapter must not attempt a blocking acquire.
        with session._navigation_lock:
            with pytest.raises(RetryableNoticeWork, match="provider_busy"):
                with port.hold_input(token):
                    pytest.fail("busy lease admitted")
        with port.hold_input(token) as live:
            assert live is not None
        assert len(calls) == 1
    finally:
        session.close()
