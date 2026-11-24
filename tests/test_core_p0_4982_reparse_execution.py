"""#4982 real journal/bridge/pump/source writer with a synthetic trusted parser."""
from contextlib import contextmanager
from dataclasses import replace
from threading import Event, Lock
from time import monotonic

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_cover_index import (
    persist_exact_public_search_cover, read_consolidated_notice_cover,
)
from cifereca.core.notice_c_reparse_execution import (
    PublicReplayPort, PublicReplayRegistry, ReplayInput,
)
from cifereca.core.notice_search_cover_producer import admit_public_search_catalogue
from cifereca.core.notice_cover_repository import read_notice_cover
from cifereca.core.notice_c_work_pump import PublicNoticeWorkPump
from cifereca.storage import Storage
from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_runtime import TaskBrokerImpl
from tests.test_core_p0_4982_raw_input_receipt import BODY, public, record
from tests.test_core_p0_4982_reparse_requests import TOKEN, PARSER, request
from tests.test_core_p0_6366_public_c_sqlite_source_search import verified_public


def corrected(source, **changes):
    row = verified_public(record_id="same-edition", title="Corrigé par le parseur", **changes)
    return admit_public_search_catalogue(
        row, identity_id="alice", observed_at=source.observation.observed_at,
        public_cover_url=row.thumbnail_url,
    )


class FakePort:
    def __init__(self, source, receipt):
        self.source = source
        self.lock = Lock()
        self.live = ReplayInput(
            receipt.producer_token, receipt.transport_digest,
            receipt.parser_family_digest, receipt.parser_implementation_digest, BODY,
        )
        self.calls = []
        self.next = corrected(source)
        self.revoke_during_parse = False

    def read(self, token):
        self.calls.append("read")
        with self.lock:
            return self.live

    def parse(self, request, selected):
        self.calls.append("parse")
        assert not self.lock.locked()
        assert request.source == self.source
        if self.revoke_during_parse:
            with self.lock:
                self.live = None
        return self.next

    @contextmanager
    def hold(self, token):
        self.calls.append("hold")
        with self.lock:
            yield self.live

    def registry(self):
        return PublicReplayRegistry((PublicReplayPort(
            self.source.observation.key.provider_id,
            self.live.parser_family_digest, PARSER, self.read, self.parse, self.hold,
        ),))


def make_job(db):
    source = public(db)
    canonical = persist_exact_public_search_cover(db, source)
    receipt = record(db, source, producer_token=TOKEN)
    ref = request(db, source, receipt)
    return source, canonical, ref, FakePort(source, receipt)


def broker():
    return TaskBrokerImpl(object(), max_workers=1, shared_admission=
                          SharedTaskAdmission(max_inflight=3, reserve_user_slots=1))


def run_until_current(pump, db, canonical, *, expected):
    deadline = monotonic() + 10
    outcomes = []
    while monotonic() < deadline:
        tick = pump.tick()
        outcomes.extend(h.result(timeout=5) for h in tick.handles)
        latest = read_consolidated_notice_cover(db, canonical.document)
        if latest.sources[0].public_bibliography.title == expected:
            return outcomes, latest
        Event().wait(.01)
    pytest.fail("canonical recomposition did not finish")


def test_one_existing_pump_reparse_then_recompute_no_freshness_invention(tmp_path):
    db = Storage(tmp_path / "work.sqlite")
    tasks = broker()
    try:
        source, canonical, ref, adapter = make_job(db)
        empty = PublicNoticeWorkPump(db, tasks, clock=lambda: 100)
        assert empty.tick().handles == ()
        assert journal.read_job(db, ref).attempts == 0
        events = []
        pump = PublicNoticeWorkPump(
            db, tasks, clock=lambda: 100, replay_registry=adapter.registry(),
            on_committed=lambda doc, epoch: events.append((doc, epoch)),
        )
        outcomes, final = run_until_current(pump, db, canonical, expected="Corrigé par le parseur")
        assert outcomes == ["current", "current"]
        assert adapter.calls == ["read", "parse", "hold"]
        latest = read_notice_cover(db, source.document)
        assert latest.epoch == source.epoch + 1
        assert latest.observation.observed_at == source.observation.observed_at
        assert journal.read_job(db, ref).state == "current"
        assert final.document == canonical.document and final.epoch == canonical.epoch + 1
        assert events == [(canonical.document, final.epoch)]
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        tasks.close()
        db.close()


@pytest.mark.parametrize("mode", ("revoked", "wrong_source", "new_time"))
def test_reparse_refusals_keep_source_and_c_and_never_fallback(tmp_path, mode):
    db = Storage(tmp_path / "refused.sqlite")
    tasks = broker()
    try:
        source, canonical, ref, adapter = make_job(db)
        registry = adapter.registry()
        if mode == "revoked":
            adapter.revoke_during_parse = True
        elif mode == "new_time":
            adapter.next = replace(adapter.next, observed_at=100)
        else:
            adapter.next = corrected(source, revision_id="other-edition")
        pump = PublicNoticeWorkPump(db, tasks, clock=lambda: 100, replay_registry=registry)
        handle, = pump.tick().handles
        assert handle.result(timeout=5) == "error"
        assert journal.read_job(db, ref).state == "error"
        assert read_notice_cover(db, source.document) == source
        assert read_consolidated_notice_cover(db, canonical.document) == canonical
        assert adapter.calls[:2] == ["read", "parse"]
        assert len(adapter.calls) <= 3
    finally:
        tasks.close()
        db.close()


def test_reparse_source_and_dirty_requests_roll_back_on_capacity_refusal(tmp_path, monkeypatch):
    from cifereca.core import notice_c_reparse_execution as execution
    from cifereca.core.notice_c_work_requests import SourceChangeRequests
    db = Storage(tmp_path / "rollback.sqlite")
    tasks = broker()
    try:
        source, canonical, ref, adapter = make_job(db)
        original = execution.request_source_worker_dependents_txn
        captured = []
        def refused(sql, published, *, now):
            result = original(sql, published, now=now)
            captured.extend(result.refs)
            return SourceChangeRequests(result.refs, result.examined, 1)
        monkeypatch.setattr(execution, "request_source_worker_dependents_txn", refused)
        pump = PublicNoticeWorkPump(db, tasks, clock=lambda: 100, replay_registry=adapter.registry())
        handle, = pump.tick().handles
        assert handle.result(timeout=5) == "transient_failure"
        assert read_notice_cover(db, source.document) == source
        assert read_consolidated_notice_cover(db, canonical.document) == canonical
        assert len(captured) == 1
        with pytest.raises(ValueError, match="generation_changed"):
            journal.read_job(db, captured[0])
        assert journal.read_job(db, ref).attempts == 1
    finally:
        tasks.close()
        db.close()


def test_reparse_witness_preserves_last_healthy_source_and_c(tmp_path, monkeypatch):
    monkeypatch.setattr('cifereca.core.notice_regression_capture.downloads_dir', lambda: tmp_path)
    import zipfile
    from cifereca.core.notice_parse_witness import CriticalParseField, field_digest
    from cifereca.core.notice_parse_witness_store import publish_approved_parse_witness
    db = Storage(tmp_path / "witness.sqlite")
    tasks = broker()
    try:
        source, canonical, ref, adapter = make_job(db)
        title = source.observation.fields_copy()["search_result"]["public_bibliography"]["title"]
        publish_approved_parse_witness(
            db, observation=source.observation, response_bytes=BODY,
            critical_fields=(CriticalParseField(
                ("search_result", "public_bibliography", "title"), "string",
                expected_sha256=field_digest(title),
            ),), expected_generation=0,
        )
        pump = PublicNoticeWorkPump(db, tasks, clock=lambda: 100,
                                    replay_registry=adapter.registry())
        handle, = pump.tick().handles
        assert handle.result(timeout=5) == "error"
        assert journal.read_job(db, ref).state == "error"
        assert read_notice_cover(db, source.document) == source
        assert read_consolidated_notice_cover(db, canonical.document) == canonical
        from cifereca.core.notice_parse_witness_alert import _ALERT_PREFIX
        from cifereca.core.notice_parse_witness import admitted_source_scope_sha256
        import json
        scope = admitted_source_scope_sha256(
            source.observation.key, parser_id=source.observation.parser_id,
        )
        alert = json.loads(db.metadata_get(_ALERT_PREFIX + scope))
        assert alert["status"] == "parser_drift_suspected"
        assert alert["generation"] == 1
        from cifereca.core.notice_regression_capture_intent import _PREFIX, pending_capture_ids
        diagnostic = alert["capture_id"]
        capture = json.loads(db.metadata_get(_PREFIX + diagnostic))
        assert capture["capture_state"] == "partial"
        assert pending_capture_ids(db) == ()
        archive_path, = tmp_path.glob("cifereca-regression-*.zip")
        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["diagnostic_id"] == diagnostic
            assert manifest["omitted"] == ["response.raw"]
            assert archive.read("last_healthy.json") == source.observation.raw_json.encode()
            assert archive.read("current_parse.json") == adapter.next.raw_json.encode()
            assert json.loads(archive.read("diff.json"))["status"] == "parser_drift_suspected"
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        tasks.close()
        db.close()
