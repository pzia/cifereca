"""P0 #1905: startup backup receipts persist safe per-account telemetry."""
from __future__ import annotations

from time import time

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.gui.numilog_materialization_status import describe_run
from tests.test_core_p0_numilog_auto_materialization import _authorize, _setup
from cifereca.core.numilog_materialization_startup import execute
from cifereca.plugin_api import Cancelled


def test_numilog_claim_and_completion_are_durable_and_strictly_single_use(tmp_path):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        clock = int(time())
        assert service.storage.numilog_materialization_run_status(sid)["status"] == "never"
        assert service.storage.numilog_materialization_claim_startup(source_id=sid, now=clock)
        started = service.storage.numilog_materialization_run_status(sid)
        assert started == {
            "status": "running", "started_at": clock,
            "next_eligible_at": clock + 3600,
            "attempted": 0, "downloaded": 0, "skipped": 0, "failed": 0,
        }
        assert service.storage.numilog_materialization_finish_startup(
            source_id=sid, started_at=clock, status="complete",
            attempted=2, downloaded=1, skipped=4, failed=0,
        )
        recorded = service.storage.numilog_materialization_run_status(sid)
        assert recorded["status"] == "complete"
        assert recorded["downloaded"] == 1 and recorded["attempted"] == 2
        assert recorded["skipped"] == 4
        assert not service.storage.numilog_materialization_finish_startup(
            source_id=sid, started_at=clock, status="failed",
            attempted=2, downloaded=0, skipped=0, failed=1,
        )
        assert service.storage.numilog_materialization_run_status(sid) == recorded
        assert calls == []
    finally:
        service.close()


def test_late_result_cannot_overwrite_newer_numilog_startup(tmp_path):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        first = int(time())
        assert service.storage.numilog_materialization_claim_startup(source_id=sid, now=first)
        assert service.storage.numilog_materialization_claim_startup(
            source_id=sid, now=first + 3600,
        )
        assert not service.storage.numilog_materialization_finish_startup(
            source_id=sid, started_at=first, status="complete",
            attempted=1, downloaded=1, skipped=0, failed=0,
        )
        assert service.storage.numilog_materialization_run_status(sid)["status"] == "running"
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize(("field", "value"), [
    ("status", "https://private.invalid/?auth=SIGNED_SECRET"),
    ("downloaded", True),
    ("skipped", 2049),
    ("attempted", -1),
    ("started_at", "not-a-date"),
])
def test_corrupt_numilog_result_metadata_is_never_rendered(tmp_path, field, value):
    import json
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        clock = int(time())
        assert service.storage.numilog_materialization_claim_startup(source_id=sid, now=clock)
        key = "sync.source.numilog.materialization.result.v1." + sid
        original = json.loads(service.storage.metadata_get(key))
        original[field] = value
        service.storage.metadata_set(key, json.dumps(original))
        facts = broker.numilog_materialization_run_status(sid)
        assert facts["status"] == "unknown"
        assert "SIGNED_SECRET" not in repr(facts)
        assert "SIGNED_SECRET" not in describe_run("on_start", facts)
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize("bad", [
    {"status": "running", "started_at": None, "next_eligible_at": 2000000000,
     "attempted": 0, "downloaded": 0, "skipped": 0, "failed": 0},
    {"status": "complete", "started_at": 2000000000, "next_eligible_at": 2000003599,
     "attempted": 1, "downloaded": 1, "skipped": 0, "failed": 0},
    {"status": "partial", "started_at": 2000000000, "next_eligible_at": 2000003600,
     "attempted": 0, "downloaded": 1, "skipped": 0, "failed": 0},
])
def test_gui_never_displays_malformed_run_summary(bad):
    from cifereca.gui.numilog_materialization_status import describe_run
    assert describe_run("on_start", bad, now=2000000500) == "Dernier bilan indisponible."


def test_numilog_status_display_is_scoped_and_time_window_is_honest():
    clock = 2_000_000_000
    summary = {
        "status": "partial", "started_at": clock, "next_eligible_at": clock + 3600,
        "attempted": 8, "downloaded": 5, "skipped": 12, "failed": 1,
    }
    early = describe_run("on_start", summary, now=clock + 30)
    assert "Dernier essai partiel" in early
    assert "5 téléchargé(s)" in early
    assert "8 tenté(s)" in early and "12 ignoré(s)" in early
    assert "pas avant" in early and "prochaine ouverture" in early
    late = describe_run("on_start", summary, now=clock + 3600)
    assert "pas avant" not in late and "prochaine ouverture" in late
    manual = describe_run("manual", summary, now=clock + 3600)
    assert "Prochain essai" not in manual
    assert "auth" not in manual


@pytest.mark.parametrize("outcome", ["complete", "failed", "blocked"])
def test_worker_persists_real_result_without_private_provider_data(tmp_path, outcome):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        if outcome == "complete":
            session.library = lambda: []
            result = execute(broker, account.identity_id, "startup")
            assert result.ok
        elif outcome == "failed":
            session.library = lambda: (_ for _ in ()).throw(
                RuntimeError("https://private.invalid/fulfillment?auth=SIGNED_SECRET")
            )
            result = execute(broker, account.identity_id, "startup")
            assert not result.ok
        else:
            def revoked():
                broker.revoke_numilog_materialization_consent(sid)
                raise Cancelled("cancelled")
            session.library = revoked
            with pytest.raises(Cancelled):
                execute(broker, account.identity_id, "startup")
        facts = service.storage.numilog_materialization_run_status(sid)
        assert facts["status"] == outcome
        assert facts["attempted"] == facts["downloaded"] == 0
        assert "SIGNED_SECRET" not in repr(facts)
        assert calls == []
    finally:
        service.close()


def test_headless_sync_page_shows_persisted_numilog_result_without_network():
    from tests.test_core_p0_1905_numilog_consent_ui import _ui
    w, broker, calls, dialogs, state, row = _ui(initial="on_start")
    broker.numilog_materialization_run_status = lambda sid: {
        "status": "partial", "started_at": 2_000_000_000,
        "next_eligible_at": 2_000_003_600,
        "attempted": 5, "downloaded": 3, "skipped": 2, "failed": 1,
    }
    w._refresh_sync_page()
    assert "Dernier essai partiel" in row().subtitle
    assert "3 téléchargé(s)" in row().subtitle
    assert len(dialogs) == 0
    assert not w.runner.tasks
    assert not any(name.startswith("materialization-prepare") for name, *_ in calls)
