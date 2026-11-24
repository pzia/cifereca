"""#4982 explicit one-shot fetch through real broker and SQLite writers.

The transport is a synthetic registered adapter, not a qualified supplier.
The default pump remains unable to execute fetch jobs.
"""
from contextlib import nullcontext
from threading import Event
from time import monotonic

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_cover_index import (
    persist_exact_public_search_cover, read_consolidated_notice_cover,
)
from cifereca.core.notice_c_fetch_authority import PublicFetchAuthority
from cifereca.core.notice_c_fetch_execution import PublicFetchExecution, PublicFetchPort, ReservedPublicFetch
from cifereca.core.notice_c_work_pump import PublicNoticeWorkPump
from cifereca.core.notice_cover_repository import read_notice_cover
from cifereca.core.notice_search_cover_producer import admit_public_search_catalogue
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.storage import Storage
from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_runtime import TaskBrokerImpl
from tests.test_core_p0_4982_fetch_authority import Authority, TRANSPORT, IMPLEMENTATION, request
from tests.test_core_p0_4982_raw_input_receipt import public
from tests.test_core_p0_6366_public_c_sqlite_source_search import verified_public


class Transport:
    def __init__(self, source, owner, mode):
        self.source = source
        self.owner = owner
        self.mode = mode
        self.authority = None
        self.calls = []

    def reserve(self, permit):
        assert self.owner.allowed
        assert permit.source == self.source
        self.calls.append("reserve")
        if self.mode == "refused":
            return None

        def execute(control):
            control.check_cancelled()
            with self.authority.transport_start_guard(permit):
                self.calls.append("execute")
            if self.mode == "offline":
                raise OSError("synthetic network failure")
            if self.mode == "revoked":
                self.owner.allowed = False
            changes = dict(record_id="same-edition",
                           title="Un livre" if self.mode == "same" else "Titre corrigé")
            if self.mode == "foreign":
                changes["record_id"] = "different-edition"
            if self.mode == "partial":
                changes["thumbnail_url"] = None
            row = verified_public(**changes)
            observation = admit_public_search_catalogue(
                row, identity_id="alice", observed_at=99 if self.mode == "old" else 100,
                public_cover_url=row.thumbnail_url,
            )
            if self.mode in ("witness_denied", "witness_allowed", "witness_changed_input"):
                from cifereca.core.notice_c_fetch_publication import FreshPublicParse
                body = b"changed" if self.mode == "witness_changed_input" else b"approved response"
                return FreshPublicParse(observation, body, self.mode == "witness_allowed")
            return observation

        return ReservedPublicFetch(permit, execute, lambda: self.calls.append("release"), nullcontext)


def setup(db, mode):
    source = public(db)
    canonical = persist_exact_public_search_cover(db, source)
    owner = Authority()
    authority = PublicFetchAuthority(db, clock=lambda: 100)
    ref = request(authority, source, owner)
    transport = Transport(source, owner, mode)
    transport.authority = authority
    execution = PublicFetchExecution(authority, (PublicFetchPort(
        source.observation.key.provider_id, TRANSPORT, IMPLEMENTATION, transport.reserve,
    ),))
    return source, canonical, authority, ref, transport, execution


def broker():
    return TaskBrokerImpl(object(), max_workers=1, shared_admission=
                           SharedTaskAdmission(max_inflight=3, reserve_user_slots=1))


@pytest.mark.parametrize("mode", ("same", "changed"))
def test_one_fetch_and_atomic_source_publication_then_c(tmp_path, mode):
    db = Storage(tmp_path / "fetch.sqlite")
    tasks = broker()
    try:
        source, canonical, authority, ref, transport, execution = setup(db, mode)
        assert PublicNoticeWorkPump(db, tasks, clock=lambda: 100).tick().handles == ()
        assert journal.read_job(db, ref).attempts == 0
        events = []
        pump = PublicNoticeWorkPump(
            db, tasks, clock=lambda: 100, fetch_execution=execution,
            on_committed=lambda doc, epoch: events.append((doc, epoch)),
        )
        handle, = pump.tick().handles
        assert handle.result(timeout=10) == "current"
        assert journal.read_job(db, ref).state == "current"
        assert transport.calls == ["reserve", "execute", "release"]
        if mode == "same":
            assert read_notice_cover(db, source.document) == source
            hit, = search_public_notice_c(db, "Un livre", now=100).hits
            assert hit.last_seen_at == 100
            assert read_consolidated_notice_cover(db, canonical.document) == canonical
            assert pump.tick().handles == () and events == []
        else:
            updated = read_notice_cover(db, source.document)
            assert updated.epoch == source.epoch + 1
            assert updated.observation.observed_at == 100
            deadline = monotonic() + 10
            while monotonic() < deadline:
                tick = pump.tick()
                for pending in tick.handles:
                    assert pending.result(timeout=5) == "current"
                latest = read_consolidated_notice_cover(db, canonical.document)
                if latest.epoch > canonical.epoch:
                    break
                Event().wait(.01)
            assert latest.epoch == canonical.epoch + 1
            assert latest.sources[0].public_bibliography.title == "Titre corrigé"
            assert events == [(canonical.document, latest.epoch)]
        assert transport.calls == ["reserve", "execute", "release"]
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        tasks.close()
        db.close()


@pytest.mark.parametrize("mode", ("refused", "offline", "revoked", "foreign", "partial", "old"))
def test_fetch_failure_never_refetches_or_overwrites_last_good_c(tmp_path, mode):
    db = Storage(tmp_path / "refused.sqlite")
    tasks = broker()
    clock = [100]
    try:
        source, canonical, authority, ref, transport, execution = setup(db, mode)
        pump = PublicNoticeWorkPump(db, tasks, clock=lambda: clock[0], fetch_execution=execution)
        handle, = pump.tick().handles
        assert handle.result(timeout=10) == "error"
        job = journal.read_job(db, ref)
        assert (job.state, job.attempts, job.reason) == ("error", 1, "permanent")
        expected = ["reserve"] if mode == "refused" else ["reserve", "execute", "release"]
        assert transport.calls == expected
        clock[0] += 20
        assert pump.tick().handles == ()
        assert transport.calls == expected
        assert read_notice_cover(db, source.document) == source
        assert read_consolidated_notice_cover(db, canonical.document) == canonical
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        tasks.close()
        db.close()


def test_dirty_request_refusal_rolls_back_fresh_source_and_does_not_retry(tmp_path, monkeypatch):
    from cifereca.core import notice_c_fetch_publication as publication
    from cifereca.core.notice_c_work_requests import SourceChangeRequests
    db = Storage(tmp_path / "rollback.sqlite")
    tasks = broker()
    try:
        source, canonical, authority, ref, transport, execution = setup(db, "changed")
        saved = []
        original = publication.request_source_worker_dependents_txn
        def refuse(sql, published, *, now):
            requests = original(sql, published, now=now)
            saved.extend(requests.refs)
            return SourceChangeRequests(requests.refs, requests.examined, 1)
        monkeypatch.setattr(publication, "request_source_worker_dependents_txn", refuse)
        pump = PublicNoticeWorkPump(db, tasks, clock=lambda: 100, fetch_execution=execution)
        handle, = pump.tick().handles
        assert handle.result(timeout=10) == "error"
        assert read_notice_cover(db, source.document) == source
        assert read_consolidated_notice_cover(db, canonical.document) == canonical
        assert len(saved) == 1
        with pytest.raises(ValueError, match="generation_changed"):
            journal.read_job(db, saved[0])
        assert journal.read_job(db, ref).state == "error"
        assert transport.calls == ["reserve", "execute", "release"]
        assert pump.tick().handles == ()
    finally:
        tasks.close()
        db.close()


@pytest.mark.parametrize("mode", ("witness_denied", "witness_allowed", "witness_changed_input"))
@pytest.mark.parametrize("configured", (False, True))
def test_fresh_parse_witness_gate_and_diagnostics(tmp_path, monkeypatch, mode, configured):
    import json
    import zipfile
    from cifereca.core.notice_parse_witness import CriticalParseField, field_digest
    from cifereca.core.notice_parse_witness_store import publish_approved_parse_witness
    from cifereca.core.notice_parse_witness_alert import read_parse_witness_diagnostic
    monkeypatch.setattr("cifereca.core.notice_regression_capture.downloads_dir", lambda: tmp_path)
    destination = tmp_path / "captures" if configured else tmp_path
    db = Storage(tmp_path / "witness-fetch.sqlite")
    tasks = broker()
    try:
        source, canonical, authority, ref, transport, execution = setup(db, mode)
        title = source.observation.fields_copy()["search_result"]["public_bibliography"]["title"]
        publish_approved_parse_witness(
            db, observation=source.observation, response_bytes=b"approved response",
            critical_fields=(CriticalParseField(
                ("search_result", "public_bibliography", "title"), "string",
                expected_sha256=field_digest(title),
            ),), expected_generation=0,
        )
        events = []
        pump = PublicNoticeWorkPump(db, tasks, clock=lambda: 100, fetch_execution=execution,
                                    on_witness_rejected=lambda *event: events.append(event),
                                    capture_directory=destination if configured else None)
        handle, = pump.tick().handles
        changed = mode == "witness_changed_input"
        assert handle.result(timeout=10) == ("current" if changed else "error")
        assert journal.read_job(db, ref).state == ("current" if changed else "error")
        projection = read_parse_witness_diagnostic(
            db, key=source.observation.key, parser_id=source.observation.parser_id,
        )
        assert projection["generation"] == 1
        if changed:
            assert projection["status"] == "source_changed"
            assert projection["capture_state"] == "not_recorded"
            assert read_notice_cover(db, source.document).epoch == source.epoch + 1
            assert not list(destination.glob("*.zip"))
            assert events == []
        else:
            assert projection["status"] == "parser_drift_suspected"
            assert projection["capture_state"] == ("complete" if mode == "witness_allowed" else "partial")
            assert events == [(projection["capture_id"], projection["capture_state"])]
            assert read_notice_cover(db, source.document) == source
            assert read_consolidated_notice_cover(db, canonical.document) == canonical
            archive_path, = destination.glob("*.zip")
            if configured:
                assert not list(tmp_path.glob("*.zip"))
            with zipfile.ZipFile(archive_path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                assert manifest["diagnostic_id"] == projection["capture_id"]
                if mode == "witness_allowed":
                    assert archive.read("response.raw") == b"approved response"
                else:
                    assert "response.raw" in manifest["omitted"]
            assert pump.tick().handles == ()
        assert transport.calls == ["reserve", "execute", "release"]
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        tasks.close()
        db.close()
