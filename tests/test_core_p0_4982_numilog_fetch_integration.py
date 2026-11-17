"""#4982 recorded Numilog Search -> exact fresh transport -> journal -> C.

HTTP is replaced by MockTransport, not the provider parser, admission,
budget, session guards or SQLite writers. No live request is made.
"""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Event, RLock
from time import monotonic
from types import SimpleNamespace

import httpx
import pytest

from cifereca.core import notice_c_job_journal as journal, search_orchestration
from cifereca.core.notice_c_cover_index import (
    persist_exact_public_search_cover, read_consolidated_notice_cover,
)
from cifereca.core.notice_c_fetch_authority import PublicFetchAuthority
from cifereca.core.notice_c_fetch_execution import PublicFetchExecution
from cifereca.core.notice_c_public_search_fetch import make_public_search_fetch_port
from cifereca.core.notice_c_public_search_replay import public_replay_same_call_observation
from cifereca.core.notice_c_raw_input_receipt import record_public_raw_input_receipt
from cifereca.core.notice_c_work_pump import PublicNoticeWorkPump
from cifereca.core.notice_cover_repository import persist_notice_cover, read_notice_cover
from cifereca.core.notice_search_cover_producer import admit_public_search_catalogue
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.storage import Storage
from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_runtime import TaskBrokerImpl

PLUGIN = Path(__file__).resolve().parents[1] / "src/plugins/provider-numilog"
HTML = PLUGIN / "tests/fixtures/observed_search-loup-0.html"
ORIGINAL = "Les Enfants du Pas du Loup"
UPDATED = "Les Enfants du Pas du Loup (édition corrigée)"


@pytest.mark.parametrize("mode", ("same", "changed", "revoked_start", "rebound_commit", "expired", "foreign"))
def test_explicit_exact_numilog_fetch_via_one_core_worker(tmp_path, monkeypatch, mode):
    monkeypatch.syspath_prepend(str(PLUGIN))
    import cifereca_numilog_public_client as client
    import cifereca_numilog_public_provider as provider
    from cifereca_numilog_public_cache import PublicCatalogueCache

    now, cache_clock = [100], [100.]
    monkeypatch.setattr(provider, "time", SimpleNamespace(
        time=lambda: now[0], monotonic=lambda: cache_clock[0],
    ))
    body = HTML.read_bytes()
    fresh = body
    if mode in ("changed", "rebound_commit"):
        fresh = body.replace(ORIGINAL.encode(), UPDATED.encode())
        assert fresh != body
    elif mode == "foreign":
        fresh = body.replace(b"1947941/", b"1947942/")
        assert fresh != body
    requests = []
    permitted = [True]
    lock = RLock()

    @contextmanager
    def hold_policy():
        with lock:
            yield permitted[0]

    def serve(request):
        requests.append((request.method, str(request.url)))
        assert request.method == "GET" and request.url.host == "www.numilog.com"
        assert "cookie" not in request.headers and "authorization" not in request.headers
        return httpx.Response(
            200, content=body if len(requests) == 1 else fresh,
            headers={"content-type": "text/html; charset=utf-8"}, request=request,
        )

    def construct(**kwargs):
        if requests and mode == "revoked_start":
            # Revoke AFTER Core and supplier reservation but BEFORE actual GET.
            with lock:
                permitted[0] = False
        return httpx.Client(transport=httpx.MockTransport(serve), trust_env=False,
                            headers=kwargs.get("headers"))

    monkeypatch.setattr(client.HTTPX_TRANSPORT, "client", construct)
    session = provider.NumilogPublicSession(
        credential_id="__public__", label="Public", credentials={}, options={},
        _retain_public_search_inputs=True,
    )
    session._public_cache = PublicCatalogueCache(clock=lambda: cache_clock[0])
    session.connect()
    db = Storage(tmp_path / "fetch.sqlite")
    tasks = TaskBrokerImpl(object(), max_workers=1, shared_admission=
                           SharedTaskAdmission(max_inflight=3, reserve_user_slots=1))
    authority = PublicFetchAuthority(db, clock=lambda: now[0])
    try:
        response, token = session._search_response_with_retained_input("loup", "catalog")
        assert len(requests) == 1 and token is not None
        row, = search_orchestration._public_rows_from_response(response, session)
        assert row.title == ORIGINAL
        observation = admit_public_search_catalogue(
            row, identity_id="alice", observed_at=now[0],
            public_cover_url=row.thumbnail_url,
        )
        source = persist_notice_cover(
            db, observation, field_path=("search_result", "thumbnail_url"),
            cover_inventory_complete=True,
        )
        canonical = persist_exact_public_search_cover(db, source)
        assert public_replay_same_call_observation(response, session, source) == observation
        raw, ttl = session._read_retained_public_input(token)
        with session._hold_retained_public_input(token) as held:
            assert held == raw and held._body == body
            receipt = record_public_raw_input_receipt(
                db, source, held._body, transport_digest=held.transport_digest,
                parser_family_digest=held.parser_family_digest,
                parser_implementation_digest=held.parser_implementation_digest,
                producer_token=held.token, now=now[0], ttl_seconds=ttl,
            )
        reserve = session._reserve_exact_public_refresh
        def reserve_exact(*args, **kwargs):
            reserved = reserve(*args, **kwargs)
            if mode != "rebound_commit":
                return reserved
            def run(control):
                value = reserved.execute(control)
                # The result was valid when returned by the real transport,
                # but the old session is no longer valid at publication.
                session.close()
                session.connect()
                return value
            return replace(reserved, execute=run)

        port = make_public_search_fetch_port(
            authority, session, source, receipt,
            reserve_exact=reserve_exact, clock=lambda: now[0],
        )
        # Explicit trusted action; production does not install this composition.
        ref = authority.request(
            source, transport_digest=port.transport_digest,
            implementation_digest=port.implementation_digest,
            hold_authority=hold_policy, ttl_seconds=30, expected_generation=0,
        )
        assert PublicNoticeWorkPump(db, tasks, clock=lambda: now[0]).tick().handles == ()
        events = []
        pump = PublicNoticeWorkPump(
            db, tasks, clock=lambda: now[0],
            fetch_execution=PublicFetchExecution(authority, (port,)),
            on_committed=lambda doc, epoch: events.append((doc, epoch)),
        )
        now[0] += 1
        if mode == "expired":
            cache_clock[0] += 61
        handle, = pump.tick().handles
        success = mode in ("same", "changed")
        assert handle.result(timeout=10) == ("current" if success else "error")
        assert journal.read_job(db, ref).state == ("current" if success else "error")
        assert len(requests) == (1 if mode in ("revoked_start", "expired") else 2)
        if len(requests) == 2:
            assert requests[0] == requests[1]
        if mode == "changed":
            latest_source = read_notice_cover(db, source.document)
            assert latest_source.epoch == source.epoch + 1
            assert latest_source.observation.observed_at == now[0]
            until = monotonic() + 10
            while monotonic() < until:
                for pending in pump.tick().handles:
                    assert pending.result(timeout=5) == "current"
                latest = read_consolidated_notice_cover(db, canonical.document)
                if latest.epoch > canonical.epoch:
                    break
                Event().wait(.01)
            assert latest.document == canonical.document
            assert latest.epoch == canonical.epoch + 1
            assert latest.sources[0].public_bibliography.title == UPDATED
            assert events == [(latest.document, latest.epoch)]
        else:
            assert read_notice_cover(db, source.document) == source
            assert read_consolidated_notice_cover(db, canonical.document) == canonical
            assert events == []
            hit, = search_public_notice_c(db, ORIGINAL, now=now[0]).hits
            assert hit.last_seen_at == (now[0] if success else observation.observed_at)
        count = len(requests)
        now[0] += 20
        assert pump.tick().handles == ()
        assert len(requests) == count
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        authority.close()
        tasks.close()
        session.close()
        db.close()
